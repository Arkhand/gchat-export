"""Downloader tests, against a fake Chat API (no network, no credentials)."""
import datetime as dt
import json
import os
import tempfile
import unittest
from collections import Counter

from fakes import ANA, BETO, DIRECTORY, ME, FakeChat
from gchat_mcp import cli, store, sync

D = dt.date.fromisoformat


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = store.open_db(os.path.join(self.tmp.name, "gchat.db"))
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
        return sync.sync(self.conn, self.chat, self.lookup, account="dani@corp.com",
                      window=window, force=force,
                      today=D(today), log=lambda *a: None)

    def sync_only(self, today, only):
        return sync.sync(self.conn, self.chat, self.lookup, account="dani@corp.com",
                         window=None, only=only, today=D(today), log=lambda *a: None)

    def title_of(self, space):
        return self.conn.execute("SELECT title FROM spaces WHERE name = ?", (space,)).fetchone()[0]

    def texts(self, space="spaces/S"):
        """Live (not deleted) message texts of a space, oldest first."""
        return [r[0] for r in self.conn.execute(
            "SELECT text FROM messages WHERE space = ? AND deleted = 0 ORDER BY create_time", (space,))]

    def seq_of(self, name):
        return self.conn.execute("SELECT seq FROM messages WHERE name = ?", (name,)).fetchone()[0]

    # ------------------------------------------------------------ basics

    def test_first_sync_stores_messages(self):
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

    def test_dm_is_named_after_the_other_person(self):
        self.chat.add_space("spaces/D", kind="DIRECT_MESSAGE", members=(ME, BETO))
        self.chat.post("spaces/D", 1, "2026-10-01T09:00:00Z", BETO, "hola")
        self.run_sync("2026-10-02")
        self.assertEqual(self.title_of("spaces/D"), "Beto Gomez")

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
        self.assertEqual(self.texts(), ["a la manana", "a la tarde"])

    def test_month_rollover_still_catches_the_last_day(self):
        self.chat.add_space("spaces/S", display="La interna")
        self.chat.post("spaces/S", 1, "2026-09-30T10:00:00Z", ANA, "temprano")
        self.run_sync("2026-09-30")

        self.chat.post("spaces/S", 2, "2026-09-30T23:30:00Z", ANA, "tardisimo")
        self.chat.post("spaces/S", 3, "2026-10-02T08:00:00Z", ANA, "octubre")
        self.run_sync("2026-10-02")

        self.assertEqual(self.texts(), ["temprano", "tardisimo", "octubre"])

    def test_window_skips_space_inactive_since_before_it(self):
        self.chat.add_space("spaces/S", display="Viejo")
        self.chat.post("spaces/S", 1, "2026-07-10T10:00:00Z", ANA, "julio")

        report = self.run_sync("2026-10-02", window=(D("2026-09-01"), D("2026-10-01")))

        self.assertEqual(self.chat.calls[("messages", "spaces/S")], 0)
        self.assertEqual(report["spaces"]["skipped_inactive"], 1)
        self.assertEqual(store.get_coverage(self.conn, "spaces/S"), [(D("2026-09-01"), D("2026-10-01"))])

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
        att = self.conn.execute("SELECT attachments FROM messages").fetchone()[0]
        self.assertEqual(json.loads(att), ["foto.png"])

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

        snap = msg["quotedMessageMetadata"]["quotedMessageSnapshot"]
        snap["attachments"][0]["downloadUri"] = "https://x?token=2"
        report = self.run_sync("2026-10-02", window=(D("2026-10-01"), D("2026-10-03")), force=True)
        self.assertEqual(report["messages_changed"], 0)

    def test_edit_bumps_seq_and_updates_text(self):
        self.chat.add_space("spaces/S", display="La interna")
        msg = self.chat.post("spaces/S", 1, "2026-10-02T10:00:00Z", ANA, "version vieja")
        self.run_sync("2026-10-02")
        old_seq = self.seq_of(msg["name"])

        msg["text"], msg["lastUpdateTime"] = "version nueva", "2026-10-02T11:00:00Z"
        self.run_sync("2026-10-02")

        self.assertGreater(self.seq_of(msg["name"]), old_seq)
        self.assertEqual(self.texts(), ["version nueva"])

    def test_deleted_message_is_flagged_not_removed(self):
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
        self.assertEqual(self.texts(), ["se queda"])

    def test_dm_title_updates_when_the_person_resolves(self):
        del self.directory[BETO]  # not resolvable yet
        self.chat.add_space("spaces/D", kind="DIRECT_MESSAGE", members=(ME, BETO))
        self.chat.post("spaces/D", 1, "2026-10-01T09:00:00Z", BETO, "hola")
        self.run_sync("2026-10-02")
        self.assertEqual(self.title_of("spaces/D"), "Unnamed DM")

        self.directory[BETO] = DIRECTORY[BETO]
        self.run_sync("2026-10-02")

        self.assertEqual(self.title_of("spaces/D"), "Beto Gomez")

    def test_sync_records_last_sync(self):
        self.chat.add_space("spaces/A", display="Team")
        self.chat.post("spaces/A", 1, "2026-10-02T10:00:00Z", ANA, "hi")
        self.run_sync("2026-10-02")
        row = self.conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        self.assertTrue(row is not None and row[0].endswith("Z"))

    def test_window_or_filtered_sync_does_not_claim_freshness(self):
        self.chat.add_space("spaces/A", display="Team")
        self.chat.post("spaces/A", 1, "2026-10-02T10:00:00Z", ANA, "today")
        self.run_sync("2026-10-02", window=(D("2026-07-01"), D("2026-08-01")))
        self.sync_only("2026-10-02", only=["Team"])
        row = self.conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        self.assertIsNone(row)

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
        self.assertEqual(store.merge_intervals(covered), [(D("2026-09-01"), D("2026-09-20"))])
        self.assertEqual(store.gaps((D("2026-08-25"), D("2026-09-25")), covered),
                         [(D("2026-08-25"), D("2026-09-01")), (D("2026-09-20"), D("2026-09-25"))])
        self.assertEqual(store.gaps((D("2026-09-02"), D("2026-09-05")), covered), [])

    def test_windows(self):
        today = D("2026-10-15")
        w = lambda *argv: cli.resolve_window(cli.parse_args(list(argv)), today)  # noqa: E731
        self.assertIsNone(w())
        self.assertEqual(w("--month", "2026-12"), (D("2026-12-01"), D("2027-01-01")))
        self.assertEqual(w("--days", "3"), (D("2026-10-12"), D("2026-10-16")))
        self.assertEqual(w("--force"), (D("2026-10-01"), D("2026-10-16")))
