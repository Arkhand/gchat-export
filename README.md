# gchat-mcp

An MCP server that gives Claude read access to your Google Chat, from a local SQLite copy,
plus the downloader that keeps that copy up to date.

It uses **user authentication**, not a bot: it only sees conversations you are already a
member of, and it never writes to Google Chat.

- Rules for agents maintaining it: [`CLAUDE.md`](CLAUDE.md)
- Tools and where they live: [`docs/FEATURE_MAP.md`](docs/FEATURE_MAP.md)
- Why it is built this way: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- One-time Google Cloud setup: [`SETUP.md`](SETUP.md)

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]
.venv/Scripts/python -u -m gchat_mcp.cli --list-spaces     # first run: authorize in the browser
claude mcp add gchat --scope user -- <full path>\.venv\Scripts\python.exe -m gchat_mcp
```

`credentials.json` goes in `~/gchat-export/auth/` (see [SETUP.md](SETUP.md)); the token is
saved next to it on the first run.

## MCP tools

| Tool | Use it for |
|---|---|
| `activity(since, until)` | per-conversation counts for a period: where to look |
| `get_messages(space, since, until, from_me, limit)` | one conversation in a period, short rows |
| `search_messages(query, sender, space, since, until, limit)` | text search, case/accent-insensitive; `sender="me"` |
| `get_thread(thread)` | one thread in full |
| `get_changes(after_seq, limit)` | what is new, edited or deleted since a cursor |
| `sync(month)` | download what is new (or backfill a month) before reading |

Dates are local time: `7d`, `24h`, `YYYY-MM-DD`, `YYYY-MM-DD HH:MM`; `until` is exclusive.
Every result carries `data_as_of` (last download) and `latest_seq` (current cursor).

A scheduled consumer keeps its own cursor: first run, store `latest_seq`; then each run calls
`sync`, then `get_changes(after_seq=<stored>)` until `has_more` is false, and stores `next_seq`.

## The downloader (CLI)

```bash
.venv/Scripts/python -u -m gchat_mcp.cli                       # catch up since the last run
.venv/Scripts/python -u -m gchat_mcp.cli --month 2026-08       # backfill a whole month
.venv/Scripts/python -u -m gchat_mcp.cli --month 2026-09 --force   # re-download, e.g. after edits
.venv/Scripts/python -u -m gchat_mcp.cli --non-interactive --json  # unattended (what `sync` runs)
```

Always run it with `-u`: without it the authorization URL stays in the buffer and the program
looks hung while it waits.

With no range it is **incremental**: each conversation resumes where its last download ended
(or at the start of the current month). Conversations with no activity since then are skipped
without an API call, so a run takes 3–5 s.

| Flag | What it does |
|---|---|
| *(none)* | incremental, since each conversation's last download |
| `--month YYYY-MM` | a whole month |
| `--days N` | the last N days |
| `--since` / `--until` | `YYYY-MM-DD`, UTC, until exclusive |
| `--force` | re-download the range even if already covered or inactive (default range: current month) |
| `--only` / `--exclude` | filter conversations by name (repeatable) |
| `--list-spaces` | list conversations and exit |
| `--non-interactive` | never open a browser; exit `3` if authorization is needed |
| `--json` | machine-readable summary on stdout; logs go to stderr |
| `--data-dir PATH` | where the data lives (see below) |
| `--reauth` | delete the token and authorize again |

The **account is not a parameter**: it comes from the token. The first log line says which
account is running. To change it, `--reauth`.

| Exit code | Meaning |
|---|---|
| `0` | ok |
| `1` | error |
| `2` | partial: some conversations not accessible (`no_access` lists them) |
| `3` | authorization required (only with `--non-interactive`) |

`--json` prints one document:

```json
{
  "account": "you@company.com",
  "data_dir": "C:\\Users\\you\\gchat-export",
  "db": "C:\\Users\\you\\gchat-export\\gchat.db",
  "mode": "incremental",
  "window": null,
  "seq": {"before": 12720, "after": 12784},
  "messages_changed": 64,
  "messages_deleted": 0,
  "spaces": {"total": 111, "selected": 111, "fetched": 4, "skipped_inactive": 107,
             "skipped_covered": 0, "members_refreshed": 0},
  "no_access": []
}
```

## Where the data lives

Outside the code: `--data-dir PATH` > `$GCHAT_DATA_DIR` > `~/gchat-export`.

```
~/gchat-export/
  gchat.db              the store
  auth/
    credentials.json    the OAuth client (from SETUP.md)
    token.json          your token: as sensitive as a password
```

## The store

Every message has a `seq` that grows on each insert, edit or deletion; messages that come back
unchanged keep theirs. A message deleted in Chat is not removed: it gets `deleted = 1` and a new
`seq`, so a consumer can retract whatever it derived from it.

`v_messages` is the convenient view:

| Column | |
|---|---|
| `seq` | change cursor |
| `name` | `spaces/X/messages/Y`, stable id; use it to link derived items back |
| `space`, `space_title`, `space_type` | conversation (`SPACE`, `GROUP_CHAT`, `DIRECT_MESSAGE`) |
| `thread` | thread id; group replies with it |
| `sender_id`, `sender_name`, `sender_email` | author |
| `create_time`, `update_time` | RFC 3339, UTC |
| `text` | plain text written by the sender (empty for a bare forward) |
| `attachments` | JSON list of file names |
| `quote_type`, `quoted_sender`, `quoted_text` | the message this one forwards (`FORWARD`) or quotes (`REPLY`). A forward's content lives here, not in `text` |
| `deleted` | `1` if deleted in Chat |

Underlying tables: `messages` (also keeps the API message in `raw`, minus the attachment URLs,
which carry a per-request token and expire), `spaces` (`uri` opens the conversation in Chat),
`people`, `coverage` (days downloaded per conversation) and `meta` (`schema_version`,
`account`, `last_sync`). The store runs in WAL mode: the server reads while a download writes.

## Limitations

- An edit or deletion of an old message in a conversation with no new activity is not noticed:
  inactive conversations are skipped. Use `--month YYYY-MM --force`.
- Attachment contents are not downloaded, only their names and stable references.
- `spaces.list` returns DMs and groups only once they have at least one message.
- System messages ("X joined the space") are not returned by the API.
- A conversation answering 403 is skipped and reported in `no_access`; usually the Workspace
  admin's app access control.

## Other accounts

Each person runs their own copy, authorized with their own account and their own data dir.
Someone in the same Workspace organization only needs `credentials.json` from the project
owner, shared privately; someone from another organization creates their own project
([SETUP.md](SETUP.md) from step 1). Never share `token.json` or the data dir.

## Tests

```bash
.venv/Scripts/python -m pytest
```

No network, no credentials: the downloader runs against a fake Chat API, the server against
small stores built in the test.
