"""Build small stores directly in SQLite for the read-side tests."""
import json

from fakes import DIRECTORY
from gchat_mcp import store


def make_store(tmp_path):
    conn = store.open_db(str(tmp_path / "gchat.db"))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('account', 'dani@corp.com')")
    conn.executemany("INSERT INTO people VALUES (?, ?, ?, '2026-10-01T00:00:00Z')",
                     [(u, name, email) for u, (name, email) in DIRECTORY.items()])
    return conn


def add_space(conn, name, title, type="SPACE"):
    conn.execute("INSERT INTO spaces (name, title, type) VALUES (?, ?, ?)", (name, title, type))


def add_msg(conn, n, space, sender, when, text, thread="T1", seq=None, deleted=0,
            quote=None, attachments=()):
    """quote = (type, sender, text)."""
    if seq is None:
        seq = store.max_seq(conn) + 1
    qt, qs, qx = quote or (None, None, None)
    conn.execute(
        "INSERT INTO messages (name, space, thread, sender_id, create_time, text, attachments,"
        " quote_type, quoted_sender, quoted_text, raw, deleted, seq, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, '2026-10-01T00:00:00Z')",
        (f"{space}/messages/{n}", space, f"{space}/threads/{thread}", sender, when, text,
         json.dumps(list(attachments)), qt, qs, qx, deleted, seq))
