"""Typed shapes for tool outputs. FastMCP derives each tool's MCP `outputSchema` from these."""

from __future__ import annotations

from typing import NotRequired

# pydantic (via FastMCP's output-schema generation) requires typing_extensions.TypedDict on
# Python < 3.12, not typing.TypedDict, to see required/not-required field metadata.
from typing_extensions import TypedDict


class TimelineState(TypedDict):
    frame: float | None
    rate: float | None
    start: float | None
    end: float | None
    play: bool | None


class ProjectInfo(TypedDict):
    api_version: str
    td_build: str
    td_version: str
    project_name: str
    project_folder: str
    cook_rate: float
    timeline: TimelineState | None


class ErrorRow(TypedDict):
    path: str
    message: str


class ErrorReport(TypedDict):
    path: str
    status: str  # "clean" | "has_issues" | "incomplete"
    unread_streams: list[str]
    errors: list[ErrorRow]
    warnings: list[ErrorRow]
    excluded: list[str]  # subtrees left out of a "/" report; empty for any other path
    caveats: list[str]


class OperatorFlags(TypedDict):
    bypass: bool
    lock: bool
    selected: bool
    display: bool
    render: bool


class GraphOperator(TypedDict):
    path: str
    type: str | None
    family: str | None
    flags: OperatorFlags
    inputs: list[str]
    outputs: list[str]


class GraphSnapshot(TypedDict):
    path: str
    max_depth: int
    operator_count: int
    truncated: bool  # True when the operator cap cut the list short
    excluded: list[str]  # subtrees left out of a "/" snapshot; empty for any other path
    operators: list[GraphOperator]


class ChildSummary(TypedDict):
    path: str
    type: str | None
    family: str | None


class ChildrenPage(TypedDict):
    path: str
    total: int
    offset: int
    limit: int  # the page size actually applied (requests are capped)
    next_offset: int | None  # pass as offset for the next page; None on the last page
    children: list[ChildSummary]


class ParameterDetail(TypedDict):
    name: str
    value: float | int | str | bool | None
    expr: str | None
    mode: str | None
    is_default: bool | None
    readable: bool  # False when evaluating the parameter raised
    min: float | None  # clamp limit; None when the value is not clamped below
    max: float | None  # clamp limit; None when the value is not clamped above
    menu_names: list[str] | None
    menu_labels: list[str] | None


class OperatorDetail(TypedDict):
    path: str
    type: str | None
    family: str | None
    flags: OperatorFlags
    inputs: list[str]
    outputs: list[str]
    non_default_parameters: list[ParameterDetail]


class ParameterList(TypedDict):
    path: str
    parameter_count: int
    parameters: list[ParameterDetail]


class TypeParameter(TypedDict):
    """A parameter of an operator type. Fields that do not apply are left out."""

    name: str
    label: NotRequired[str]
    style: NotRequired[str]  # for example "Float", "Menu", "Toggle", "TOP"
    page: NotRequired[str]
    default: NotRequired[float | int | str | bool | None]
    default_expr: NotRequired[str]  # set when the default is an expression
    min: NotRequired[float]  # clamp limit
    max: NotRequired[float]  # clamp limit
    slider_range: NotRequired[list[float]]  # the UI slider's range; values may lie outside it
    menu_names: NotRequired[list[str]]
    menu_labels: NotRequired[list[str]]  # only when the call filters by name
    help: NotRequired[str]  # shortened unless the call filters by name
    read_only: NotRequired[bool]
    custom: NotRequired[bool]
    sequence: NotRequired[str]  # repeating block, such as "const" for const0name, const1name


class OperatorTypeDescription(TypedDict):
    type: str
    family: str
    label: str | None
    source: str  # "live" | "help_file"
    note: str | None  # why the description is partial, and what to do instead
    min_inputs: int | None
    max_inputs: int | None  # 9999 on a Composite TOP, which takes any number
    pages: list[str]
    also_creates: list[str]  # types TouchDesigner adds alongside, such as docked DATs
    parameter_count: int  # parameters matching the filters, across all pages of results
    offset: int
    limit: int  # the page size actually applied (requests are capped)
    next_offset: int | None
    parameters: list[TypeParameter]


class DocPassage(TypedDict):
    page: str  # pass as `page` to search within it
    title: str
    kind: str  # "operator" | "class" | "article"
    section: str  # heading path, empty for a page's opening text
    text: str
    all_words: bool  # False when the passage lacks some of the query's words
    url: str


class DocSearchResult(TypedDict):
    query: str
    offset: int
    next_offset: int | None
    results: list[DocPassage]


class ParameterChange(TypedDict):
    """One input to td_set_parameters: a name plus exactly one of value, expr or bind."""

    name: str
    value: NotRequired[bool | int | float | str]
    expr: NotRequired[str]
    bind: NotRequired[str]


class ChangeResult(TypedDict):
    undo_label: str | None  # the TouchDesigner undo entry holding this change
    audit_warning: str | None  # set when the change was applied but could not be logged


class CreatedOperator(ChangeResult):
    path: str
    type: str
    family: str
    requested_name: str | None
    renamed: bool  # True when the requested name was taken and TouchDesigner chose another
    x: int
    y: int
    also_created: list[str]  # operators TouchDesigner adds alongside, such as docked DATs


class Connection(ChangeResult):
    source: str
    target: str
    source_output: int
    target_input: int
    replaced: list[str]  # sources that were on this input before
    inputs: list[str]  # the target's inputs now


class Disconnection(ChangeResult):
    target: str
    target_input: int
    removed: list[str]
    inputs: list[str]  # the target's inputs now; later inputs may have moved up


class ParameterState(TypedDict):
    mode: str  # "CONSTANT" | "EXPRESSION" | "BIND" | "EXPORT"
    value: float | int | str | bool | None
    expr: str | None
    bind: str | None


class ChangedParameter(ParameterState):
    name: str
    previous: ParameterState
    adjusted: bool  # True when TouchDesigner stored a different number than requested (clamping)
    warning: str | None


class ParametersChanged(ChangeResult):
    path: str
    parameters: list[ChangedParameter]


class Wiring(TypedDict):
    path: str
    inputs: list[str]


class DeletedOperator(ChangeResult):
    deleted: str
    type: str
    family: str
    descendants: int
    disconnected_inputs: list[str]
    disconnected_outputs: list[str]
    downstream: list[Wiring]  # what each former output is wired to now
    also_deleted: list[str]  # operators docked to the deleted one


class NodePosition(TypedDict):
    path: str
    x: int
    y: int


class LayoutResult(ChangeResult):
    path: str
    operators: list[NodePosition]
    skipped: list[str]  # annotations and protected operators, which are not moved


class HistoryResult(TypedDict):
    action: str  # "undo" | "redo"
    done: list[str]  # undo entries stepped through, in order
    stopped: str | None  # why fewer steps than requested were taken
    next: str | None  # the server-made entry that another call would take, if any
    audit_warning: str | None
