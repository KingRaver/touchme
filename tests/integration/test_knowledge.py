"""Live knowledge checks, through the real stdio server.

Opt-in (a plain `pytest` skips it). The `search_docs` tests need only an installed
TouchDesigner (TD_APP or TD_MCP_DOCS_DIR if it is elsewhere). The `describe` tests need the
same running bridge as test_construction.py: built with `--test-fixtures`, loaded in
TouchDesigner, with TD_MCP_TOKEN_FILE set.

The describe tests make the bridge create a few ordinary operator types for an instant. The one
hardware type asked about, videodeviceinTOP, is described from the help file and never created.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

pytestmark = pytest.mark.integration


def call(tool: str, env: dict[str, str] | None = None, **args: Any) -> Any:
    async def run():
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "td_mcp.server"],
            env={**os.environ, **(env or {})},
        )
        async with Client(transport) as client:
            return await client.call_tool(tool, args, raise_on_error=False, timeout=180)

    return asyncio.run(run())


def ok(tool: str, env: dict[str, str] | None = None, **args: Any) -> dict[str, Any]:
    result = call(tool, env, **args)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def refused(tool: str, **args: Any) -> str:
    result = call(tool, **args)
    assert result.is_error, result.structured_content
    return str(getattr(result.content[0], "text", result.content[0]))


# --- documentation search -------------------------------------------------------------------


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """A fresh index for this run, so building it from the installed help is covered."""
    return {"TD_MCP_CACHE_DIR": str(tmp_path_factory.mktemp("docs-index"))}


@pytest.mark.parametrize(
    "query, page",
    [
        ("noiseTOP", "Noise_TOP"),
        ("feedback loop TOP", "Feedback_TOP"),
        ("composite operand", "Composite_TOP"),
        ("GLSL TOP uniforms", "GLSL_TOP"),
        ("Par class clampMax", "Par_Class"),
        ("instancing geometry", "Geometry_COMP"),
    ],
)
def test_search_docs_puts_the_expected_page_first(cache, query, page):
    results = ok("td_search_docs", cache, query=query)["results"]
    assert results[0]["page"] == page, [hit["page"] for hit in results]
    assert results[0]["url"] == "https://docs.derivative.ca/" + page


def test_search_docs_within_a_page_and_built_one_index(cache):
    found = ok("td_search_docs", cache, query="noise type", page="noiseTOP", limit=20)
    assert {hit["page"] for hit in found["results"]} == {"Noise_TOP"}
    assert any("simplex3d" in hit["text"] for hit in found["results"])
    assert [p.name for p in Path(cache["TD_MCP_CACHE_DIR"]).iterdir()] == ["docs-index.sqlite"]


def test_search_docs_keeps_inherited_members_on_the_parent_page(cache):
    # create() is a COMP method: noise TOP's class page must not repeat any parent's members.
    own = ok("td_search_docs", cache, query="saveByteArray", page="NoiseTOP_Class")
    assert own["results"] == []
    parent = ok("td_search_docs", cache, query="saveByteArray", page="TOP_Class")
    assert parent["results"]


def test_search_docs_has_no_glossary_popups_in_passages(cache):
    found = ok("td_search_docs", cache, query="Container COMP info channels", limit=20)
    assert not any("An Operator Family that" in hit["text"] for hit in found["results"]
                   if hit["page"] == "Container_COMP")


# --- operator types from the running build --------------------------------------------------


@pytest.fixture(scope="module")
def bridge() -> str:
    """The bridge's own COMP path."""
    if not os.environ.get("TD_MCP_TOKEN_FILE") and not os.environ.get("TD_MCP_TOKEN"):
        pytest.fail("Set TD_MCP_TOKEN_FILE (see this file's docstring). Not skipping on purpose.")
    from td_mcp.client import BridgeClient

    return BridgeClient().ping()["bridge_path"]


def children(bridge: str) -> list[str]:
    return sorted(c["path"] for c in ok("td_list_children", path=bridge, limit=500)["children"])


def by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in result["parameters"]}


def test_describe_reads_a_type_and_leaves_nothing_behind(bridge):
    before = children(bridge)
    result = ok("td_describe_operator_type", type="noiseTOP", limit=250)
    assert children(bridge) == before
    assert (result["source"], result["family"], result["label"]) == ("live", "TOP", "Noise")
    assert result["parameter_count"] == len(result["parameters"])
    assert result["max_inputs"] is not None and "Noise" in result["pages"]
    pars = by_name(result)
    assert "pageindex" not in pars
    assert pars["type"]["default"] in pars["type"]["menu_names"]
    assert pars["resolutionw"]["min"] == 1 and "max" not in pars["resolutionw"]


def test_described_names_are_the_ones_a_real_operator_has(bridge):
    described = ok("td_describe_operator_type", type="constantCHOP", limit=250)
    made = ok("td_create_operator", parent="/", type="constantCHOP", name="td_mcp_describe_check")
    try:
        real = ok("td_get_parameters", path=made["path"])["parameters"]
    finally:
        ok("td_delete_operator", path=made["path"])
    real_names = {p["name"] for p in real} - {"pageindex"}
    assert set(by_name(described)) == real_names
    assert by_name(described)["const0name"]["sequence"] == "const"


def test_describe_leaves_undo_and_redo_alone(bridge):
    made = ok("td_create_operator", parent="/", type="baseCOMP", name="td_mcp_describe_check")
    ok("td_undo")
    ok("td_describe_operator_type", type="annotateCOMP")
    ok("td_describe_operator_type", type="glslTOP")
    redone = ok("td_redo")
    assert redone["done"] == [made["undo_label"]]
    ok("td_undo")


def test_describe_filters_and_pages(bridge):
    transform = ok("td_describe_operator_type", type="noiseTOP", page="transform", limit=2)
    assert {p["page"] for p in transform["parameters"]} == {"Transform"}
    assert transform["next_offset"] == 2
    named = ok("td_describe_operator_type", type="noiseTOP", name="resolution?")
    assert set(by_name(named)) == {"resolutionw", "resolutionh"}
    assert named["parameters"][0]["help"]


def test_describe_lists_what_comes_with_a_type(bridge):
    assert "textDAT" in ok("td_describe_operator_type", type="glslTOP")["also_creates"]


def test_describe_does_not_create_a_camera(bridge):
    result = ok("td_describe_operator_type", type="videodeviceinTOP")
    assert result["source"] == "help_file", result
    assert result["parameters"] and "no operator was created" in result["note"]


def test_describe_refuses_unknown_types_with_suggestions(bridge):
    assert "noiseTOP" in refused("td_describe_operator_type", type="noise")
