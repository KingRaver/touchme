"""Typed shapes for tool outputs. FastMCP derives each tool's MCP `outputSchema` from these."""

from __future__ import annotations

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
    min: float | None
    max: float | None
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
