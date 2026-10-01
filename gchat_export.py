#!/usr/bin/env python3
"""Download Google Chat messages into a local SQLite store, plus readable Markdown.

It only downloads. It does not summarize or interpret anything: another program
(an MCP server, a skill, a scheduled job) calls it and then reads what it left.

Typical use:
    python -u gchat_export.py --list-spaces            # what is visible, and as whom
    python -u gchat_export.py                          # catch up since the last run
    python -u gchat_export.py --month 2026-08          # backfill a whole month
    python -u gchat_export.py --non-interactive --json # from another program

Output, under the data dir (--data-dir, $GCHAT_DATA_DIR, or ~/gchat-export):
    gchat.db                            SQLite store, the source of truth
    md/2026-08/La-interna-b451c5.md     one readable file per space and month

A consumer picks up new and changed messages with:
    SELECT * FROM v_messages WHERE seq > :last_seen ORDER BY seq

Exit codes: 0 ok, 1 error, 2 partial (some spaces not accessible),
            3 authorization required (only with --non-interactive).

Needs credentials.json (OAuth client, Desktop type) next to this script. The
token is kept in token.json; the account comes from it, not from a parameter.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    # The Chat API never returns people's names, neither in sender.displayName
    # nor in memberships: only users/NNN ids. Names come from the Workspace
    # directory through the People API.
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/directory.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
HERE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(HERE, "credentials.json")
TOKEN = os.path.join(HERE, "token.json")
DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), "gchat-export")
DB_NAME = "gchat.db"
SCHEMA_VERSION = 1
RETRIES = 3  # googleapiclient retries 429 and 5xx with exponential backoff

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL, EXIT_AUTH = 0, 1, 2, 3


# ---------------------------------------------------------------- auth

class AuthRequired(Exception):
    """Raised instead of opening a browser when running non-interactively."""


def get_creds(reauth=False, interactive=True, log=print):
    if reauth and os.path.exists(TOKEN):
        os.remove(TOKEN)
        log("[i] token.json removed; authorization required.")

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            log(f"[!] Token refresh failed ({e}).")
            creds = None
    if not creds or not creds.valid:
        # A caller running unattended must never hang waiting for a browser.
        if not interactive:
            raise AuthRequired("authorization required: run once without --non-interactive")
        if not os.path.exists(CREDS):
            raise SystemExit(f"[X] Missing {CREDS}. See SETUP.md, step 4.")
        log("[i] Open the URL below with the Workspace account (not a personal Gmail).")
        flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
        creds = flow.run_local_server(port=0)
    with open(TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return creds


def whoami(creds):
    """Email of the authorized account, so the wrong account is noticed early."""
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return svc.userinfo().get().execute(num_retries=RETRIES).get("email") or "?"
    except Exception:
        return "?"


# ---------------------------------------------------------------- Chat API

def _pages(make_request):
    token = None
    while True:
        resp = make_request(token).execute(num_retries=RETRIES)
        yield resp
        token = resp.get("nextPageToken")
        if not token:
            return


def list_spaces(svc):
    spaces = []
    for resp in _pages(lambda t: svc.spaces().list(pageSize=1000, pageToken=t)):
        spaces.extend(resp.get("spaces", []))
    return spaces


def list_members(svc, space_name):
    """Human member ids of a space. Chat never returns their names."""
    ids = set()
    for resp in _pages(lambda t: svc.spaces().members().list(
            parent=space_name, pageSize=1000, pageToken=t)):
        for ms in resp.get("memberships", []):
            user = ms.get("member") or {}
            if user.get("name") and user.get("type") == "HUMAN":
                ids.add(user["name"])
    return sorted(ids)


def list_messages(svc, space_name, since, until):
    """Messages created in [since, until), both dates in UTC."""
    # The filter only supports strict < and >, so start a hair before midnight.
    start = dt.datetime.combine(since, dt.time()) - dt.timedelta(microseconds=1)
    flt = (f'createTime > "{start:%Y-%m-%dT%H:%M:%S.%f}Z" '
           f'AND createTime < "{until.isoformat()}T00:00:00Z"')
    msgs = []
    for resp in _pages(lambda t: svc.spaces().messages().list(
            parent=space_name, filter=flt, pageSize=1000, pageToken=t)):
        msgs.extend(resp.get("messages", []))
    return msgs


def people_lookup(creds, log=print):
    """Returns lookup(user_ids) -> {user_id: (name, email)} backed by People API."""
    try:
        svc = build("people", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        log(f"[!] People API unavailable ({e}); people will show as users/NNN.")
        return lambda ids: {}

    def lookup(user_ids):
        found = {}
        for i in range(0, len(user_ids), 50):  # getBatchGet takes up to 50
            batch = user_ids[i:i + 50]
            try:
                resp = svc.people().getBatchGet(
                    resourceNames=[u.replace("users/", "people/") for u in batch],
                    personFields="names,emailAddresses",
                ).execute(num_retries=RETRIES)
            except HttpError as e:
                log(f"[!] People API failed: HTTP {e.resp.status}")
                continue
            for r in resp.get("responses", []):
                person = r.get("person") or {}
                rn = person.get("resourceName") or r.get("requestedResourceName") or ""
                email = next((e.get("value", "").strip()
                              for e in person.get("emailAddresses") or [] if e.get("value")), "")
                name = next((n.get("displayName", "").strip()
                             for n in person.get("names") or [] if n.get("displayName")), "")
                name = name or email.split("@")[0]
                if rn and name:
                    found[rn.replace("people/", "users/")] = (name, email)
        return found

    return lookup


# ---------------------------------------------------------------- dates

def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()


def month_start(d):
    return d.replace(day=1)


def next_month(d):
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def merge_intervals(intervals):
    """Sorted, non-overlapping [since, until) date intervals; touching ones join."""
    merged = []
    for a, b in sorted(i for i in intervals if i[0] < i[1]):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def gaps(window, covered):
    """Parts of window [since, until) not inside any covered interval."""
    a, b = window
    out = []
    for c0, c1 in merge_intervals(covered):
        if c1 <= a or c0 >= b:
            continue
        if c0 > a:
            out.append((a, c0))
        a = max(a, c1)
        if a >= b:
            break
    if a < b:
        out.append((a, b))
    return out


# ---------------------------------------------------------------- store

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS spaces (
    name TEXT PRIMARY KEY,
    title TEXT,
    type TEXT,
    uri TEXT,
    last_active_time TEXT,
    membership_count TEXT,   -- JSON; a change triggers a members refresh
    members TEXT,            -- JSON list of users/NNN
    raw TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS people (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    email TEXT,
    resolved_at TEXT
);

-- Days fully downloaded per space, as [since, until) date intervals.
CREATE TABLE IF NOT EXISTS coverage (
    space TEXT NOT NULL,
    since TEXT NOT NULL,
    until TEXT NOT NULL,
    PRIMARY KEY (space, since)
);

CREATE TABLE IF NOT EXISTS messages (
    name TEXT PRIMARY KEY,   -- spaces/X/messages/Y, stable across edits
    space TEXT NOT NULL,
    thread TEXT,
    sender_id TEXT,
    create_time TEXT NOT NULL,
    update_time TEXT,
    text TEXT,
    attachments TEXT,        -- JSON list of file names
    quote_type TEXT,         -- FORWARD or REPLY when quoting another message
    quoted_sender TEXT,
    quoted_text TEXT,        -- a forward's real content is here, not in text
    raw TEXT NOT NULL,       -- the API message, minus expiring URLs
    deleted INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL,    -- grows on every insert, edit or deletion
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_seq ON messages (seq);
CREATE INDEX IF NOT EXISTS messages_space_time ON messages (space, create_time);
CREATE INDEX IF NOT EXISTS messages_thread ON messages (thread);

CREATE VIEW IF NOT EXISTS v_messages AS
SELECT m.seq, m.name, m.space, s.title AS space_title, s.type AS space_type,
       m.thread, m.sender_id, COALESCE(p.name, m.sender_id) AS sender_name,
       p.email AS sender_email, m.create_time, m.update_time, m.text,
       m.attachments, m.quote_type, m.quoted_sender, m.quoted_text, m.deleted
FROM messages m
LEFT JOIN spaces s ON s.name = m.space
LEFT JOIN people p ON p.user_id = m.sender_id;
"""

UPSERT_MESSAGE = """
INSERT INTO messages (name, space, thread, sender_id, create_time, update_time,
                      text, attachments, quote_type, quoted_sender, quoted_text,
                      raw, deleted, seq, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
ON CONFLICT (name) DO UPDATE SET
    space = excluded.space, thread = excluded.thread,
    sender_id = excluded.sender_id, create_time = excluded.create_time,
    update_time = excluded.update_time, text = excluded.text,
    attachments = excluded.attachments, quote_type = excluded.quote_type,
    quoted_sender = excluded.quoted_sender, quoted_text = excluded.quoted_text,
    raw = excluded.raw, deleted = 0,
    seq = excluded.seq, fetched_at = excluded.fetched_at
"""

UPSERT_SPACE = """
INSERT INTO spaces (name, title, type, uri, last_active_time, membership_count,
                    members, raw, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (name) DO UPDATE SET
    title = excluded.title, type = excluded.type, uri = excluded.uri,
    last_active_time = excluded.last_active_time,
    membership_count = excluded.membership_count, members = excluded.members,
    raw = excluded.raw, updated_at = excluded.updated_at
"""


def open_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL lets a consumer read while a download is writing.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    elif int(row["value"]) > SCHEMA_VERSION:
        conn.close()
        raise SystemExit(f"[X] {path} has schema v{row['value']}; "
                         f"this script only knows v{SCHEMA_VERSION}.")
    return conn


@contextlib.contextmanager
def write_tx(conn):
    """IMMEDIATE takes the write lock up front, so seq stays unique even if two
    downloads run at once."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def max_seq(conn):
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM messages").fetchone()[0]


def get_coverage(conn, space):
    return [(dt.date.fromisoformat(r["since"]), dt.date.fromisoformat(r["until"]))
            for r in conn.execute("SELECT since, until FROM coverage WHERE space = ?", (space,))]


def add_coverage(conn, space, since, until):
    """Must run inside write_tx."""
    if since >= until:
        return
    merged = merge_intervals(get_coverage(conn, space) + [(since, until)])
    conn.execute("DELETE FROM coverage WHERE space = ?", (space,))
    conn.executemany("INSERT INTO coverage VALUES (?, ?, ?)",
                     [(space, a.isoformat(), b.isoformat()) for a, b in merged])


VOLATILE_KEYS = {"downloadUri", "thumbnailUri"}


def stable(value):
    """The message without fields that change on every fetch, at any depth.

    Attachment download and thumbnail URLs carry a fresh access token each time,
    both on the message and inside a quoted or forwarded one. Kept, they would make
    those messages look edited on every run, and a consumer would reprocess them
    for nothing. They also expire; attachmentDataRef is the stable reference.
    """
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def quote_of(m):
    """(type, sender, text) of the message this one quotes or forwards.

    A forward often has no text of its own: its whole content lives here.
    """
    q = m.get("quotedMessageMetadata") or {}
    if not q:
        return None, None, None
    snap = q.get("quotedMessageSnapshot") or {}
    sender = snap.get("sender")
    if isinstance(sender, dict):
        sender = sender.get("displayName") or sender.get("name")
    text = (snap.get("text") or snap.get("formattedText") or "").strip()
    files = " ".join(f"[attachment: {a.get('contentName') or 'file'}]"
                     for a in snap.get("attachments") or [])
    text = " ".join(t for t in (text, files) if t)
    return q.get("quoteType"), sender or None, text or None


def store_window(conn, space, msgs, since, until, covered_until, now_iso):
    """Upsert one fetched window of a space and record it as covered.

    Messages that come back identical keep their seq. Stored messages of this
    window that the API no longer returns were deleted in Chat: they are flagged
    rather than removed, with a new seq so a consumer notices.

    Returns (changed, deleted, touched months, sender ids).
    """
    changed = deleted = 0
    months, senders = set(), set()
    with write_tx(conn):
        seq = max_seq(conn)
        # Compare by date prefix: as text, "00:00:00.1Z" sorts before "00:00:00Z".
        stored = {r["name"]: r for r in conn.execute(
            "SELECT name, raw, deleted, create_time FROM messages "
            "WHERE space = ? AND substr(create_time, 1, 10) >= ? "
            "AND substr(create_time, 1, 10) < ?",
            (space, since.isoformat(), until.isoformat()))}
        seen = set()
        for m in map(stable, msgs):
            name = m.get("name")
            if not name:
                continue
            seen.add(name)
            sender = (m.get("sender") or {}).get("name")
            if sender:
                senders.add(sender)
            raw = json.dumps(m, ensure_ascii=False, sort_keys=True)
            old = stored.get(name)
            if old is not None and old["raw"] == raw and not old["deleted"]:
                continue
            seq += 1
            attachments = [a.get("contentName") or "attachment" for a in m.get("attachment") or []]
            conn.execute(UPSERT_MESSAGE, (
                name, space, (m.get("thread") or {}).get("name"), sender,
                m.get("createTime") or "", m.get("lastUpdateTime"),
                m.get("text") or m.get("formattedText") or "",
                json.dumps(attachments, ensure_ascii=False), *quote_of(m), raw, seq, now_iso))
            changed += 1
            months.add((m.get("createTime") or "")[:7])
        for name, old in stored.items():
            if name not in seen and not old["deleted"]:
                seq += 1
                conn.execute("UPDATE messages SET deleted = 1, seq = ?, fetched_at = ? "
                             "WHERE name = ?", (seq, now_iso, name))
                deleted += 1
                months.add(old["create_time"][:7])
        add_coverage(conn, space, since, covered_until)
    return changed, deleted, months, senders


# ---------------------------------------------------------------- people

def resolve_people(conn, lookup, user_ids, now_iso, log=print):
    """Look up the ids not seen before. Unresolved ones are retried next run."""
    known = {r[0] for r in conn.execute("SELECT user_id FROM people")}
    missing = sorted(u for u in user_ids if u and u not in known)
    if not missing:
        return
    log(f"[i] Resolving {len(missing)} new people...")
    found = lookup(missing)
    with write_tx(conn):
        conn.executemany("INSERT OR REPLACE INTO people VALUES (?, ?, ?, ?)",
                         [(u, name, email, now_iso) for u, (name, email) in found.items()])
    if len(found) < len(missing):
        log(f"[!] {len(missing) - len(found)} people could not be resolved; "
            f"they show as users/NNN.")


def people_names(conn):
    return {r["user_id"]: r["name"] for r in conn.execute("SELECT user_id, name FROM people")}


def my_user_id(conn, my_email, members_by_space):
    """Which users/NNN is the account running this. Without it, every DM would
    be named after its owner instead of the other person.

    By email it is exact (people/me needs the 'profile' scope, which we do not
    ask for). Fallback: the only member present in every space.
    """
    if my_email:
        row = conn.execute("SELECT user_id FROM people WHERE lower(email) = lower(?)",
                           (my_email,)).fetchone()
        if row:
            return row[0]
    sets = [set(ids) for ids in members_by_space.values() if ids]
    if not sets:
        return None
    common = set.intersection(*sets)
    return next(iter(common)) if len(common) == 1 else None


# ---------------------------------------------------------------- names

def space_type(sp):
    return sp.get("spaceType") or sp.get("type") or "?"


def space_title(sp, member_ids, names, me_id):
    title = (sp.get("displayName") or "").strip()
    if title:
        return title
    kind = space_type(sp)
    my_name = names.get(me_id)
    # DMs and unnamed groups have no displayName: name them after the others.
    others = sorted(names[u] for u in member_ids
                    if u != me_id and names.get(u) and names[u] != my_name)
    if kind == "DIRECT_MESSAGE" and len(others) == 1:
        return others[0]
    if kind == "GROUP_CHAT" and others:
        return ", ".join(others[:3]) + (f" +{len(others) - 3}" if len(others) > 3 else "")
    return {"DIRECT_MESSAGE": "Unnamed DM", "GROUP_CHAT": "Unnamed group"}.get(kind, "Unnamed space")


def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text.strip())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:60] or "space"


def space_filename(space_name, title):
    """Readable name plus a suffix from the space id: two spaces never collide,
    and the suffix survives renames."""
    suffix = hashlib.sha1(space_name.encode("utf-8")).hexdigest()[:6]
    return f"{slugify(title)}-{suffix}.md"


# ---------------------------------------------------------------- markdown

def sender_name(m, names):
    s = m.get("sender") or {}
    return (s.get("displayName") or "").strip() or names.get(s.get("name")) or s.get("name") or "?"


def body_of(m):
    text = (m.get("text") or m.get("formattedText") or "").strip()
    bits = [text] if text else []
    for att in m.get("attachment") or []:
        bits.append(f"[attachment: {att.get('contentName') or 'file'}]")
    qtype, qsender, qtext = quote_of(m)
    if qtext:
        who = f" from {qsender}" if qsender else ""
        if qtype == "FORWARD":
            bits.append(f"[forwarded{who}: {qtext}]")
        else:  # a reply quoting context: enough to know what it answers
            bits.append(f"[quoting{who}: {qtext if len(qtext) <= 120 else qtext[:117] + '...'}]")
    if not bits and m.get("cardsV2"):
        bits.append("(card / app message)")
    return " ".join(bits) or "(no text)"


def indent(text, pad):
    return text.replace("\n", "\n" + pad)


def render_markdown(space_name, title, kind, month, msgs, names, thread_start, now_iso):
    """One space, one month. Grouped by day, then by thread."""
    lines = [
        f"<!-- gchat-export space={space_name} month={month} messages={len(msgs)} "
        f"generated={now_iso} -->",
        f"# {title}",
        "",
        f"{kind} - {len(msgs)} messages - {month} - times in UTC",
        "",
    ]
    by_day = defaultdict(list)
    for m in msgs:
        by_day[m["createTime"][:10]].append(m)

    for day in sorted(by_day):
        lines += [f"## {day}", ""]
        threads, order = defaultdict(list), []
        for m in by_day[day]:
            tid = (m.get("thread") or {}).get("name") or m.get("name")
            if tid not in threads:
                order.append(tid)
            threads[tid].append(m)
        for tid in order:
            head, rest = threads[tid][0], threads[tid][1:]
            # The thread started earlier: on a previous day, or a previous month.
            started = thread_start.get(tid)
            cont = " (continues an earlier thread)" if started and started < head["createTime"] else ""
            lines.append(f"**{head['createTime'][11:16]} {sender_name(head, names)}**:{cont} "
                         f"{indent(body_of(head), '  ')}")
            for m in rest:
                lines.append(f"  - **{m['createTime'][11:16]} {sender_name(m, names)}**: "
                             f"{indent(body_of(m), '    ')}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_atomic(path, content):
    """Temp file + rename: an interrupted run never leaves a truncated file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def drop_renamed(mdir, path):
    """Remove an older file of the same space saved under a previous title."""
    base = os.path.basename(path)
    m = re.match(r"^.*-([0-9a-f]{6})\.md$", base)
    if not m or not os.path.isdir(mdir):
        return
    for f in os.listdir(mdir):
        if f != base and f.endswith(f"-{m.group(1)}.md"):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(mdir, f))


def write_markdown(conn, md_dir, space_name, title, kind, month, names, now_iso):
    """(Re)generate one space/month file from the store. Returns (path, count)."""
    mdir = os.path.join(md_dir, month)
    path = os.path.join(mdir, space_filename(space_name, title))
    msgs = [json.loads(r[0]) for r in conn.execute(
        "SELECT raw FROM messages WHERE space = ? AND deleted = 0 "
        "AND substr(create_time, 1, 7) = ? ORDER BY create_time", (space_name, month))]
    if not msgs:
        drop_renamed(mdir, path)
        with contextlib.suppress(OSError):
            os.remove(path)
        return path, 0
    thread_start = {r[0]: r[1] for r in conn.execute(
        "SELECT thread, MIN(create_time) FROM messages WHERE space = ? AND deleted = 0 "
        "AND thread IS NOT NULL GROUP BY thread", (space_name,))}
    os.makedirs(mdir, exist_ok=True)
    write_atomic(path, render_markdown(space_name, title, kind, month, msgs, names,
                                       thread_start, now_iso))
    drop_renamed(mdir, path)
    return path, len(msgs)


# ---------------------------------------------------------------- sync

def load_spaces(conn, svc, lookup, account, now_iso, log=print):
    """List spaces and refresh what is needed to name them.

    Members are fetched only for new spaces or when a space's membership count
    changed; otherwise the stored list is reused. That keeps a daily run from
    making ~100 calls to rediscover the same people.

    Returns (spaces, titles, renamed space names, members refreshed).
    """
    spaces = list_spaces(svc)
    stored = {r["name"]: r for r in conn.execute(
        "SELECT name, title, membership_count, members FROM spaces")}
    members, counts, refreshed = {}, {}, 0
    for sp in spaces:
        name = sp["name"]
        count = json.dumps(sp.get("membershipCount") or {}, sort_keys=True)
        old = stored.get(name)
        if old is not None and old["members"] is not None and old["membership_count"] == count:
            members[name], counts[name] = json.loads(old["members"]), count
            continue
        try:
            members[name], counts[name] = list_members(svc, name), count
            refreshed += 1
        except HttpError:
            # Keep what we had; leave the count stale so it is retried next run.
            members[name] = json.loads(old["members"]) if old is not None and old["members"] else []
            counts[name] = old["membership_count"] if old is not None else None

    resolve_people(conn, lookup, {u for ids in members.values() for u in ids}, now_iso, log)
    names = people_names(conn)
    me_id = my_user_id(conn, account, members)
    titles = {sp["name"]: space_title(sp, members[sp["name"]], names, me_id) for sp in spaces}
    renamed = {n for n, t in titles.items()
               if n in stored and stored[n]["title"] and stored[n]["title"] != t}

    with write_tx(conn):
        conn.executemany(UPSERT_SPACE, [
            (sp["name"], titles[sp["name"]], space_type(sp), sp.get("spaceUri"),
             sp.get("lastActiveTime"), counts[sp["name"]],
             json.dumps(members[sp["name"]]), json.dumps(sp, ensure_ascii=False, sort_keys=True),
             now_iso)
            for sp in spaces])
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('account', ?)", (account,))
    return spaces, titles, renamed, refreshed


def sync(conn, svc, lookup, *, account, window, md_dir, force=False, only=(), exclude=(),
         today=None, log=print):
    """Bring the store up to date and regenerate the Markdown that changed.

    window=None is the incremental mode: each space resumes from the end of its
    own coverage (or the start of the current month if it was never synced).
    With a window, only the parts of it not yet covered are fetched, unless
    force is set.
    """
    today = today or utc_today()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seq_before = max_seq(conn)

    spaces, titles, renamed, refreshed = load_spaces(conn, svc, lookup, account, now_iso, log=log)

    def keep(title):
        title = title.lower()
        if only and not any(p.lower() in title for p in only):
            return False
        return not any(p.lower() in title for p in exclude)

    kept = [sp for sp in spaces if keep(titles[sp["name"]])]
    if window is None:
        log(f"[i] {len(kept)} of {len(spaces)} spaces, incremental since each one's last sync")
    else:
        log(f"[i] {len(kept)} of {len(spaces)} spaces, window {window[0]} -> {window[1]}")

    stats = {"total": len(spaces), "selected": len(kept), "fetched": 0,
             "skipped_inactive": 0, "skipped_covered": 0, "members_refreshed": refreshed}
    changed_total = deleted_total = 0
    no_access, senders = [], set()
    touched = defaultdict(set)

    for sp in kept:
        name, title = sp["name"], titles[sp["name"]]
        covered = get_coverage(conn, name)
        if window is None:
            start = max((b for _, b in covered), default=month_start(today))
            win = (min(start, today), today + dt.timedelta(days=1))
        else:
            win = window
        todo = [win] if force else gaps(win, covered)
        if not todo:
            stats["skipped_covered"] += 1
            continue

        last_active = (sp.get("lastActiveTime") or "")[:10]
        fetched = changed = deleted = 0
        failed = False
        for a, b in todo:
            # Today is never final: leaving it uncovered makes the next run
            # fetch it again whole, so a second run on the same day still sees
            # the afternoon's messages.
            done_until = min(b, today)
            if not force and last_active and last_active < a.isoformat():
                # Nothing happened in this space since `a`: no call needed.
                with write_tx(conn):
                    add_coverage(conn, name, a, done_until)
                continue
            try:
                msgs = list_messages(svc, name, a, b)
            except HttpError as e:
                no_access.append({"space": title, "error": f"HTTP {e.resp.status}"})
                failed = True
                break
            fetched += 1
            c, d, months, who = store_window(conn, name, msgs, a, b, done_until, now_iso)
            changed, deleted = changed + c, deleted + d
            touched[name] |= months
            senders |= who

        if fetched:
            stats["fetched"] += 1
        elif not failed:
            stats["skipped_inactive"] += 1
        changed_total += changed
        deleted_total += deleted
        if changed or deleted:
            extra = f"  ({deleted} deleted)" if deleted else ""
            log(f"    {changed:+6d}  {title}{extra}")

    # Senders who left a space are not among its members: resolve them too.
    resolve_people(conn, lookup, senders, now_iso, log)

    # A renamed space (e.g. a DM whose person just got resolved) gets all its
    # files rewritten under the new name.
    for name in renamed & {sp["name"] for sp in kept}:
        touched[name] |= {r[0] for r in conn.execute(
            "SELECT DISTINCT substr(create_time, 1, 7) FROM messages "
            "WHERE space = ? AND deleted = 0", (name,))}

    names = people_names(conn)
    kinds = {sp["name"]: space_type(sp) for sp in spaces}
    files = []
    for name in sorted(touched):
        for month in sorted(m for m in touched[name] if m):
            path, count = write_markdown(conn, md_dir, name, titles[name], kinds[name],
                                         month, names, now_iso)
            files.append({"path": path, "space": titles[name], "month": month,
                          "messages": count})

    return {
        "seq": {"before": seq_before, "after": max_seq(conn)},
        "messages_changed": changed_total,
        "messages_deleted": deleted_total,
        "spaces": stats,
        "files": files,
        "no_access": no_access,
    }


# ---------------------------------------------------------------- cli

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Download Google Chat messages into SQLite plus readable Markdown.")
    ap.add_argument("--days", type=int, help="last N days")
    ap.add_argument("--since", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--until", help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--month", help="YYYY-MM: a whole month")
    ap.add_argument("--only", action="append", default=[],
                    help="only spaces whose name contains this (repeatable)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="skip spaces whose name contains this (repeatable)")
    ap.add_argument("--force", action="store_true",
                    help="re-download the window even if already covered or inactive")
    ap.add_argument("--update", action="store_true",
                    help="incremental download; the default, kept for compatibility")
    ap.add_argument("--list-spaces", action="store_true", help="list spaces and exit")
    ap.add_argument("--reauth", action="store_true", help="delete the token and authorize again")
    ap.add_argument("--non-interactive", action="store_true",
                    help=f"never open a browser; exit {EXIT_AUTH} if authorization is needed")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable summary on stdout; logs go to stderr")
    ap.add_argument("--data-dir",
                    help=f"where the store and Markdown live "
                         f"(default: $GCHAT_DATA_DIR or {DEFAULT_DATA_DIR})")
    return ap.parse_args(argv)


def resolve_window(args, today):
    """(since, until) for an explicit range, or None for incremental mode."""
    if args.month:
        start = dt.date.fromisoformat(args.month + "-01")
        return start, next_month(start)
    if args.days:
        return today - dt.timedelta(days=args.days), today + dt.timedelta(days=1)
    if args.since or args.until:
        since = dt.date.fromisoformat(args.since) if args.since else month_start(today)
        until = dt.date.fromisoformat(args.until) if args.until else today + dt.timedelta(days=1)
        return since, until
    if args.force:
        return month_start(today), today + dt.timedelta(days=1)
    return None


def main(argv=None):
    args = parse_args(argv)
    # With --json, stdout is reserved for the JSON document.
    log = (lambda *a: print(*a, file=sys.stderr)) if args.json else print
    today = utc_today()

    # Validate before touching the network or the browser.
    try:
        window = resolve_window(args, today)
    except ValueError as e:
        log(f"[X] Invalid date: {e}")
        return EXIT_ERROR
    if window and window[1] <= window[0]:
        log(f"[X] Empty range: {window[0]} -> {window[1]}")
        return EXIT_ERROR

    data_dir = os.path.abspath(args.data_dir or os.environ.get("GCHAT_DATA_DIR")
                               or DEFAULT_DATA_DIR)
    db_path = os.path.join(data_dir, DB_NAME)
    md_dir = os.path.join(data_dir, "md")

    try:
        creds = get_creds(reauth=args.reauth, interactive=not args.non_interactive, log=log)
    except AuthRequired as e:
        log(f"[X] {e}")
        if args.json:
            print(json.dumps({"error": "auth_required", "message": str(e)}))
        return EXIT_AUTH

    account = whoami(creds)
    log(f"[i] Account: {account}")
    chat = build("chat", "v1", credentials=creds, cache_discovery=False)
    lookup = people_lookup(creds, log)
    conn = open_db(db_path)
    try:
        if args.list_spaces:
            now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            spaces, titles, _, _ = load_spaces(conn, chat, lookup, account, now_iso, log=log)
            rows = sorted(({"name": sp["name"], "title": titles[sp["name"]],
                            "type": space_type(sp), "last_active": sp.get("lastActiveTime"),
                            "file": space_filename(sp["name"], titles[sp["name"]])}
                           for sp in spaces), key=lambda r: r["title"].lower())
            if args.json:
                print(json.dumps({"account": account, "spaces": rows}, ensure_ascii=False, indent=2))
            else:
                log(f"[i] {len(rows)} spaces:\n")
                for r in rows:
                    log(f"    {r['title']:<45} {r['type']:<16} {(r['last_active'] or '')[:10]}")
            return EXIT_OK

        report = sync(conn, chat, lookup, account=account, window=window, md_dir=md_dir,
                      force=args.force, only=args.only, exclude=args.exclude,
                      today=today, log=log)
    finally:
        conn.close()

    code = EXIT_PARTIAL if report["no_access"] else EXIT_OK
    if args.json:
        print(json.dumps({
            "account": account,
            "data_dir": data_dir,
            "db": db_path,
            "md_dir": md_dir,
            "mode": "incremental" if window is None else "window",
            "window": None if window is None else
                      {"since": window[0].isoformat(), "until": window[1].isoformat()},
            **report,
        }, ensure_ascii=False, indent=2))
    else:
        s, seq = report["spaces"], report["seq"]
        log(f"\n[OK] {report['messages_changed']} messages new or changed, "
            f"{report['messages_deleted']} deleted (seq {seq['before']} -> {seq['after']})")
        log(f"     spaces: {s['fetched']} fetched, {s['skipped_inactive']} inactive, "
            f"{s['skipped_covered']} already covered")
        log(f"     {len(report['files'])} Markdown files updated -> {md_dir}")
        log(f"     store: {db_path}")
        if report["no_access"]:
            log(f"[!] No access to {len(report['no_access'])} spaces:")
            for e in report["no_access"]:
                log(f"      {e['error']}  {e['space']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
