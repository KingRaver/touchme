"""MCP server (stdio). Phase 0 spike: one tool, `td_capture_top`."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from td_mcp.client import BridgeClient

mcp = FastMCP("td-mcp")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def td_capture_top(path: str, format: Literal["png", "jpg"] = "png") -> Image:
    """Render one TOP to an image Claude can see.

    path: absolute operator path of a TOP, for example "/project1/out1".
    format: "png" (lossless) or "jpg" (smaller).
    """
    capture = BridgeClient().capture_top(path, format)
    return Image(data=capture.data, format=capture.format)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
