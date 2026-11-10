"""Fakes shared by the tests: a Chat API client and a directory lookup (no network)."""
import datetime as dt
import json
import re
from collections import Counter

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
