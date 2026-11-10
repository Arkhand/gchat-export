# gchat-mcp — design

Date: 2026-11-10. Status: draft, waiting for Daniel's approval.

## Intent

`gchat-export` (this repo) downloads Daniel's Google Chat into a local SQLite store.
It was built so "another program" could read that store. That program is Claude, so
the downloader becomes an MCP server: Claude reads the chats directly through tools
instead of each consumer opening SQLite by hand.

Two real uses drive every decision:

1. **Month-end review, ad hoc.** "What did I do in October?", "what was said with X
   about Y?". Needs date ranges, per-conversation overviews, "only my messages", and
   must not flood the context with a whole month of chat.
2. **An agent that runs every 3 hours and reviews pending work.** Needs "what is new
   since last time", cheap, unattended, never blocked waiting for a browser.

## Goals

- One project, `gchat-mcp`, at `C:\Users\musia\Desktop\Proyectos\_MCP\gchat-mcp`,
  shaped like its sibling `_MCP\outlook-mcp` (read that project first: layout, docs,
  safety wrapper, test style, server API).
- An MCP server with read tools over the store plus a `sync` tool.
- The downloader stays a command-line program; the MCP `sync` tool runs it as a
  subprocess (Daniel's call).
- Keep the git history and the GitHub remote; delete the old folder at the end.

## Non-goals

- The 3-hour pending-review agent itself. It already exists: the local Claude scheduled
  task `revisar-mails` (Mon–Fri 9/12/15/18, `~/.claude/scheduled-tasks/revisar-mails/SKILL.md`),
  which reviews Outlook and proposes Trello cards. Daniel extends it himself with a
  Google Chat section once this MCP is registered; that section calls `sync` first,
  then `get_changes` with its own cursor in `~/.claude/gchat-watch/state.json`.
- Summarizing, extracting tasks, interpreting. Same rule as before: it only reads/downloads.
- Writing anything to Google Chat. Ever.
- Markdown output. Dropped (Daniel's call): the store is the only output.

## Anti-goals (failure even if it "works")

- The MCP server or an unattended run hangs waiting for an OAuth browser flow.
- A month query dumps thousands of messages into the context.
- Chat text is treated as instructions (prompt injection from a colleague's message
  or a forwarded mail).
- Losing data or the authorization during the move: today's store
  (`~/gchat-export/gchat.db`, Aug–Oct 2026, 23 MB) must keep working as is.

Only Daniel uses this installation (my assumption; the README's teammate section is
kept as setup docs but nobody else runs it today), so dropping Markdown and moving the
auth files need no compatibility step.

## Constraints and facts from the current code

Everything under "Decisiones y por que" and "Gotchas conocidos" in the current
`CLAUDE.md` still holds and moves into `docs/DECISIONS.md` (rewritten in English,
numbered like outlook-mcp's). The ones a builder trips on:

- `seq` cursor: grows on insert/edit/delete; identical refetch keeps it. Deletions are
  flagged (`deleted = 1`), never removed.
- `downloadUri`/`thumbnailUri` stripped at any depth before storing.
- Dates compared by `YYYY-MM-DD` prefix, never full RFC 3339 strings as text
  (`"…00.123Z"` sorts before `"…00Z"`). Any new time filter must respect this —
  e.g. compare `substr(create_time, 1, 19)` against a normalized `YYYY-MM-DDTHH:MM:SS`.
- Stored times are UTC. Coverage is in UTC days; today is never marked covered.
- WAL + `BEGIN IMMEDIATE`: a reader and a writer can run at once; two syncs can't
  repeat a `seq`.
- `--non-interactive` exits 3 instead of opening a browser.
- Google login cannot be automated; re-authorization is always Daniel in a terminal.
- Python 3.11 on this machine. Deps today: `google-api-python-client`,
  `google-auth-oauthlib`; add `mcp` (outlook-mcp runs `mcp` 2.3.0, `MCPServer` API).

## What gets built

### Layout

```
_MCP/gchat-mcp/
  pyproject.toml           # like outlook-mcp; package in src/, pytest config
  CLAUDE.md  README.md  SETUP.md
  docs/FEATURE_MAP.md  docs/DECISIONS.md
  memory/                  # project memory (Spanish), see Migration
  src/gchat_mcp/
    __main__.py            # python -m gchat_mcp → MCP server over stdio
    server.py              # tools: validate, query, shape, wrap
    cli.py                 # python -m gchat_mcp.cli → the downloader (today's main)
    google_api.py          # the only module that talks to Google (auth + API calls)
    sync.py                # today's sync/store_window/load_spaces/people logic
    store.py               # schema, open_db, write_tx, coverage, seq
    queries.py             # read side used by the server (plain SQL over v_messages)
    safety.py  config.py  dates.py
  tests/
```

Module split is a suggestion; the hard line is "only `google_api.py` talks to Google,
the server never imports it".

### The command-line program

Today's `gchat_export.py` CLI, same flags and same exit codes (0 ok, 1 error,
2 partial, 3 auth required), minus everything Markdown (`md_dir`, `files` in the
JSON report, the `file` column of `--list-spaces`, `render_markdown` and friends).
`--update` is a no-op kept for compatibility; drop it.

Changes:
- **Auth files move** to `<data_dir>/auth/credentials.json` and `token.json`
  (my call: the project already moved folders once; auth should not live with code).
- After a successful sync it writes `meta.last_sync` (UTC timestamp) so readers can
  say how fresh the data is. No schema version bump: `meta` is key/value.

### MCP tools

Output style copied from outlook-mcp: compact JSON, every chat-derived payload wrapped
by `safety.wrap_untrusted` with tag `untrusted_chat_content`, row caps in `config.py`,
dates accepted as `7d`, `24h`, `YYYY-MM-DD`, `YYYY-MM-DD HH:MM` in **local time**
(converted to UTC for queries), times shown in local time `YYYY-MM-DD HH:MM`.
Every read result includes `data_as_of` (`meta.last_sync`, local time) and
`latest_seq` (current `MAX(seq)`), so a new consumer can start its cursor from
"now" without reading the whole store (my call).

Text search is case- and accent-insensitive via a Python function registered on the
SQLite connection; no FTS table, no schema change (my call). Pin `mcp>=2` in
`pyproject.toml` (the `MCPServer` API is 2.x; outlook-mcp's `>=1.2` is wrong).

Message row (short form): `id` (message `name`), `seq`, `time`, `space`, `sender`,
`from_me`, `thread`, `text` truncated to a per-row cap, `quoted` (type, sender,
truncated text) when present, `attachments` (names), `deleted` when 1.
`from_me` uses the same "who am I" logic the downloader uses for DM names
(account email → `people` table).

| Tool | For | Behavior |
|---|---|---|
| `sync(month=None)` | both | Runs the CLI as a subprocess (`<venv python> -m gchat_mcp.cli --non-interactive --json`, plus `--month` if given) with a timeout (incremental ~120 s; month backfill ~600 s). Returns the JSON summary trimmed (seq before/after, changed, deleted, spaces counts, no_access). Exit 3 → tool error telling Daniel to run `python -m gchat_mcp.cli --reauth` in a terminal with the Workspace account. Timeout → kill the process, tool error. Not read-only (writes the local store), never writes to Google. |
| `activity(since, until)` | month-end | Per conversation in the range: title, type, total messages, my messages, first/last time. Sorted by activity. The cheap overview that decides what to open. |
| `get_messages(space, since, until, from_me=False, limit)` | month-end | One conversation's messages in the range, short rows, oldest first, grouped by thread. `space` is the id or part of the title (ambiguous → error listing matches). |
| `search_messages(query, sender, space, since, until, limit)` | month-end | Text search over `text` and `quoted_text`. `sender` accepts `"me"`. Newest first. At least one filter required. |
| `get_thread(thread)` | both | Whole thread, full text (still capped), for when a short row is not enough. |
| `get_changes(after_seq, limit)` | 3-hour agent | Rows with `seq > after_seq` ordered by `seq`, including edits and deletions (flagged). Returns `next_seq` and `has_more`. The caller stores `next_seq`; the server keeps no consumer state (same as outlook-mcp DECISIONS #5). `after_seq=0` is refused with a hint to use `activity` instead (it would mean the whole store). |

Exact caps, defaults and parameter names are the builder's call; keep them in
`config.py` and in `docs/FEATURE_MAP.md`.

### No Windows scheduled task

The task "GoogleChat - descarga" **does not exist today** (checked 2026-11-10;
the store's last write is 2026-10-30), and it is not re-created: `revisar-mails`
calls `sync` four times a weekday, and an ad-hoc review can call `sync` too
(my call: one fewer moving part). `register-task.ps1` and `run-gchat.ps1` are not
carried over.

## Migration (order matters)

1. Copy the whole repo folder, `.git` included, to `_MCP\gchat-mcp`. Restructure there.
   The remote stays `https://github.com/Arkhand/gchat-export.git`. Renaming the GitHub
   repo: not done unless Daniel asks (my call; he did not answer).
2. Move `credentials.json` and `token.json` to `~/gchat-export/auth/`.
3. Drop `register-task.ps1` / `run-gchat.ps1` (untracked; see "No Windows scheduled task").
4. Project memory: copy the 4 files from
   `~/.claude/projects/C--Users-musia-Desktop-Proyectos--SinCliente-GoogleChatCloude/memory/`
   into `memory/` in the repo, updating paths. Fold `CLAUDE.md` (Spanish today) into
   an English agent-rules `CLAUDE.md` + `DECISIONS.md`, as outlook-mcp does; things
   meant for Daniel stay Spanish in `memory/`.
5. Port the existing tests (unittest, fake Chat API) to the new modules; drop the
   Markdown assertions, keep the seq/coverage/deletion/rename ones. Add server tests
   in outlook-mcp's style (`build_server(...).call_tool` in-process) and a
   feature-map test.
6. Verify (below).
7. With Daniel's OK, each separately: register the MCP at user scope
   (`claude mcp add gchat --scope user -- <venv python> -m gchat_mcp`), delete `~/gchat-export/md/`, delete the old folder
   `_SinCliente\GoogleChatCloude` (its `out/`, `personas.json`, `_to_delete/` go with it),
   commit, push.

Nothing in Google Cloud changes: same OAuth client, same scopes, same token.

## Verification (evidence, not claims)

- `pytest` green, and at least one test per new tool proven to fail when the tool
  is broken (the project's own rule: a test counts only if it fails with the bug).
- Live: CLI incremental sync from the new folder with the moved token catches up
  2026-10-30 → today; JSON shows seq growing; exit 0 or 2.
- Live: each MCP tool called in-process against the real store; `activity` for
  2026-10 matches a direct SQL count; `get_changes` from the pre-sync seq returns
  exactly the rows the sync reported.
- Live: `sync` tool returns the summary; with the token temporarily renamed it
  returns the auth error fast and does not hang.
- After registration: a fresh Claude session lists the `gchat` tools.

## Left to the builder

Module boundaries beyond the hard line above, caps and defaults, exact row fields,
how `space` partial matching ranks, test structure, README wording.
