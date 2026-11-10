"""run_cli: the child process the sync tool starts."""
import subprocess
import sys
import time

import pytest

from gchat_mcp.runner import run_cli


def test_child_cannot_read_server_stdin(tmp_path):
    # Like the MCP server: our stdin carries protocol bytes the child must never see.
    eat = tmp_path / "eat.py"
    eat.write_text("import sys; d = sys.stdin.read(); print(len(d))")
    parent = ("import sys; from gchat_mcp.runner import run_cli; "
              f"r = run_cli([], timeout=10, cmd=[sys.executable, {str(eat)!r}]); print(r.stdout.strip())")
    out = subprocess.run([sys.executable, "-c", parent], input='{"jsonrpc": "2.0"}\n',
                         capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "0", out.stderr


def test_output_and_exit_code_are_captured(tmp_path):
    script = tmp_path / "talk.py"
    script.write_text("import sys; print('{\"a\": 1}'); print('log', file=sys.stderr); sys.exit(2)")
    r = run_cli(["--x"], timeout=10, cmd=[sys.executable, str(script)])
    assert (r.code, r.stdout.strip(), r.stderr.strip()) == (2, '{"a": 1}', "log")


def test_non_ascii_output_survives(tmp_path):
    # Space titles like "Diseño" travel in the child's JSON; a cp1252 pipe would mangle them.
    script = tmp_path / "enie.py"
    script.write_text("print('Diseño ✓')", encoding="utf-8")
    assert run_cli([], timeout=10, cmd=[sys.executable, str(script)]).stdout.strip() == "Diseño ✓"


def test_timeout_kills_child(tmp_path):
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)")
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_cli([], timeout=1, cmd=[sys.executable, str(script)])
    assert time.monotonic() - start < 10


def test_default_command_is_this_python_running_the_cli():
    r = run_cli(["--help"], timeout=60)
    assert r.code == 0 and "--non-interactive" in r.stdout


def test_no_console_window_on_windows(monkeypatch):
    seen = {}

    def fake_run(*a, **k):
        seen.update(k)
        return subprocess.CompletedProcess(a[0], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_cli([], timeout=5)
    assert seen.get("creationflags", 0) & getattr(subprocess, "CREATE_NO_WINDOW", 0) ==         getattr(subprocess, "CREATE_NO_WINDOW", 0)
