"""MCP server (stdio): perception, construction and knowledge tools."""

from __future__ import annotations

from typing import Literal, cast

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from td_mcp import docs_index, schemas
from td_mcp.client import BridgeClient

mcp = FastMCP("td-mcp")

_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
# Adds to the project without removing or overwriting anything.
_ADDITIVE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
# Removes or overwrites something. All of these can be reversed with td_undo.
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
    name: optional parameter name pattern, for example "resolution*". Filtering by name also
    returns full help text and menu labels.
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
    `adjusted` marks a number TouchDesigner clamped. CHOP and DAT exports cannot be created,
    and pulse buttons cannot be pressed.
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
