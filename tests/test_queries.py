"""Read side: what the MCP tools return, over a store built by hand."""
from datetime import datetime, timedelta, timezone

import pytest

from fakes import ANA, BETO, ME
from gchat_mcp.queries import Reader
from storefix import add_msg, add_space, make_store

UY = timezone(timedelta(hours=-3))


@pytest.fixture
def conn(tmp_path):
    c = make_store(tmp_path)
    yield c
    c.close()


@pytest.fixture
def reader(conn):
    return Reader(conn, UY)


def test_local_day_boundary_and_fraction(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-01T02:59:59Z", "late sept")
    add_msg(conn, 2, "spaces/A", ANA, "2026-10-01T03:00:00.500Z", "first oct")
    rows = reader.messages("spaces/A", since=datetime(2026, 10, 1), until=None, from_me=False, limit=50)
    assert [r["text"] for r in rows] == ["first oct"]
    assert rows[0]["time"] == "2026-10-01 00:00"


def test_until_is_exclusive(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ANA, "2026-11-01T02:59:00Z", "oct 31 local")
    add_msg(conn, 2, "spaces/A", ANA, "2026-11-01T03:00:00Z", "nov 1 local")
    rows = reader.messages("spaces/A", datetime(2026, 10, 1), datetime(2026, 11, 1), False, 50)
    assert [r["text"] for r in rows] == ["oct 31 local"]


def test_activity_counts_mine_and_skips_deleted(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_space(conn, "spaces/B", "Ops")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-05T12:00:00Z", "a")
    add_msg(conn, 2, "spaces/A", ME, "2026-10-06T12:00:00Z", "b")
    add_msg(conn, 3, "spaces/A", ANA, "2026-10-07T12:00:00Z", "gone", deleted=1)
    add_msg(conn, 4, "spaces/A", ANA, "2026-09-20T12:00:00Z", "outside")
    add_msg(conn, 5, "spaces/B", BETO, "2026-10-08T12:00:00Z", "c")
    acts = reader.activity(datetime(2026, 10, 1), datetime(2026, 11, 1))
    assert [(a["title"], a["messages"], a["mine"]) for a in acts] == [("Team", 2, 1), ("Ops", 1, 0)]
    assert acts[0]["first"] == "2026-10-05 09:00" and acts[0]["last"] == "2026-10-06 09:00"


def test_messages_from_me_and_sender_names(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-05T12:00:00Z", "pedido")
    add_msg(conn, 2, "spaces/A", ME, "2026-10-05T12:05:00Z", "hecho")
    rows = reader.messages("spaces/A", datetime(2026, 10, 1), None, False, 50)
    assert [(r["sender"], r["from_me"]) for r in rows] == [("Ana Perez", False), ("Dani Musial", True)]
    mine = reader.messages("spaces/A", datetime(2026, 10, 1), None, True, 50)
    assert [r["text"] for r in mine] == ["hecho"]


def test_messages_grouped_by_thread(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-05T12:00:00Z", "t1 start", thread="T1")
    add_msg(conn, 2, "spaces/A", BETO, "2026-10-05T12:01:00Z", "t2 start", thread="T2")
    add_msg(conn, 3, "spaces/A", ME, "2026-10-05T12:02:00Z", "t1 reply", thread="T1")
    rows = reader.messages("spaces/A", datetime(2026, 10, 1), None, False, 50)
    assert [r["text"] for r in rows] == ["t1 start", "t1 reply", "t2 start"]


def test_changes_paging_edge(conn, reader):
    add_space(conn, "spaces/A", "Team")
    for n in range(1, 5):
        add_msg(conn, n, "spaces/A", ANA, f"2026-10-0{n}T12:00:00Z", f"m{n}", deleted=int(n == 3))
    page = reader.changes(after_seq=0, limit=2)
    assert [m["seq"] for m in page["messages"]] == [1, 2] and page["has_more"] and page["next_seq"] == 2
    page = reader.changes(after_seq=2, limit=2)
    assert [m["seq"] for m in page["messages"]] == [3, 4] and not page["has_more"]
    assert page["messages"][0]["deleted"] is True
    assert "deleted" not in page["messages"][1]
    empty = reader.changes(after_seq=4, limit=2)
    assert empty == {"messages": [], "next_seq": 4, "has_more": False}


def test_resolve_space(conn, reader):
    add_space(conn, "spaces/T", "Team")
    add_space(conn, "spaces/O", "Team Ops")
    add_space(conn, "spaces/D", "Daniela Díaz", type="DIRECT_MESSAGE")
    assert reader.resolve_space("spaces/O") == "spaces/O"
    assert reader.resolve_space("team") == "spaces/T"
    assert reader.resolve_space("diaz") == "spaces/D"
    with pytest.raises(ValueError, match="Team Ops"):
        reader.resolve_space("tea")
    with pytest.raises(ValueError):
        reader.resolve_space("nope")


def test_search_accent_case_quoted_and_me(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_space(conn, "spaces/B", "Ops")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-05T12:00:00Z", "Revisión del iFlow")
    add_msg(conn, 2, "spaces/B", ME, "2026-10-06T12:00:00Z", "",
            quote=("FORWARD", "Joaquin", "revisar la nota 3798315"))
    add_msg(conn, 3, "spaces/B", ANA, "2026-10-07T12:00:00Z", "otra cosa")
    hits = reader.search("revision", None, None, None, None, 20)
    assert [h["text"] for h in hits] == ["Revisión del iFlow"]
    hit = reader.search("3798315", "me", None, None, None, 20)
    assert len(hit) == 1 and hit[0]["from_me"] is True
    assert hit[0]["quoted"] == {"type": "FORWARD", "sender": "Joaquin", "text": "revisar la nota 3798315"}
    assert [h["text"] for h in reader.search(None, "ana", "ops", None, None, 20)] == ["otra cosa"]


def test_search_newest_first_and_limit(conn, reader):
    add_space(conn, "spaces/A", "Team")
    for n in range(1, 4):
        add_msg(conn, n, "spaces/A", ANA, f"2026-10-0{n}T12:00:00Z", f"deploy {n}")
    assert [h["text"] for h in reader.search("deploy", None, None, None, None, 2)] == ["deploy 3", "deploy 2"]


def test_row_text_is_truncated_but_thread_is_not(conn, reader):
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-02T12:00:00Z", "x" * 1000, attachments=["a.pdf"])
    row = reader.messages("spaces/A", datetime(2026, 10, 1), None, False, 10)[0]
    assert len(row["text"]) == 301 and row["text"].endswith("…")
    assert row["attachments"] == ["a.pdf"]
    full = reader.thread("spaces/A/threads/T1")
    assert len(full[0]["text"]) == 1000


def test_status(conn, reader):
    assert reader.status() == {"data_as_of": None, "latest_seq": 0}
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ANA, "2026-10-02T12:00:00Z", "x")
    conn.execute("INSERT INTO meta VALUES ('last_sync', '2026-11-10T15:00:00Z')")
    assert reader.status() == {"data_as_of": "2026-11-10 12:00", "latest_seq": 1}


def test_no_account_means_nothing_is_mine(conn):
    conn.execute("DELETE FROM meta WHERE key = 'account'")
    add_space(conn, "spaces/A", "Team")
    add_msg(conn, 1, "spaces/A", ME, "2026-10-02T12:00:00Z", "x")
    assert Reader(conn, UY).messages("spaces/A", datetime(2026, 10, 1), None, False, 10)[0]["from_me"] is False
