"""MCP server (stdio): perception, construction and knowledge tools."""

from __future__ import annotations

import io
import math
from typing import Literal, cast

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from PIL import Image as PILImage

from td_mcp import docs_index, schemas
from td_mcp.client import BridgeClient, ContactCell

mcp = FastMCP("td-mcp")

_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
# Adds to the project without removing or overwriting anything.
_ADDITIVE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
# Removes or overwrites something. td_undo reverses these, except what
# td_capture_contact_sheet changes inside a stateful network.
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
# A parameter expression is Python that TouchDesigner runs, so it can reach outside the project.
_SETS_EXPRESSIONS = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}


@mcp.tool(annotations=_READ_ONLY)
def td_capture_top(path: str, format: Literal["png", "jpg"] = "png") -> Image:
    """Render one TOP to an image Claude can see.

    path: absolute operator path of a TOP, for example "/project1/out1".
    format: "png" (lossless) or "jpg" (smaller).
    """
    capture = BridgeClient().capture_top(path, format)
    return Image(data=capture.data, format=capture.format)


def _tile_cells(cells: list[ContactCell], format: str, columns: int | None) -> Image:
    """Tile the cells into a grid, each centred in a slot of the largest width and height.
    Cells are never resized, so their shapes are not distorted."""
    opened = [(cell, PILImage.open(io.BytesIO(cell.data)).convert("RGB")) for cell in cells]
    cell_w = max(img.width for _cell, img in opened)
    cell_h = max(img.height for _cell, img in opened)
    cols = max(1, min(columns or math.ceil(math.sqrt(len(opened))), len(opened)))
    rows = math.ceil(len(opened) / cols)
    sheet = PILImage.new("RGB", (cell_w * cols, cell_h * rows), (32, 32, 32))
    for index, (_cell, img) in enumerate(opened):
        left = (index % cols) * cell_w + (cell_w - img.width) // 2
        top = (index // cols) * cell_h + (cell_h - img.height) // 2
        sheet.paste(img, (left, top))
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG" if format == "png" else "JPEG")
    return Image(data=buffer.getvalue(), format=format)


@mcp.tool(annotations=_DESTRUCTIVE)
def td_capture_contact_sheet(
    paths: list[str],
    frames: list[float] | None = None,
    format: Literal["png", "jpg"] = "png",
    columns: int | None = None,
) -> Image:
    """Several TOPs, or one TOP at several timeline frames, tiled into one image to compare
    variations cheaply.

    paths: 1 to 16 absolute TOP paths. Without `frames`, each is captured at the current frame.
    frames: optional, at most 12 whole frame numbers inside the timeline's playback range. Every
    path is captured at every frame (cells go left to right, top to bottom, path by path), then
    the timeline is put back. Cells follow what is computed from the timeline frame
    (expressions on me.time.frame, a Timeline CHOP); absTime, time-sliced Noise CHOPs and LFOs
    do not follow. With `frames` the call can change stateful networks it cooks, even when it
    is then refused, and td_undo does not reverse that: a feedback loop gives identical cells
    and advances one step, and a Trail CHOP records the requested frames into its history.
    Without `frames` nothing was seen to change.
    format: "png" or "jpg". A TOP larger than 480x480 pixels in area is scaled down. Cells
    keep their shape; smaller ones sit centred on a dark grey background.
    columns: optional grid width. Defaults to a roughly square grid.
    """
    return _tile_cells(BridgeClient().contact_cells(paths, frames, format), format, columns)


@mcp.tool(annotations=_READ_ONLY)
def td_project_info() -> schemas.ProjectInfo:
    """Build, project name and folder, cook rate (FPS), and timeline state."""
    return cast(schemas.ProjectInfo, BridgeClient().project_info())


@mcp.tool(annotations=_DESTRUCTIVE)
def td_timeline(
    play: bool | None = None,
    frame: float | None = None,
    start: float | None = None,
    end: float | None = None,
) -> schemas.TimelineChanged:
    """Play, pause, jump to a frame, or set the range of the project's root timeline.

    Give any combination of the four; at least one is required. Frame numbers are whole.
    start and end set both the timeline's range and its playback range (`range_start`,
    `range_end`); a frame outside the playback range is refused, and a new range that leaves
    the playhead outside moves it to the range's first frame. td_undo reverses the change.
    """
    return cast(
        schemas.TimelineChanged, BridgeClient().timeline(play=play, frame=frame, start=start, end=end)
    )


@mcp.tool(annotations=_READ_ONLY)
def td_get_performance(
    path: str = "/", top_n: int = 20, max_depth: int = 8
) -> schemas.PerformanceReport:
    """FPS, cook time per operator (top N by CPU time), GPU/CPU memory and dropped frames.

    path: subtree to rank by cook time, for example "/project1". Defaults to "/", which leaves
    out TouchDesigner's own "/ui" and "/sys" networks (listed in `excluded`); pass either path
    directly to see inside it.
    top_n: how many of the most expensive operators to return, capped at 200.
    max_depth: how many levels below `path` to scan for the ranking, capped at 8.
    Process-wide numbers (fps, memory, dropped frames) come from a Perform CHOP the bridge
    creates, reads and destroys for this call; nothing is left in the project.
    """
    return cast(
        schemas.PerformanceReport, BridgeClient().get_performance(path, top_n, max_depth)
    )


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
    name: optional names or patterns separated by spaces, for example "t?" for tx/ty/tz, or
    "seed period". `min`/`max` are clamp limits, null on a side where the value is not clamped.
    A value that is not a finite number is the text "nan", "inf" or "-inf".
    """
    return cast(schemas.ParameterList, BridgeClient().get_parameters(path, name))


@mcp.tool(annotations=_READ_ONLY)
def td_read_chop(
    path: str, channels: str | None = None, start: int = 0, count: int = 100
) -> schemas.ChopSamples:
    """Sample values from a CHOP's channels, with channel and sample limits to protect context.

    path: absolute operator path of a CHOP.
    channels: optional names or patterns separated by spaces, for example "tx ty" or "chan*".
    start/count: the sample range to return, from 0. count is capped at 2000, and at 8000
    values across the channels returned; `next_offset` gives the next page's start. Values that
    are not finite come back as "nan", "inf" or "-inf".
    """
    return cast(schemas.ChopSamples, BridgeClient().read_chop(path, channels, start, count))


@mcp.tool(annotations=_READ_ONLY)
def td_read_dat(path: str, row_offset: int = 0, row_limit: int = 100) -> schemas.DatTable:
    """Rows from a DAT table, or the text of a non-table DAT, with limits to protect context.

    path: absolute operator path of a DAT.
    row_offset/row_limit: page through a large table (limit capped at 500, and fewer when the
    cells are long; `next_row_offset` gives the next page's offset). Columns beyond 64 are left
    out (`columns_truncated`); each cell, and a non-table DAT's `text`, are clipped.
    """
    return cast(schemas.DatTable, BridgeClient().read_dat(path, row_offset, row_limit))


@mcp.tool(annotations=_READ_ONLY)
def td_read_sop_info(path: str) -> schemas.SopInfo:
    """Point/primitive counts, bounds and attribute names for a SOP; not its full geometry.

    path: absolute operator path of a SOP.
    Position (P) is always present and is not listed in `point_attributes`. TouchDesigner's
    internal "__..." groups are left out.
    """
    return cast(schemas.SopInfo, BridgeClient().read_sop_info(path))


@mcp.tool(annotations=_READ_ONLY)
def td_describe_operator_type(
    type: str,
    name: str | None = None,
    page: str | None = None,
    offset: int = 0,
    limit: int = 60,
) -> schemas.OperatorTypeDescription:
    """Parameters of an operator type, read from the running TouchDesigner. Use it before
    setting parameters, so names and menu entries are real ones.

    type: operator type with its family, for example "noiseTOP". An unknown type is refused
    with the closest valid names.
    name: optional parameter names or patterns separated by spaces, for example "resolution*"
    or "seed period". Filtering by name also returns full help text and menu labels.
    page: optional parameter page, for example "Transform"; the reply lists the `pages`.
    offset/limit: page through the parameters. limit is capped at 250.
    The bridge reads the type from a temporary operator that it removes at once. Hardware types
    (cameras, sensors, audio, MIDI, serial and so on) are never created; they are described
    with less detail from the build's help file, as `source` and `note` say.
    """
    result = BridgeClient().describe_operator_type(type, name, page, offset, limit)
    return cast(schemas.OperatorTypeDescription, result)


@mcp.tool(annotations=_READ_ONLY)
def td_search_docs(
    query: str,
    kind: Literal["operator", "class", "article"] | None = None,
    page: str | None = None,
    offset: int = 0,
    limit: int = 5,
) -> schemas.DocSearchResult:
    """Search TouchDesigner's offline documentation: operator reference, Python classes and
    articles. TouchDesigner does not need to be running.

    query: words to find, for example "feedback TOP" or "Par class clampMax".
    kind: optional filter: "operator" reference pages, Python "class" pages, or "article".
    page: optional, to search one page, for example "Noise_TOP" or "OP_Class". A class page
    holds only that class's own members; inherited ones are on the parent class's page.
    offset/limit: page through the passages. limit is capped at 20.
    The first search after installing or updating TouchDesigner takes several seconds.
    For exact parameter names and menu entries use td_describe_operator_type.
    """
    result = docs_index.search(query, kind, page, offset, limit)
    return cast(schemas.DocSearchResult, result)


@mcp.tool(annotations=_ADDITIVE)
def td_create_operator(
    parent: str,
    type: str,
    name: str | None = None,
    x: int | None = None,
    y: int | None = None,
) -> schemas.CreatedOperator:
    """Create one operator inside a COMP, as a single undo step.

    parent: absolute path of the COMP to create in, for example "/project1".
    type: operator type with its family, for example "noiseTOP", "constantCHOP", "baseCOMP".
    An unknown type is refused with the closest valid names.
    name: optional; letters, digits and underscores. If taken, TouchDesigner picks another:
    always use the returned `path`.
    x, y: optional node position (give both). By default it goes right of the existing nodes.
    """
    result = BridgeClient().create_operator(parent, type, name, x, y)
    return cast(schemas.CreatedOperator, result)


@mcp.tool(annotations=_DESTRUCTIVE)
def td_connect(
    source: str, target: str, source_output: int = 0, target_input: int = 0
) -> schemas.Connection:
    """Wire an output of one operator into an input of another in the same network.

    source, target: absolute operator paths, for example "/project1/noise1".
    source_output, target_input: connector numbers, from 0. Whatever was on that input is
    replaced (listed in `replaced`). To add an input to a multi-input operator such as a
    Composite TOP, use the next free number. Does not set up 3D or panel parenting between
    COMPs.
    """
    result = BridgeClient().connect(source, target, source_output, target_input)
    return cast(schemas.Connection, result)


@mcp.tool(annotations=_DESTRUCTIVE)
def td_disconnect(target: str, target_input: int = 0) -> schemas.Disconnection:
    """Remove the wire on one input of an operator.

    target: absolute path of the operator whose input is disconnected.
    target_input: connector number, from 0. On multi-input operators the later inputs move up,
    so read `inputs` in the reply before disconnecting another.
    """
    result = BridgeClient().disconnect(target, target_input)
    return cast(schemas.Disconnection, result)


@mcp.tool(annotations=_SETS_EXPRESSIONS)
def td_set_parameters(
    path: str, parameters: list[schemas.ParameterChange]
) -> schemas.ParametersChanged:
    """Set several parameters of one operator as one atomic undo step.

    path: absolute operator path.
    parameters: each is {"name": ...} plus exactly one of:
      "value": a constant, for example {"name": "resolutionw", "value": 1280}. Replaces any
        expression or bind on that parameter. Menus take a menu name (see td_get_parameters).
      "expr": a Python expression TouchDesigner evaluates, for example "absTime.seconds".
      "bind": a bind reference, for example "op('/project1/base1').par.Speed".
    If any change is invalid, or an expression or bind does not work, none are applied.
    `adjusted` marks a number TouchDesigner clamped. An expression or bind that evaluates to
    NaN or infinity is applied, with a `warning`; such values read as "nan", "inf" or "-inf".
    CHOP and DAT exports cannot be created, and pulse buttons cannot be pressed.
    """
    changes = [dict(change) for change in parameters]
    return cast(schemas.ParametersChanged, BridgeClient().set_parameters(path, changes))


@mcp.tool(annotations=_DESTRUCTIVE)
def td_delete_operator(path: str) -> schemas.DeletedOperator:
    """Delete one operator and everything inside it. Reversible with td_undo.

    path: exact absolute path. Wildcards are refused. The bridge, anything containing it, and
    TouchDesigner's /ui, /sys and /local networks cannot be deleted. TouchDesigner may wire
    the deleted operator's source straight into the operators it fed: check `downstream`.
    """
    return cast(schemas.DeletedOperator, BridgeClient().delete_operator(path))


@mcp.tool(annotations=_DESTRUCTIVE)
def td_layout(path: str) -> schemas.LayoutResult:
    """Arrange the direct children of a COMP left to right by signal flow, as one undo step.

    path: absolute path of the COMP, for example "/project1". Overwrites node positions.
    Docked operators move with their host; annotation boxes are not moved.
    """
    return cast(schemas.LayoutResult, BridgeClient().layout(path))


@mcp.tool(annotations=_DESTRUCTIVE)
def td_undo(steps: int = 1) -> schemas.HistoryResult:
    """Undo the most recent changes made through this server, newest first.

    steps: 1 to 20. Stops at the first undo entry this server did not make (for example an edit
    made by hand in TouchDesigner), and never undoes it.
    """
    return cast(schemas.HistoryResult, BridgeClient().undo(steps))


@mcp.tool(annotations=_DESTRUCTIVE)
def td_redo(steps: int = 1) -> schemas.HistoryResult:
    """Redo changes undone with td_undo.

    steps: 1 to 20. A new change made after an undo discards what could be redone.
    """
    return cast(schemas.HistoryResult, BridgeClient().redo(steps))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
