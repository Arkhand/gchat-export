# gchat-export

Downloads your Google Chat messages into a local SQLite store, plus one readable
Markdown file per conversation and month.

It uses **user authentication**, not a bot: it only sees conversations you are
already a member of.

**It only downloads.** It does not summarize, extract tasks or interpret anything.
Another program (an MCP server, a skill, a scheduled job) runs it and then reads
the store. Everything below is shaped around making that second program's job easy.

## What it does

Each run:

1. **Lists your conversations**: spaces, group chats and direct messages.
2. **Names them.** The Chat API only returns ids like `users/104692848...`; names
   come from the Workspace directory and are cached. A DM is named after the other
   person.
3. **Downloads only what is missing.** Every conversation remembers which days it
   already has. Conversations with no activity since then are skipped without a
   single API call, so a daily run touches only the few that changed.
4. **Stores every message** in `gchat.db` (SQLite): text, author, thread,
   attachments, forwarded content, and a `seq` number that grows whenever a message
   is added, edited or deleted.
5. **Rewrites the Markdown** of the conversation/months that changed, one readable
   file each.
6. **Reports** what it did: a short summary, or a JSON document with `--json`.

A daily run looks like this:

```
$ python -u gchat_export.py
[i] Account: you@company.com
[i] 111 of 111 spaces, incremental since each one's last sync
       +41  La interna
       +12  Francisco Cosco

[OK] 53 messages new or changed, 0 deleted (seq 12762 -> 12815)
     spaces: 4 fetched, 107 inactive, 0 already covered
     2 Markdown files updated -> C:\Users\you\gchat-export\md
     store: C:\Users\you\gchat-export\gchat.db
```

## Install

See [SETUP.md](SETUP.md): a Google Cloud project, two APIs and a `credentials.json`.
One-time work.

```bash
pip install -r requirements.txt
python -u gchat_export.py --list-spaces    # first run: authorize in the browser
```

Always run it with `python -u`. Without it Python buffers stdout, the authorization
URL never shows, and the script looks hung while it is actually waiting.

## Using it with another account

Each person runs their own copy, authorized with their own account. Everyone only
sees the conversations they are a member of, even when sharing a Google Cloud
project.

### A teammate in the same Workspace organization

No Google Cloud setup needed: the existing project's consent screen is **Internal**,
so any account in the organization can authorize it (project details in
[SETUP.md](SETUP.md#this-installation)).

1. **Get the code.** The repository is private: ask for access and clone it, or
   get a copy.
2. **Get `credentials.json` from the project owner, privately** (a direct message, a
   password manager). It identifies the app, not a person. It is not in the
   repository on purpose; keep it that way.
3. Put it next to `gchat_export.py`, then:
   ```bash
   pip install -r requirements.txt
   python -u gchat_export.py --list-spaces
   ```
4. **Authorize with your own Workspace account**, not a personal Gmail. Check the
   first line it prints: `[i] Account: you@company.com`.
5. Backfill what you want (`--month 2026-09`), then run it daily with no flags.

Your data goes to your own `~/gchat-export`. Nothing is shared with anyone else.

### Someone from another organization

An Internal app only accepts accounts from the organization that owns the project.
Follow [SETUP.md](SETUP.md) from step 1 to create your own project; then the same
steps 3 to 5 above apply.

### What to share, and what never to share

| File | Share? | Why |
|---|---|---|
| `credentials.json` | privately, with teammates only | identifies the app; not a password, but no reason to publish it |
| `token.json` | **never** | grants access to *your* conversations; it is as sensitive as a password |
| the data dir (`~/gchat-export`) | **never** | your conversations and your colleagues' names and emails |

One copy of the script holds one account: `token.json` lives next to it. To use two
accounts on one machine, keep two copies of the repository, each with its own
`--data-dir`. To switch the account of a copy, run `--reauth`.

## Usage

```bash
python -u gchat_export.py                     # catch up since the last run
python -u gchat_export.py --month 2026-08     # backfill a whole month
python -u gchat_export.py --days 30           # last 30 days
python -u gchat_export.py --month 2026-09 --force   # re-download, e.g. after edits
```

With no range it is **incremental**: each conversation resumes where its last sync
ended (or at the start of the current month if it was never synced). Run it as often
as you like; running it twice in a row is cheap and safe.

| Flag | What it does |
|---|---|
| *(none)* | incremental, since each conversation's last sync |
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
| `--update` | same as no flag; kept for compatibility |

The **account is not a parameter**: it comes from `token.json`, created when you
authorize. The script prints which account it runs as. To change it, `--reauth`.

## Where the data lives

Outside the code, so it does not move when the repository does:

1. `--data-dir PATH`, if given
2. otherwise `$GCHAT_DATA_DIR`
3. otherwise `~/gchat-export`

```
~/gchat-export/
  gchat.db                          the store: source of truth
  md/
    2026-09/
      La-interna-b451c5.md          one file per conversation and month
      Francisco-Cosco-8b4eb3.md
```

The Markdown is generated from the store, only for the conversation/months that
changed. The suffix comes from the conversation id, so two people with the same name
never collide and a file survives renames.

```markdown
## 2026-09-03

**14:22 Dani Musial**: staging is broken
  - **14:25 Marce Gomez**: looking
  - **15:40 Marce Gomez**: it was the env var, fixed

**16:01 Dani Musial**: here is the report [attachment: report-august.pdf]
```

Grouped by day, then by thread. Times are UTC. A forward shows its content as
`[forwarded from X: ...]`; a reply quoting another message shows a short
`[quoting X: ...]`. A reply to a thread that started on
an earlier day or month is marked `(continues an earlier thread)`.

## Consumer contract

This is what a second program can rely on.

### Calling it

```bash
python -u gchat_export.py --non-interactive --json
```

| Exit code | Meaning | What the caller should do |
|---|---|---|
| `0` | ok | read the store |
| `1` | error | report it |
| `2` | partial: some conversations not accessible | read the store; `no_access` lists them |
| `3` | authorization required | ask a human to run it once without `--non-interactive` |

stdout is a single JSON document:

```json
{
  "account": "you@company.com",
  "db": "C:\\Users\\you\\gchat-export\\gchat.db",
  "md_dir": "C:\\Users\\you\\gchat-export\\md",
  "mode": "incremental",
  "seq": {"before": 12720, "after": 12784},
  "messages_changed": 64,
  "messages_deleted": 0,
  "spaces": {"total": 111, "fetched": 4, "skipped_inactive": 107, "skipped_covered": 0},
  "files": [{"path": "...\\md\\2026-10\\La-interna-b451c5.md", "space": "La interna",
             "month": "2026-10", "messages": 41}],
  "no_access": []
}
```

The caller does not need to know where the data lives: `db` and `md_dir` say so.

### Reading changes: the `seq` cursor

Every message has a `seq` that grows on each insert, edit or deletion. Messages that
come back unchanged keep theirs. The consumer keeps its own cursor (the downloader
knows nothing about it) and asks only for what changed:

```python
import sqlite3

conn = sqlite3.connect(report["db"])
rows = conn.execute(
    "SELECT * FROM v_messages WHERE seq > ? ORDER BY seq", (last_seen,)
).fetchall()
last_seen = max([r["seq"] for r in rows], default=last_seen)  # persist it
```

A message deleted in Chat is not removed: it gets `deleted = 1` and a new `seq`, so
the consumer can retract whatever it derived from it.

### Schema

`v_messages` is the view meant for consumers:

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

Underlying tables: `messages` (also keeps the API message in `raw`, minus the
attachment URLs, which carry a per-request token and expire), `spaces`
(`uri` opens the conversation in Chat), `people`, `coverage` (days downloaded per
conversation) and `meta` (`schema_version`, `account`).

The store runs in WAL mode: a consumer can read while a download writes.

## How the incremental mode stays fast

- **Inactive conversations cost nothing.** Each conversation reports when it was
  last active. If that is before the part still missing, no call is made.
- **Members are cached.** They are fetched again only when a conversation's member
  count changes.
- **Names are cached** in the `people` table.
- **Today is never final.** The current day is not marked as covered, so the next
  run fetches it again whole. Running twice on the same day still catches the
  afternoon's messages; a run on the 1st still catches the 30th's late messages.

Measured on 111 conversations:

| Run | Time |
|---|---|
| First month ever (reads every member list, resolves 71 people) | ~110 s |
| A further month backfill | 30–75 s (network-bound) |
| Daily incremental run | 3–5 s |

## Limitations

- An edit or deletion of an old message in a conversation with no new activity is
  not noticed: inactive conversations are skipped. Use `--force` on that month.
- Attachment contents are not downloaded, only their names and stable references.
- `spaces.list` returns DMs and groups only once they have at least one message.
- System messages ("X joined the space") are not returned by the API.
- A conversation answering 403 is skipped and reported in `no_access`; usually the
  Workspace admin's app access control.

## Tests

```bash
python -m unittest discover -s tests -v
```

They run against a fake Chat API: no network, no credentials.

## Security

Never committed (see `.gitignore`):

| File | Contains |
|---|---|
| `credentials.json` | the app's OAuth credential |
| `token.json` | your access token |
| the data dir | work conversations, names and emails, unencrypted |

The data dir lives outside the repository by default. If you keep months of DMs
there, point it at a backed-up or encrypted location.
