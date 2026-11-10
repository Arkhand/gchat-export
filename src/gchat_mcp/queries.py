"""Read side of the store, used by the MCP server. Plain SQL; no Google code.

Times: tool arguments are naive local datetimes, rows show local 'YYYY-MM-DD HH:MM'.
The store keeps UTC RFC 3339 text; ranges compare substr(create_time, 1, 19) so
fractional seconds cannot break the ordering (see dates.to_utc_key).
"""

import json
import sqlite3
import unicodedata
from datetime import datetime, tzinfo

from . import config
from .dates import to_local, to_utc_key
from .store import max_seq

_ROW_SQL = """
SELECT m.seq, m.name, m.space, COALESCE(s.title, m.space) AS title, m.thread,
       m.sender_id, COALESCE(p.name, m.sender_id) AS sender, m.create_time, m.text,
       m.attachments, m.quote_type, m.quoted_sender, m.quoted_text, m.deleted
FROM messages m
LEFT JOIN spaces s ON s.name = m.space
LEFT JOIN people p ON p.user_id = m.sender_id
"""


def fold(value) -> str:
    """Case- and accent-insensitive form: 'Revisión' → 'revision'."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _like(value: str) -> str:
    """LIKE pattern for a substring of fold(value); % and _ are literal."""
    escaped = fold(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _cut(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


class Reader:
    def __init__(self, conn: sqlite3.Connection, tz: tzinfo):
        conn.row_factory = sqlite3.Row
        conn.create_function("fold", 1, fold, deterministic=True)
        self.conn, self.tz = conn, tz
        row = conn.execute(
            "SELECT p.user_id FROM people p JOIN meta ON meta.key = 'account' "
            "WHERE lower(p.email) = lower(meta.value)").fetchone()
        self.me = row[0] if row else None

    # ------------------------------------------------------------ helpers

    def _range(self, since: datetime | None, until: datetime | None) -> tuple[str, list]:
        sql, params = "", []
        if since:
            sql += " AND substr(m.create_time, 1, 19) >= ?"
            params.append(to_utc_key(since, self.tz))
        if until:
            sql += " AND substr(m.create_time, 1, 19) < ?"
            params.append(to_utc_key(until, self.tz))
        return sql, params

    def _row(self, r: sqlite3.Row, text_limit: int = config.ROW_TEXT_CHARS) -> dict:
        row = {
            "id": r["name"], "seq": r["seq"], "time": to_local(r["create_time"], self.tz),
            "space": r["title"], "sender": r["sender"],
            "from_me": self.me is not None and r["sender_id"] == self.me,
            "thread": r["thread"], "text": _cut(r["text"], text_limit),
        }
        if r["quote_type"] or r["quoted_text"]:
            row["quoted"] = {"type": r["quote_type"], "sender": r["quoted_sender"],
                             "text": _cut(r["quoted_text"], config.QUOTE_CHARS)}
        attachments = json.loads(r["attachments"] or "[]")
        if attachments:
            row["attachments"] = attachments
        if r["deleted"]:
            row["deleted"] = True
        return row

    # ------------------------------------------------------------ queries

    def status(self) -> dict:
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
        return {"data_as_of": to_local(row[0], self.tz) if row else None,
                "latest_seq": max_seq(self.conn)}

    def activity(self, since: datetime, until: datetime | None) -> list[dict]:
        rng, params = self._range(since, until)
        rows = self.conn.execute(f"""
            SELECT m.space, COALESCE(s.title, m.space) AS title, s.type, COUNT(*) AS n,
                   SUM(CASE WHEN m.sender_id = ? THEN 1 ELSE 0 END) AS mine,
                   MIN(substr(m.create_time, 1, 19)) AS first,
                   MAX(substr(m.create_time, 1, 19)) AS last
            FROM messages m LEFT JOIN spaces s ON s.name = m.space
            WHERE m.deleted = 0 {rng}
            GROUP BY m.space ORDER BY n DESC, title""", [self.me, *params])
        return [{"space": r["space"], "title": r["title"], "type": r["type"],
                 "messages": r["n"], "mine": r["mine"],
                 "first": to_local(r["first"], self.tz), "last": to_local(r["last"], self.tz)}
                for r in rows]

    def resolve_space(self, space: str) -> str:
        """Space id, exact title, or a unique part of a title (case/accents ignored)."""
        rows = self.conn.execute("SELECT name, title FROM spaces").fetchall()
        if any(r["name"] == space for r in rows):
            return space
        exact = [r for r in rows if fold(r["title"]) == fold(space)]
        if len(exact) == 1:
            return exact[0]["name"]
        matches = [r for r in rows if fold(space) in fold(r["title"])]
        if len(matches) == 1:
            return matches[0]["name"]
        if not matches:
            raise ValueError(f"No conversation matches {space!r}. Use activity to list them.")
        found = sorted(f"{r['title'] or '?'} ({r['name']})" for r in matches)[:10]
        raise ValueError(f"{space!r} matches {len(matches)} conversations: {', '.join(found)}. "
                         "Pass the id in parentheses, or a more specific title.")

    def messages(self, space: str, since: datetime | None, until: datetime | None,
                 from_me: bool, limit: int) -> list[dict]:
        """Oldest first, each thread kept together in order of its first message."""
        rng, params = self._range(since, until)
        mine = " AND m.sender_id = ?" if from_me else ""
        rows = self.conn.execute(
            f"{_ROW_SQL} WHERE m.space = ? AND m.deleted = 0 {rng}{mine} "
            "ORDER BY substr(m.create_time, 1, 19), m.create_time LIMIT ?",
            [self.resolve_space(space), *params, *([self.me] if from_me else []), limit]).fetchall()
        order: dict[str, int] = {}
        for r in rows:
            order.setdefault(r["thread"], len(order))
        return [self._row(r) for r in sorted(rows, key=lambda r: order[r["thread"]])]

    def search(self, query: str | None, sender: str | None, space: str | None,
               since: datetime | None, until: datetime | None, limit: int) -> list[dict]:
        """Newest first. sender='me' means Daniel's own messages."""
        where, params = ["m.deleted = 0"], []
        if query:
            where.append("(fold(m.text) LIKE ? ESCAPE '\\' OR fold(m.quoted_text) LIKE ? ESCAPE '\\')")
            params += [_like(query)] * 2
        if sender and sender.strip().lower() == "me":
            where.append("m.sender_id = ?")
            params.append(self.me)
        elif sender:
            where.append("(fold(p.name) LIKE ? ESCAPE '\\' OR fold(p.email) LIKE ? ESCAPE '\\')")
            params += [_like(sender)] * 2
        if space:
            where.append("m.space = ?")
            params.append(self.resolve_space(space))
        rng, rparams = self._range(since, until)
        rows = self.conn.execute(
            f"{_ROW_SQL} WHERE {' AND '.join(where)} {rng} "
            "ORDER BY substr(m.create_time, 1, 19) DESC LIMIT ?", [*params, *rparams, limit])
        return [self._row(r) for r in rows]

    def thread(self, thread: str) -> list[dict]:
        rows = self.conn.execute(
            f"{_ROW_SQL} WHERE m.thread = ? AND m.deleted = 0 "
            "ORDER BY substr(m.create_time, 1, 19) LIMIT ?", (thread, config.THREAD_MAX))
        return [self._row(r, config.THREAD_TEXT_CHARS) for r in rows]

    def changes(self, after_seq: int, limit: int) -> dict:
        """Everything with seq > after_seq (edits and deletions included), by seq."""
        rows = self.conn.execute(f"{_ROW_SQL} WHERE m.seq > ? ORDER BY m.seq LIMIT ?",
                                 (after_seq, limit + 1)).fetchall()
        page = [self._row(r) for r in rows[:limit]]
        return {"messages": page, "next_seq": page[-1]["seq"] if page else after_seq,
                "has_more": len(rows) > limit}
