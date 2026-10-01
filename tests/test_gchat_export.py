"""Tests for gchat_export, run against a fake Chat API (no network, no credentials).

    python -m unittest discover -s tests -v
"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gchat_export as g  # noqa: E402

D = dt.date.fromisoformat
ME, ANA, BETO = "users/1", "users/2", "users/3"
DIRECTORY = {
    ME: ("Dani Musial", "dani@corp.com"),
    ANA: ("Ana Perez", "ana@corp.com"),
    BETO: ("Beto Gomez", "beto@corp.com"),
}


class _Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeChat:
    """Just enough of the Chat API client for sync(): spaces, members, messages."""

    def __init__(self):
        self.spaces_, self.members_, self.messages_ = {}, {}, {}
        self.calls = Counter()

    def add_space(self, name, kind="SPACE", display=None, members=(ME, ANA)):
        sp = {"name": name, "spaceType": kind,
              "membershipCount": {"joinedDirectHumanUserCount": len(members)}}
        if display:
            sp["displayName"] = display
        self.spaces_[name] = sp
        self.members_[name] = list(members)
        self.messages_[name] = []

    def post(self, space, n, when, sender, text, thread="T1"):
        msg = {"name": f"{space}/messages/{n}", "createTime": when,
               "sender": {"name": sender, "type": "HUMAN"}, "text": text,
               "thread": {"name": f"{space}/threads/{thread}"}}
        self.messages_[space].append(msg)
        sp = self.spaces_[space]
        sp["lastActiveTime"] = max(sp.get("lastActiveTime", ""), when)
        return msg

    def spaces(self):
        return _Spaces(self)


class _Spaces:
    def __init__(self, fake):
        self.f = fake

    def list(self, pageSize=None, pageToken=None):
        self.f.calls["spaces"] += 1
        return _Request(lambda: {"spaces": [dict(s) for s in self.f.spaces_.values()]})

    def members(self):
        return _Members(self.f)

    def messages(self):
        return _Messages(self.f)


class _Members:
    def __init__(self, fake):
        self.f = fake

    def list(self, parent, pageSize=None, pageToken=None):
        self.f.calls["members"] += 1
        return _Request(lambda: {"memberships": [
            {"member": {"name": u, "type": "HUMAN"}} for u in self.f.members_[parent]]})


class _Messages:
    def __init__(self, fake):
        self.f = fake

    def list(self, parent, filter, pageSize=None, pageToken=None):
        self.f.calls["messages"] += 1
        self.f.calls[("messages", parent)] += 1
        lo, hi = re.findall(r'"([^"]+)"', filter)
        return _Request(lambda: {"messages": [
            json.loads(json.dumps(m)) for m in self.f.messages_[parent]
            if lo < m["createTime"] < hi]})


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.md_dir = os.path.join(self.tmp.name, "md")
        self.conn = g.open_db(os.path.join(self.tmp.name, "gchat.db"))
        self.chat = FakeChat()
        self.directory = dict(DIRECTORY)
        self.lookups = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def lookup(self, ids):
        self.lookups += 1
        return {u: self.directory[u] for u in ids if u in self.directory}

    def run_sync(self, today, window=None, force=False):
        return g.sync(self.conn, self.chat, self.lookup, account="dani@corp.com",
                      window=window, md_dir=self.md_dir, force=force,
                      today=D(today), log=lambda *a: None)

    def md(self, month, prefix):
        mdir = os.path.join(self.md_dir, month)
        files = [f for f in os.listdir(mdir) if f.startswith(prefix)] if os.path.isdir(mdir) else []
        self.assertEqual(len(files), 1, f"expected one {prefix}* in {month}, got {files}")
        with open(os.path.join(mdir, files[0]), encoding="utf-8") as f:
            return f.read()

    def seq_of(self, name):
        return self.conn.execute("SELECT seq FROM messages WHERE name = ?", (name,)).fetchone()[0]

    # ------------------------------------------------------------ basics

    def test_first_sync_stores_messages_and_renders_markdown(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-10-01T10:00:00Z", ANA, "deploy roto")
        self.chat.post("spaces/S", 2, "2026-10-01T10:05:00Z", ME, "lo miro")

        report = self.run_sync("2026-10-02")

        self.assertEqual(report["messages_changed"], 2)
        self.assertEqual(report["seq"], {"before": 0, "after": 2})
        rows = self.conn.execute(
            "SELECT sender_name, space_title, text FROM v_messages ORDER BY seq").fetchall()
        self.assertEqual([tuple(r) for r in rows], [
            ("Ana Perez", "La interna", "deploy roto"),
            ("Dani Musial", "La interna", "lo miro")])
        text = self.md("2026-10", "La-interna-")
        self.assertIn("**10:00 Ana Perez**: deploy roto", text)
        self.assertIn("  - **10:05 Dani Musial**: lo miro", text)

    def test_dm_is_named_after_the_other_person(self):
        self.chat.add_space("spaces/D", kind="DIRECT_MESSAGE", members=(ME, BETO))
        self.chat.post("spaces/D", 1, "2026-10-01T09:00:00Z", BETO, "hola")
        self.run_sync("2026-10-02")
        self.assertIn("# Beto Gomez", self.md("2026-10", "Beto-Gomez-"))

    # ------------------------------------------------------------ incremental

    def test_rerun_without_activity_makes_no_message_or_member_calls(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-10-01T10:00:00Z", ANA, "hola")
        self.run_sync("2026-10-02")
        before = Counter(self.chat.calls)

        report = self.run_sync("2026-10-02")

        self.assertEqual(self.chat.calls["messages"], before["messages"])
        self.assertEqual(self.chat.calls["members"], before["members"])
        self.assertEqual(report["seq"]["before"], report["seq"]["after"])
        self.assertEqual(report["spaces"]["skipped_inactive"], 1)

    def test_same_day_rerun_picks_up_later_messages_without_duplicating(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-10-02T09:00:00Z", ANA, "a la manana")
        self.run_sync("2026-10-02")
        first_seq = self.seq_of("spaces/S/messages/1")

        self.chat.post("spaces/S", 2, "2026-10-02T18:00:00Z", ANA, "a la tarde")
        report = self.run_sync("2026-10-02")

        self.assertEqual(report["messages_changed"], 1)
        self.assertEqual(self.seq_of("spaces/S/messages/1"), first_seq)
        text = self.md("2026-10", "La-interna-")
        self.assertEqual(text.count("## 2026-10-02"), 1)
        self.assertIn("a la manana", text)
        self.assertIn("a la tarde", text)

    def test_month_rollover_still_catches_the_last_day(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-09-30T10:00:00Z", ANA, "temprano")
        self.run_sync("2026-09-30")

        self.chat.post("spaces/S", 2, "2026-09-30T23:30:00Z", ANA, "tardisimo")
        self.chat.post("spaces/S", 3, "2026-10-02T08:00:00Z", ANA, "octubre")
        self.run_sync("2026-10-02")

        september = self.md("2026-09", "La-interna-")
        self.assertIn("temprano", september)
        self.assertIn("tardisimo", september)
        self.assertIn("octubre", self.md("2026-10", "La-interna-"))

    def test_window_skips_space_inactive_since_before_it(self):
        self.chat.add_space("spaces/S", display="Viejo")
        self.chat.post("spaces/S", 1, "2026-07-10T10:00:00Z", ANA, "julio")

        report = self.run_sync("2026-10-02", window=(D("2026-09-01"), D("2026-10-01")))

        self.assertEqual(self.chat.calls[("messages", "spaces/S")], 0)
        self.assertEqual(report["spaces"]["skipped_inactive"], 1)
        self.assertEqual(g.get_coverage(self.conn, "spaces/S"), [(D("2026-09-01"), D("2026-10-01"))])

    def test_covered_window_is_not_fetched_again(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-09-10T10:00:00Z", ANA, "hola")
        september = (D("2026-09-01"), D("2026-10-01"))
        self.run_sync("2026-10-02", window=september)
        calls = self.chat.calls["messages"]

        report = self.run_sync("2026-10-02", window=september)

        self.assertEqual(self.chat.calls["messages"], calls)
        self.assertEqual(report["spaces"]["skipped_covered"], 1)

    # ------------------------------------------------------------ changes

    def test_identical_refetch_keeps_seq(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-10-01T10:00:00Z", ANA, "hola")
        self.run_sync("2026-10-02")
        calls = self.chat.calls["messages"]

        # Same window the CLI builds for a bare --force: the current month.
        report = self.run_sync("2026-10-02", window=(D("2026-10-01"), D("2026-10-03")), force=True)

        self.assertGreater(self.chat.calls["messages"], calls)  # it did re-fetch
        self.assertEqual(report["messages_changed"], 0)
        self.assertEqual(report["seq"]["before"], report["seq"]["after"])

    def test_fresh_attachment_urls_do_not_count_as_an_edit(self):
        self.chat.add_space("spaces/S", display="La interna")
        msg = self.chat.post("spaces/S", 1, "2026-10-01T10:00:00Z", ANA, "mira")
        msg["attachment"] = [{"contentName": "foto.png", "attachmentDataRef": {"resourceName": "r1"},
                              "downloadUri": "https://x/get?token=1", "thumbnailUri": "https://x/t?token=1"}]
        self.run_sync("2026-10-02")

        # The API hands out a new access token in these URLs on every request.
        msg["attachment"][0]["downloadUri"] = "https://x/get?token=2"
        msg["attachment"][0]["thumbnailUri"] = "https://x/t?token=2"
        report = self.run_sync("2026-10-02", window=(D("2026-10-01"), D("2026-10-03")), force=True)

        self.assertEqual(report["messages_changed"], 0)
        raw = self.conn.execute("SELECT raw FROM messages").fetchone()[0]
        self.assertNotIn("token=", raw)
        self.assertIn("[attachment: foto.png]", self.md("2026-10", "La-interna-"))

    def test_forward_keeps_its_content_and_nested_urls_are_stable(self):
        self.chat.add_space("spaces/S", display="La interna")
        msg = self.chat.post("spaces/S", 1, "2026-10-01T10:00:00Z", ANA, "")
        msg["quotedMessageMetadata"] = {"quoteType": "FORWARD", "quotedMessageSnapshot": {
            "sender": "Joaquin Mansilla", "text": "revisar la nota 3798315",
            "attachments": [{"contentName": "image.png", "downloadUri": "https://x?token=1"}]}}
        self.run_sync("2026-10-02")

        row = self.conn.execute(
            "SELECT text, quote_type, quoted_sender, quoted_text FROM v_messages").fetchone()
        self.assertEqual(tuple(row), ("", "FORWARD", "Joaquin Mansilla",
                                      "revisar la nota 3798315 [attachment: image.png]"))
        self.assertIn("[forwarded from Joaquin Mansilla: revisar la nota 3798315",
                      self.md("2026-10", "La-interna-"))

        snap = msg["quotedMessageMetadata"]["quotedMessageSnapshot"]
        snap["attachments"][0]["downloadUri"] = "https://x?token=2"
        report = self.run_sync("2026-10-02", window=(D("2026-10-01"), D("2026-10-03")), force=True)
        self.assertEqual(report["messages_changed"], 0)

    def test_edit_bumps_seq_and_updates_markdown(self):
        self.chat.add_space("spaces/S", display="La interna")
        msg = self.chat.post("spaces/S", 1, "2026-10-02T10:00:00Z", ANA, "version vieja")
        self.run_sync("2026-10-02")
        old_seq = self.seq_of(msg["name"])

        msg["text"], msg["lastUpdateTime"] = "version nueva", "2026-10-02T11:00:00Z"
        self.run_sync("2026-10-02")

        self.assertGreater(self.seq_of(msg["name"]), old_seq)
        text = self.md("2026-10", "La-interna-")
        self.assertIn("version nueva", text)
        self.assertNotIn("version vieja", text)

    def test_deleted_message_is_flagged_and_leaves_markdown(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-10-02T10:00:00Z", ANA, "se queda")
        gone = self.chat.post("spaces/S", 2, "2026-10-02T10:01:00Z", ANA, "me arrepenti")
        self.run_sync("2026-10-02")
        old_seq = self.seq_of(gone["name"])

        self.chat.messages_["spaces/S"].remove(gone)
        report = self.run_sync("2026-10-02")

        self.assertEqual(report["messages_deleted"], 1)
        row = self.conn.execute("SELECT deleted, seq FROM messages WHERE name = ?",
                                (gone["name"],)).fetchone()
        self.assertEqual(row["deleted"], 1)
        self.assertGreater(row["seq"], old_seq)
        self.assertNotIn("me arrepenti", self.md("2026-10", "La-interna-"))

    def test_rename_rewrites_markdown_under_the_new_name(self):
        del self.directory[BETO]  # not resolvable yet
        self.chat.add_space("spaces/D", kind="DIRECT_MESSAGE", members=(ME, BETO))
        self.chat.post("spaces/D", 1, "2026-10-01T09:00:00Z", BETO, "hola")
        self.run_sync("2026-10-02")
        self.md("2026-10", "Unnamed-DM-")

        self.directory[BETO] = DIRECTORY[BETO]
        self.run_sync("2026-10-02")

        self.assertIn("# Beto Gomez", self.md("2026-10", "Beto-Gomez-"))
        self.assertFalse(any(f.startswith("Unnamed-DM-")
                             for f in os.listdir(os.path.join(self.md_dir, "2026-10"))))

    def test_thread_continuation_is_marked_across_months(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-09-30T20:00:00Z", ANA, "arranca", thread="T9")
        self.chat.post("spaces/S", 2, "2026-10-01T09:00:00Z", ME, "sigue", thread="T9")
        self.run_sync("2026-10-02", window=(D("2026-09-01"), D("2026-10-03")))

        self.assertNotIn("continues an earlier thread", self.md("2026-09", "La-interna-"))
        self.assertIn("**09:00 Dani Musial**: (continues an earlier thread) sigue",
                      self.md("2026-10", "La-interna-"))

    def test_consumer_cursor_sees_only_what_changed(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-10-02T09:00:00Z", ANA, "uno")
        cursor = self.run_sync("2026-10-02")["seq"]["after"]
        self.chat.post("spaces/S", 2, "2026-10-02T10:00:00Z", ANA, "dos")
        self.run_sync("2026-10-02")

        rows = self.conn.execute("SELECT text FROM v_messages WHERE seq > ? ORDER BY seq",
                                 (cursor,)).fetchall()
        self.assertEqual([r[0] for r in rows], ["dos"])


class PureTest(unittest.TestCase):
    def test_merge_and_gaps(self):
        covered = [(D("2026-09-10"), D("2026-09-20")), (D("2026-09-01"), D("2026-09-10"))]
        self.assertEqual(g.merge_intervals(covered), [(D("2026-09-01"), D("2026-09-20"))])
        self.assertEqual(g.gaps((D("2026-08-25"), D("2026-09-25")), covered),
                         [(D("2026-08-25"), D("2026-09-01")), (D("2026-09-20"), D("2026-09-25"))])
        self.assertEqual(g.gaps((D("2026-09-02"), D("2026-09-05")), covered), [])

    def test_windows(self):
        today = D("2026-10-15")
        w = lambda *argv: g.resolve_window(g.parse_args(list(argv)), today)  # noqa: E731
        self.assertIsNone(w())
        self.assertIsNone(w("--update"))
        self.assertEqual(w("--month", "2026-12"), (D("2026-12-01"), D("2027-01-01")))
        self.assertEqual(w("--days", "3"), (D("2026-10-12"), D("2026-10-16")))
        self.assertEqual(w("--force"), (D("2026-10-01"), D("2026-10-16")))

    def test_filenames_are_stable_and_distinct(self):
        a = g.space_filename("spaces/A", "Proyecto X")
        b = g.space_filename("spaces/B", "Proyecto X")
        self.assertNotEqual(a, b)
        self.assertEqual(a.rsplit("-", 1)[1], g.space_filename("spaces/A", "Otro").rsplit("-", 1)[1])
        self.assertEqual(g.slugify("Diseño & Producción"), "Diseno-Produccion")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._token = g.TOKEN
        g.TOKEN = os.path.join(self.tmp.name, "missing-token.json")

    def tearDown(self):
        g.TOKEN = self._token
        self.tmp.cleanup()

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = g.main(list(argv))
        return code, out.getvalue()

    def test_non_interactive_without_token_exits_3_with_json(self):
        code, out = self.run_main("--non-interactive", "--json", "--data-dir", self.tmp.name)
        self.assertEqual(code, g.EXIT_AUTH)
        self.assertEqual(json.loads(out)["error"], "auth_required")

    def test_bad_month_fails_before_any_auth(self):
        code, _ = self.run_main("--non-interactive", "--month", "2026-13")
        self.assertEqual(code, g.EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
