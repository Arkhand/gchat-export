"""The SQLite store: schema, transactions, the seq cursor and per-space coverage.

Pure sqlite3; no Google code. Both the downloader and the MCP server use it.
"""
import contextlib
import datetime as dt
import os
import sqlite3

DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), "gchat-export")
DB_NAME = "gchat.db"


SCHEMA_VERSION = 1


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


def data_dir(arg=None):
    """Where the store lives: arg > $GCHAT_DATA_DIR > ~/gchat-export."""
    return os.path.abspath(arg or os.environ.get("GCHAT_DATA_DIR") or DEFAULT_DATA_DIR)


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
