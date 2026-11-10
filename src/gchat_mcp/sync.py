"""Bring the store up to date from the Chat API: spaces, members, people, messages."""
import datetime as dt
import json

from googleapiclient.errors import HttpError

from .google_api import list_members, list_messages, list_spaces
from .store import (UPSERT_MESSAGE, UPSERT_SPACE, add_coverage, gaps, get_coverage,
                    max_seq, write_tx)

def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()


def month_start(d):
    return d.replace(day=1)


def next_month(d):
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


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

    Returns (changed, deleted, sender ids).
    """
    changed = deleted = 0
    senders = set()
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
        for name, old in stored.items():
            if name not in seen and not old["deleted"]:
                seq += 1
                conn.execute("UPDATE messages SET deleted = 1, seq = ?, fetched_at = ? "
                             "WHERE name = ?", (seq, now_iso, name))
                deleted += 1
        add_coverage(conn, space, since, covered_until)
    return changed, deleted, senders


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


def load_spaces(conn, svc, lookup, account, now_iso, log=print):
    """List spaces and refresh what is needed to name them.

    Members are fetched only for new spaces or when a space's membership count
    changed; otherwise the stored list is reused. That keeps a daily run from
    making ~100 calls to rediscover the same people.

    Returns (spaces, titles, members refreshed).
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
    with write_tx(conn):
        conn.executemany(UPSERT_SPACE, [
            (sp["name"], titles[sp["name"]], space_type(sp), sp.get("spaceUri"),
             sp.get("lastActiveTime"), counts[sp["name"]],
             json.dumps(members[sp["name"]]), json.dumps(sp, ensure_ascii=False, sort_keys=True),
             now_iso)
            for sp in spaces])
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('account', ?)", (account,))
    return spaces, titles, refreshed


def sync(conn, svc, lookup, *, account, window, force=False, only=(), exclude=(),
         today=None, log=print):
    """Bring the store up to date; a full incremental run records meta.last_sync.

    window=None is the incremental mode: each space resumes from the end of its
    own coverage (or the start of the current month if it was never synced).
    With a window, only the parts of it not yet covered are fetched, unless
    force is set.
    """
    today = today or utc_today()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seq_before = max_seq(conn)

    spaces, titles, refreshed = load_spaces(conn, svc, lookup, account, now_iso, log=log)

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
            c, d, who = store_window(conn, name, msgs, a, b, done_until, now_iso)
            changed, deleted = changed + c, deleted + d
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

    if window is None and not only and not exclude:
        # Only a full incremental run makes the whole store current; a month backfill
        # or a filtered run must not tell readers the data is fresh (data_as_of).
        with write_tx(conn):
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)", (now_iso,))

    return {
        "seq": {"before": seq_before, "after": max_seq(conn)},
        "messages_changed": changed_total,
        "messages_deleted": deleted_total,
        "spaces": stats,
        "no_access": no_access,
    }
