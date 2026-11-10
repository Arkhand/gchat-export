# Feature map

One row per MCP tool. `tests/test_feature_map.py` parses the **Tools** table and fails if a
tool exists in the server but not here, or the other way around. Keep rows short.

## Tools

| Tool | What it does | Code | Tests | Decisions |
|---|---|---|---|---|
| `activity` | Per-conversation counts (total, mine, first, last) for a period; the overview | `server.py` → `queries.py::Reader.activity` | `test_server.py`, `test_queries.py` | #18, #20 |
| `get_messages` | One conversation's messages in a period, threads kept together, short rows; `from_me` filter | `server.py` → `queries.py::Reader.messages` (`resolve_space`) | `test_server.py`, `test_queries.py` | #18, #20 |
| `search_messages` | Text search over text and quoted/forwarded text, case/accent-insensitive; sender, space, dates | `server.py` → `queries.py::Reader.search` | `test_server.py`, `test_queries.py` | #18, #21 |
| `get_thread` | One thread in full | `server.py` → `queries.py::Reader.thread` | `test_server.py`, `test_queries.py` | #18 |
| `get_changes` | New/edited/deleted messages after a `seq` cursor, with `next_seq` and `has_more` | `server.py` → `queries.py::Reader.changes` | `test_server.py`, `test_queries.py` | #4, #19, #28 |
| `sync` | Runs the downloader CLI as a child process (incremental, or `month` backfill) with a timeout; returns its summary; auth/timeout/errors as tool errors | `server.py` → `runner.py::run_cli` → `cli.py` | `test_server.py`, `test_runner.py`, `test_auth.py` | #17, #22, #27, #29, #30 |

## Internal modules

| Module | Responsibility |
|---|---|
| `server.py` | Tool definitions; opens the store read-only per call; wraps output |
| `runner.py` | Starts the CLI child process (stdin closed, UTF-8 pipes, timeout) |
| `queries.py` | Read-side SQL over the store (`Reader`): ranges, rows, `from_me`, space resolution, search folding |
| `dates.py` | Parses tool dates (`7d`, `24h`, `YYYY-MM-DD[ HH:MM]`); local ↔ UTC keys |
| `safety.py` | Untrusted-content wrapper and invisible-character removal |
| `config.py` | Row caps, text caps, timeouts, store path |
| `store.py` | Schema, `open_db`, `write_tx`, `seq`, coverage intervals, data dir |
| `cli.py` | The downloader's command line (`python -m gchat_mcp.cli`), exit codes, JSON report |
| `sync.py` | Download logic: spaces, members, people, message windows, deletions, `last_sync` |
| `google_api.py` | The only Google code: OAuth (files in `<data_dir>/auth`), Chat and People API calls |
