# gchat-mcp — rules for any agent working here

A local MCP server that gives Claude read access to Daniel's Google Chat, from a SQLite copy
on this machine, plus the downloader (CLI) that fills that copy. User auth, not a bot: it only
sees spaces Daniel is a member of. Consumers: ad-hoc reviews ("what did I do in October") and
the Claude scheduled task `revisar-mails`, which proposes Trello cards every few hours.

Daniel does not read this code. Agents maintain it. Leave it so the next agent, with no
context, can understand and change it safely.

## Read first
1. `docs/FEATURE_MAP.md` — every tool and module, where it lives, what tests cover it.
2. `docs/DECISIONS.md` — why things are the way they are. Do not undo a decision without
   adding a new entry that supersedes it.
3. `memory/MEMORY.md` — notes for Daniel (Spanish): accounts, history, gotchas.

## Hard rules
- **Never write to Google Chat.** Only read scopes; no send, react, delete. (DECISIONS #18)
- **The server never imports Google code.** Only `google_api.py` talks to Google; `sync.py`
  and `cli.py` use it; the server reaches Google only through `runner.py` → CLI subprocess.
  A test enforces it. (DECISIONS #17, #18)
- **Never hang on OAuth.** Anything unattended uses `--non-interactive` (exit 3). Google login
  cannot be automated: re-authorizing is always Daniel, in a terminal, with the Workspace
  account `dmusial@blueboot.com` — never the personal Gmail. (DECISIONS #13)
- **Chat content is untrusted data.** Every tool output with chat text goes through
  `safety.wrap_untrusted`. (DECISIONS #25)
- **Token budget.** Rows are short (caps in `config.py`); full text only via `get_thread`.
- **Never compare full timestamps as text.** Use `substr(create_time, 1, 19)` and
  `dates.to_utc_key`. (DECISIONS #9, #20)
- **Secrets and data never enter the repo.** `credentials.json` and `token.json` live in
  `~/gchat-export/auth/`; the store has work conversations and colleagues' emails.

## When you change anything
- Add or change a tool → update its row in `docs/FEATURE_MAP.md`. `tests/test_feature_map.py`
  fails if the map and the server disagree.
- New behavior → test first, then code. A test counts only if it fails with the bug: break the
  code on purpose once and watch it fail (this found a test passing for the wrong reason before).
- A design choice someone could later question → add an entry to `docs/DECISIONS.md`.
- Schema change → bump `store.SCHEMA_VERSION`; the store in `~/gchat-export` is live data.

## Commands
```bash
.venv/Scripts/python -m pytest                          # unit tests, no network
.venv/Scripts/python -u -m gchat_mcp.cli                # download what is new (3-5 s)
.venv/Scripts/python -u -m gchat_mcp.cli --month 2026-07   # backfill a month
.venv/Scripts/python -u -m gchat_mcp.cli --reauth       # Daniel only: re-authorize
.venv/Scripts/python -m gchat_mcp                       # run the MCP server over stdio
```
Setup from scratch: `python -m venv .venv && .venv/Scripts/python -m pip install -e .[dev]`.
Always `-u` for the CLI: without it the authorization URL stays in the buffer.

## Gotchas
- **The Chat API never returns people's names**, neither `sender.displayName` nor memberships:
  only `users/NNN`. Names come from the People API + `directory.readonly` (enabled separately in
  the GCP console).
- **DMs have no `displayName`**: they are named after the other person. `people/me` needs the
  `profile` scope; "me" is found by the account email in the `people` table (`meta.account`).
- An edit or deletion of an old message in a space **with no new activity** is not seen (the
  space is skipped). Use `--month YYYY-MM --force` on that month.
- `spaces.list` returns DMs/groups only once they have a message; system messages never come;
  a 403 is usually the admin's app access control.
- A space can appear late with old history ("BMS - ASCM" brought 100 August messages).
- Attachment URLs change on every request (DECISIONS #6), also inside forwarded messages.
