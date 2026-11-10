"""The command-line contract: exit codes and JSON, without touching Google."""
import contextlib
import io
import json

from gchat_mcp import cli


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue()


def test_non_interactive_without_token_exits_3_with_json(tmp_path):
    code, out = run_main("--non-interactive", "--json", "--data-dir", str(tmp_path))
    assert code == cli.EXIT_AUTH
    assert json.loads(out)["error"] == "auth_required"


def test_bad_month_fails_before_any_auth(tmp_path):
    code, _ = run_main("--non-interactive", "--month", "2026-13", "--data-dir", str(tmp_path))
    assert code == cli.EXIT_ERROR
