# Decisions

Short entries, newest last. To change one, add a new entry that supersedes it; do not edit history.
Entries 1–15 come from `gchat-export` (2026-09 / 2026-10), before it became an MCP server.

## 1. Chat API, not scraping the web UI (2026-09-08)
The API filters by `createTime` and pages 1000 at a time. Scrolling the web UI was slow and fragile.

## 2. Internal OAuth consent screen (2026-09-08)
**Why:** an External app stays in Testing and its refresh token expires every 7 days.

## 3. SQLite is the source of truth (2026-10-01)
**Why:** a consumer needs message ids (to link a task back to its origin), to know what it
already processed, and to query without rereading a month. SQLite ships with Python, is one
file and is queryable. (The Markdown view it came with was dropped: #16.)

## 4. `seq` change cursor; the consumer owns its state (2026-10-01)
Every insert, edit or deletion raises `seq`; a message that comes back identical keeps its own.
A consumer stores "processed up to N" and asks for `seq > N`. The downloader knows nothing about
consumers.

## 5. Deletions are flagged, not removed (2026-10-01)
`deleted = 1` plus a new `seq`, so a consumer can retract whatever it derived from that message.

## 6. `downloadUri` / `thumbnailUri` are stripped at any depth before storing (2026-10-01)
They carry a fresh token on every request: kept, every run marked all messages with attachments
as edited (55 of 868 in "La interna"). They also appear inside a forwarded message's
`quotedMessageSnapshot.attachments`; the first fix only looked at the top level and missed 2.
They expire anyway; `attachmentDataRef` is the stable reference.

## 7. Quotes and forwards in their own columns (2026-10-01)
`quote_type`, `quoted_sender`, `quoted_text`. A forward has no text of its own; its content
(e.g. "note 3798315, vulnerability in @sap/cds-mtxs") lives only in the snapshot. `text` stays
what that person wrote.

## 8. Coverage per space in UTC days; today is never covered (2026-10-01)
So the next run fetches today again whole: two runs on the same day still catch the afternoon,
and the run on the 1st catches the 30th's late messages. No special-case logic.

## 9. Dates compare by `YYYY-MM-DD` prefix, never full timestamps as text (2026-10-01)
As text, `"00:00:00.123Z"` sorts before `"00:00:00Z"` (`.` < `Z`). The read side compares
`substr(create_time, 1, 19)` (#20).

## 10. Skip spaces by `lastActiveTime` (2026-10-01)
A space with no activity since the missing part is not called at all. This takes the daily run
from ~2 min to 3–5 s (107 of 111 spaces skipped). Verified: in all 111 spaces no message is newer
than its `lastActiveTime`.

## 11. Members cached; refreshed only when `membershipCount` changes (2026-10-01)
It was ~110 calls per run. `--force` does NOT refresh them (that cost 76 s).

## 12. Data outside the repository (2026-10-01)
`--data-dir` > `$GCHAT_DATA_DIR` > `~/gchat-export`. The project already moved folders twice.

## 13. `--non-interactive` exits 3 instead of opening the OAuth flow (2026-10-01)
An unattended process must never hang waiting for a browser.

## 14. WAL + `BEGIN IMMEDIATE` (2026-10-01)
A reader can read while a download writes, and two simultaneous downloads cannot repeat a `seq`.

## 15. Retries: `num_retries=3` for 429/5xx (2026-10-01)

## 16. No Markdown output (2026-11-10, Daniel's call)
The `.md` per space and month was a human view from before the store existed. With the MCP
server Claude reads the store directly, with message ids and less context. Supersedes the
file-naming, atomic-write and rename rules that only served the Markdown.

## 17. The downloader stays a CLI; the MCP `sync` tool runs it as a subprocess (2026-11-10, Daniel's call)
**Why:** one code path for manual runs and the tool; Google code and OAuth stay out of the
server process; a hung download is killed by a timeout without taking the server down.

## 18. The MCP server reads SQLite directly and never imports Google code (2026-11-10)
Read-only connection per call (`mode=ro`), so it always sees fresh data and cannot write.
`tests/test_server.py::test_server_does_not_import_google` enforces the boundary.

## 19. Stateless cursor: every result carries `latest_seq` and `data_as_of` (2026-11-10)
The server keeps no consumer state (like outlook-mcp #5). A new consumer starts its cursor at
`latest_seq` instead of reading the whole store; `get_changes(after_seq=0)` is refused.
`data_as_of` (from `meta.last_sync`) tells the caller whether to `sync` first.

## 20. Tool dates are local time; `until` is exclusive (2026-11-10)
Matches outlook-mcp and how Daniel thinks ("October" = local October). Converted to UTC keys
for queries; rows show local `YYYY-MM-DD HH:MM`.

## 21. Search folds case and accents with a Python SQLite function, no FTS (2026-11-10)
No schema change and no index to keep in sync; the store is small (tens of MB).

## 22. `sync` tool limits (2026-11-10)
120 s for incremental, 600 s for a month backfill. The child gets `stdin=DEVNULL` (the server's
stdin is the MCP protocol stream) and UTF-8 pipes (Windows pipes default to cp1252 and mangle
titles). Exit 3 becomes a tool error with the exact `--reauth` command.

## 23. OAuth files live in `<data_dir>/auth/` (2026-11-10)
`credentials.json` and `token.json` no longer sit next to the code, so moving the code never
breaks authorization.

## 24. No Windows scheduled task (2026-11-10)
The "GoogleChat - descarga" task never existed on this machine (the store stopped on
2026-10-30). The Claude scheduled task `revisar-mails` (weekdays 9/12/15/18) calls `sync` before
reading, and an ad-hoc review can too.

## 25. Chat content is wrapped as untrusted (2026-11-10)
Every tool output with chat-derived text is wrapped in `<untrusted_chat_content>`; invisible
and bidi characters are removed and fake closing tags neutralized (code from outlook-mcp).
Colleagues' messages and forwarded mail are attacker-controllable input.

## 26. `mcp>=2` (2026-11-10)
The server uses `mcp.server.mcpserver.MCPServer`, which is the 2.x API.

## 27. `last_sync` is written only by a full incremental run (2026-11-10)
A month backfill or an `--only`/`--exclude` run leaves other spaces behind; recording it would
make `data_as_of` claim fresh data and a reader would skip `sync`.

## 28. Tool JSON escapes `<` as `\u003c` (2026-11-10)
Still valid JSON, but no chat text can form a tag that closes the untrusted wrapper (a regex on
the tag missed `</untrusted_chat_content x="1">`). Complements #25.

## 29. Only a rejected token means re-authorize (2026-11-10)
`RefreshError` → exit 3 (`--reauth`). A network failure while refreshing (laptop just woke up)
propagates as an error (exit 1): following a `--reauth` hint offline would delete a working token.

## 30. The sync child runs with `CREATE_NO_WINDOW` (2026-11-10)
The server is started without a console by Claude; a console child would flash a window per sync.
