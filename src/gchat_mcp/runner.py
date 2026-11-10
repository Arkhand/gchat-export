"""Run the downloader CLI as a child process (the MCP sync tool's only way to Google).

A child process keeps Google code and OAuth out of the server, and a hung download
can be killed by its timeout without taking the server down.
"""

import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CliResult:
    code: int
    stdout: str
    stderr: str


def run_cli(args: list[str], timeout: int, cmd: list[str] | None = None) -> CliResult:
    """Run `cmd + args`; raises subprocess.TimeoutExpired (child killed) on timeout.

    stdin is DEVNULL: the server's stdin is the MCP protocol stream and must never
    reach the child. Output is forced to UTF-8 so space titles survive the pipe.
    """
    cmd = cmd or [sys.executable, "-m", "gchat_mcp.cli"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    # A server started without a console (Claude desktop) would flash one per sync.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run(cmd + args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env,
                          creationflags=flags)
    return CliResult(done.returncode, done.stdout, done.stderr)
