"""The downloader as a command-line program.

    python -u -m gchat_mcp.cli                          # catch up since the last run
    python -u -m gchat_mcp.cli --month 2026-08          # backfill a whole month
    python -u -m gchat_mcp.cli --non-interactive --json # unattended (the MCP sync tool)
    python -u -m gchat_mcp.cli --reauth                 # authorize again (Workspace account)

Exit codes: 0 ok, 1 error, 2 partial (some spaces not accessible),
            3 authorization required (only with --non-interactive).
"""
import argparse
import datetime as dt
import json
import os
import sys

from googleapiclient.discovery import build

from .google_api import AuthRequired, get_creds, people_lookup, whoami
from .store import DB_NAME, DEFAULT_DATA_DIR, data_dir, open_db
from .sync import load_spaces, month_start, next_month, space_type, sync, utc_today

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL, EXIT_AUTH = 0, 1, 2, 3


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Download Google Chat messages into the local SQLite store.")
    ap.add_argument("--days", type=int, help="last N days")
    ap.add_argument("--since", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--until", help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--month", help="YYYY-MM: a whole month")
    ap.add_argument("--only", action="append", default=[],
                    help="only spaces whose name contains this (repeatable)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="skip spaces whose name contains this (repeatable)")
    ap.add_argument("--force", action="store_true",
                    help="re-download the window even if already covered or inactive")
    ap.add_argument("--list-spaces", action="store_true", help="list spaces and exit")
    ap.add_argument("--reauth", action="store_true", help="delete the token and authorize again")
    ap.add_argument("--non-interactive", action="store_true",
                    help=f"never open a browser; exit {EXIT_AUTH} if authorization is needed")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable summary on stdout; logs go to stderr")
    ap.add_argument("--data-dir",
                    help=f"where the store and auth/ live "
                         f"(default: $GCHAT_DATA_DIR or {DEFAULT_DATA_DIR})")
    return ap.parse_args(argv)


def resolve_window(args, today):
    """(since, until) for an explicit range, or None for incremental mode."""
    if args.month:
        start = dt.date.fromisoformat(args.month + "-01")
        return start, next_month(start)
    if args.days:
        return today - dt.timedelta(days=args.days), today + dt.timedelta(days=1)
    if args.since or args.until:
        since = dt.date.fromisoformat(args.since) if args.since else month_start(today)
        until = dt.date.fromisoformat(args.until) if args.until else today + dt.timedelta(days=1)
        return since, until
    if args.force:
        return month_start(today), today + dt.timedelta(days=1)
    return None


def main(argv=None):
    args = parse_args(argv)
    # With --json, stdout is reserved for the JSON document.
    log = (lambda *a: print(*a, file=sys.stderr)) if args.json else print
    today = utc_today()

    # Validate before touching the network or the browser.
    try:
        window = resolve_window(args, today)
    except ValueError as e:
        log(f"[X] Invalid date: {e}")
        return EXIT_ERROR
    if window and window[1] <= window[0]:
        log(f"[X] Empty range: {window[0]} -> {window[1]}")
        return EXIT_ERROR

    root = data_dir(args.data_dir)
    db_path = os.path.join(root, DB_NAME)

    try:
        creds = get_creds(os.path.join(root, "auth"), reauth=args.reauth, interactive=not args.non_interactive, log=log)
    except AuthRequired as e:
        log(f"[X] {e}")
        if args.json:
            print(json.dumps({"error": "auth_required", "message": str(e)}))
        return EXIT_AUTH

    account = whoami(creds)
    log(f"[i] Account: {account}")
    chat = build("chat", "v1", credentials=creds, cache_discovery=False)
    lookup = people_lookup(creds, log)
    conn = open_db(db_path)
    try:
        if args.list_spaces:
            now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            spaces, titles, _ = load_spaces(conn, chat, lookup, account, now_iso, log=log)
            rows = sorted(({"name": sp["name"], "title": titles[sp["name"]],
                            "type": space_type(sp), "last_active": sp.get("lastActiveTime")}
                           for sp in spaces), key=lambda r: r["title"].lower())
            if args.json:
                print(json.dumps({"account": account, "spaces": rows}, ensure_ascii=False, indent=2))
            else:
                log(f"[i] {len(rows)} spaces:\n")
                for r in rows:
                    log(f"    {r['title']:<45} {r['type']:<16} {(r['last_active'] or '')[:10]}")
            return EXIT_OK

        report = sync(conn, chat, lookup, account=account, window=window,
                      force=args.force, only=args.only, exclude=args.exclude,
                      today=today, log=log)
    finally:
        conn.close()

    code = EXIT_PARTIAL if report["no_access"] else EXIT_OK
    if args.json:
        print(json.dumps({
            "account": account,
            "data_dir": root,
            "db": db_path,
            "mode": "incremental" if window is None else "window",
            "window": None if window is None else
                      {"since": window[0].isoformat(), "until": window[1].isoformat()},
            **report,
        }, ensure_ascii=False, indent=2))
    else:
        s, seq = report["spaces"], report["seq"]
        log(f"\n[OK] {report['messages_changed']} messages new or changed, "
            f"{report['messages_deleted']} deleted (seq {seq['before']} -> {seq['after']})")
        log(f"     spaces: {s['fetched']} fetched, {s['skipped_inactive']} inactive, "
            f"{s['skipped_covered']} already covered")
        log(f"     store: {db_path}")
        if report["no_access"]:
            log(f"[!] No access to {len(report['no_access'])} spaces:")
            for e in report["no_access"]:
                log(f"      {e['error']}  {e['space']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
