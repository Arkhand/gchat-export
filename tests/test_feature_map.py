"""docs/FEATURE_MAP.md must list exactly the tools the server exposes.

If this fails, you added/renamed/removed a tool without updating the map. Fix the map.
"""

import asyncio
import re
from pathlib import Path

from gchat_mcp.server import build_server

MAP = Path(__file__).resolve().parents[1] / "docs" / "FEATURE_MAP.md"


def tools_in_map() -> set[str]:
    section = MAP.read_text(encoding="utf-8").split("## Tools", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.MULTILINE))


def test_feature_map_lists_exactly_the_server_tools(tmp_path):
    served = {t.name for t in asyncio.run(build_server(str(tmp_path / "gchat.db")).list_tools())}
    assert tools_in_map() == served
