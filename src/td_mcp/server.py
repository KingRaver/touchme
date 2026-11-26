"""MCP server (stdio). Phase 1: core and perception (read-only tools)."""

from __future__ import annotations

from typing import Literal, cast

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from td_mcp import schemas
from td_mcp.client import BridgeClient

mcp = FastMCP("td-mcp")

_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


@mcp.tool(annotations=_READ_ONLY)
def td_capture_top(path: str, format: Literal["png", "jpg"] = "png") -> Image:
    """Render one TOP to an image Claude can see.

    path: absolute operator path of a TOP, for example "/project1/out1".
    format: "png" (lossless) or "jpg" (smaller).
    """
    capture = BridgeClient().capture_top(path, format)
    return Image(data=capture.data, format=capture.format)


@mcp.tool(annotations=_READ_ONLY)
def td_project_info() -> schemas.ProjectInfo:
    """Build, project name and folder, cook rate (FPS), and timeline state."""
    return cast(schemas.ProjectInfo, BridgeClient().project_info())


@mcp.tool(annotations=_READ_ONLY)
def td_get_errors(path: str = "/") -> schemas.ErrorReport:
    """Errors and warnings across a subtree, one row per message.

    path: absolute path of the subtree root, for example "/project1". Defaults to the whole
    project ("/"). Reads both the errors and warnings streams; if either cannot be read, the
    report's status is "incomplete" rather than a false all-clear. `caveats` always notes that
    an exception inside a Script operator's own callback is invisible on either stream unless
    the callback catches it and calls addError() — a "clean" status does not rule that out.
    A report on "/" leaves out TouchDesigner's own "/ui" and "/sys" networks and lists them in
    `excluded`; pass either path to see inside it.
    """
    return cast(schemas.ErrorReport, BridgeClient().get_errors(path))


@mcp.tool(annotations=_READ_ONLY)
def td_snapshot_graph(path: str = "/", max_depth: int = 1) -> schemas.GraphSnapshot:
    """Compact description of a network: operators, types, connections and flags.

    path: absolute path of the root to describe, for example "/project1". Defaults to "/".
    max_depth: how many levels of children to include below the root (0 = root only), capped
    at 8. At most 1000 operators are returned; `truncated` says when that cut the list short.
    A snapshot of "/" leaves out TouchDesigner's own "/ui" and "/sys" networks and lists them
    in `excluded`; pass either path to see inside it.
    """
    return cast(schemas.GraphSnapshot, BridgeClient().snapshot_graph(path, max_depth))


@mcp.tool(annotations=_READ_ONLY)
def td_list_children(
    path: str = "/",
    family: str | None = None,
    type: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> schemas.ChildrenPage:
    """Direct children of a COMP, paginated and optionally filtered.

    path: absolute path of the parent COMP, for example "/project1". Defaults to "/".
    family: optional filter, for example "TOP", "CHOP", "DAT", "SOP", "COMP", "MAT".
    type: optional filter on the operator type name, for example "constant" or "noise".
    offset/limit: page through a large network. limit is capped at 500; the response reports
    the limit applied and `next_offset` for the following page (null on the last page).
    """
    return cast(schemas.ChildrenPage, BridgeClient().list_children(path, family, type, offset, limit))


@mcp.tool(annotations=_READ_ONLY)
def td_get_operator(path: str) -> schemas.OperatorDetail:
    """Type, flags, inputs/outputs and non-default parameters for one operator.

    path: absolute operator path, for example "/project1/out1". Must match exactly; wildcard
    paths are refused because they resolve to an arbitrary operator, not a chosen one.
    A parameter's `min`/`max` are clamp limits, null on a side where the value is not clamped.
    """
    return cast(schemas.OperatorDetail, BridgeClient().get_operator(path))


@mcp.tool(annotations=_READ_ONLY)
def td_get_parameters(path: str, name: str | None = None) -> schemas.ParameterList:
    """Full parameter detail for one operator (value, expression, mode, clamp limits, menu items).

    path: absolute operator path, for example "/project1/out1".
    name: optional pattern to filter parameter names, for example "t?" for tx/ty/tz.
    `min`/`max` are clamp limits, null on a side where the value is not clamped.
    """
    return cast(schemas.ParameterList, BridgeClient().get_parameters(path, name))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
