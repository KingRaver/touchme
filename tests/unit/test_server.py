import asyncio
import base64
import io

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from PIL import Image as PILImage

from td_mcp import errors as server_errors
from td_mcp import server
from td_mcp.client import Capture, ContactCell

READ_ONLY_TOOLS = [
    "td_capture_top",
    "td_project_info",
    "td_get_errors",
    "td_snapshot_graph",
    "td_list_children",
    "td_get_operator",
    "td_get_parameters",
    "td_read_chop",
    "td_read_dat",
    "td_read_sop_info",
    "td_describe_operator_type",
    "td_search_docs",
    "td_get_performance",
]

# Tools returning a JSON-shaped result (everything but td_capture_top, which returns an MCP
# Image: there is no JSON outputSchema for that, by MCP design).
STRUCTURED_TOOLS = [t for t in READ_ONLY_TOOLS if t != "td_capture_top"]


def _list_tools():
    async def run():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    return {t.name: t for t in asyncio.run(run())}


def test_tool_is_registered_read_only():
    tools = _list_tools()
    assert "td_capture_top" in tools
    annotations = tools["td_capture_top"].annotations
    assert annotations is not None and annotations.read_only_hint is True


@pytest.mark.parametrize("name", READ_ONLY_TOOLS)
def test_every_tool_is_registered_and_marked_read_only(name):
    tools = _list_tools()
    assert name in tools
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.read_only_hint is True
    assert annotations.open_world_hint is False


@pytest.mark.parametrize("name", STRUCTURED_TOOLS)
def test_every_structured_tool_has_an_output_schema(name):
    tool = _list_tools()[name]
    schema = tool.output_schema
    assert schema is not None
    assert schema.get("type") == "object"
    assert schema.get("properties")


def test_jpg_is_labelled_image_jpeg(monkeypatch):
    jpg = b"\xff\xd8\xff\xe0fake"
    monkeypatch.setattr(
        server.BridgeClient, "capture_top",
        lambda self, path, fmt="png": Capture(data=jpg, format="jpg", width=1, height=1),
    )

    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool("td_capture_top", {"path": "/p/out", "format": "jpg"})

    image = asyncio.run(run()).content[0]
    assert image.type == "image" and image.mime_type == "image/jpeg"


def test_capture_tool_returns_an_mcp_image(monkeypatch):
    png = b"\x89PNG\r\n\x1a\nfake"
    monkeypatch.setattr(
        server.BridgeClient, "capture_top",
        lambda self, path, fmt="png": Capture(data=png, format="png", width=1, height=1),
    )

    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool("td_capture_top", {"path": "/project1/out1"})

    result = asyncio.run(run())
    image = result.content[0]
    assert image.type == "image"
    assert image.mime_type == "image/png"


def _call(name, args):
    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def test_project_info_tool_returns_structured_content(monkeypatch):
    payload = {
        "api_version": "0.1.0", "td_build": "2025.33230", "td_version": "1",
        "project_name": "show.toe", "project_folder": "/tmp", "cook_rate": 60.0,
        "timeline": {"frame": 1.0, "rate": 60.0, "start": 0.0, "end": 100.0, "play": True},
    }
    monkeypatch.setattr(server.BridgeClient, "project_info", lambda self: payload)
    result = _call("td_project_info", {})
    assert result.structured_content is not None
    assert result.structured_content == payload


def test_get_errors_tool_passes_the_path_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server.BridgeClient, "get_errors",
        lambda self, path="/": seen.update(path=path)
        or {"path": path, "status": "clean", "unread_streams": [], "errors": [], "warnings": [], "excluded": [], "caveats": []},
    )
    result = _call("td_get_errors", {"path": "/project1"})
    assert seen["path"] == "/project1"
    assert result.structured_content is not None
    assert result.structured_content["status"] == "clean"


def test_snapshot_graph_tool_passes_path_and_max_depth(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server.BridgeClient, "snapshot_graph",
        lambda self, path="/", max_depth=1: seen.update(path=path, max_depth=max_depth)
        or {"path": path, "max_depth": max_depth, "operator_count": 0, "truncated": False, "excluded": [], "operators": []},
    )
    _call("td_snapshot_graph", {"path": "/p", "max_depth": 2})
    assert seen == {"path": "/p", "max_depth": 2}


def test_list_children_tool_passes_filters_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server.BridgeClient, "list_children",
        lambda self, path="/", family=None, type=None, offset=0, limit=100: (
            seen.update(path=path, family=family, type=type, offset=offset, limit=limit)
            or {"path": path, "total": 0, "offset": offset, "limit": limit, "next_offset": None, "children": []}
        ),
    )
    _call("td_list_children", {"path": "/p", "family": "TOP"})
    assert seen == {"path": "/p", "family": "TOP", "type": None, "offset": 0, "limit": 100}


def test_get_operator_tool_passes_the_path_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server.BridgeClient, "get_operator",
        lambda self, path: seen.update(path=path)
        or {
            "path": path, "type": "constant", "family": "TOP",
            "flags": {"bypass": False, "lock": False, "selected": False, "display": True, "render": True},
            "inputs": [], "outputs": [], "non_default_parameters": [],
        },
    )
    result = _call("td_get_operator", {"path": "/p/out"})
    assert seen["path"] == "/p/out"
    assert result.structured_content is not None
    assert result.structured_content["type"] == "constant"


def test_get_parameters_tool_passes_name_pattern_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server.BridgeClient, "get_parameters",
        lambda self, path, name=None: seen.update(path=path, name=name)
        or {"path": path, "parameter_count": 0, "parameters": []},
    )
    _call("td_get_parameters", {"path": "/p/out", "name": "reso*"})
    assert seen == {"path": "/p/out", "name": "reso*"}


# --- knowledge tools ------------------------------------------------------------------------


def test_describe_operator_type_tool_leaves_absent_fields_absent(monkeypatch):
    seen = []
    reply = {
        "type": "noiseTOP", "family": "TOP", "label": "Noise", "source": "live", "note": None,
        "min_inputs": 0, "max_inputs": 2, "pages": ["Noise"], "also_creates": [],
        "parameter_count": 2, "offset": 0, "limit": 60, "next_offset": None,
        "parameters": [{"name": "seed", "default": 1.0}, {"name": "file", "default": ""}],
    }
    monkeypatch.setattr(
        server.BridgeClient, "describe_operator_type",
        lambda self, *given: seen.append(given) or reply,
    )

    result = _call("td_describe_operator_type", {"type": "noiseTOP", "page": "Noise"})

    assert seen == [("noiseTOP", None, "Noise", 0, 60)]
    assert result.structured_content == reply


def test_search_docs_tool_returns_passages_without_a_bridge(monkeypatch):
    seen = []
    reply = {
        "query": "blur", "offset": 0, "next_offset": None,
        "results": [{
            "page": "Blur_TOP", "title": "Blur TOP", "kind": "operator", "section": "Summary",
            "text": "t", "all_words": True, "url": "https://docs.derivative.ca/Blur_TOP",
        }],
    }
    monkeypatch.setattr(
        server.docs_index, "search", lambda *given: seen.append(given) or reply
    )

    result = _call("td_search_docs", {"query": "blur", "kind": "operator"})

    assert seen == [("blur", "operator", None, 0, 5)]
    assert result.structured_content == reply


def test_search_docs_tool_reports_why_it_failed(monkeypatch):
    def fail(*given):
        raise server_errors.DocsError("No TouchDesigner offline help at /x.")

    monkeypatch.setattr(server.docs_index, "search", fail)
    with pytest.raises(ToolError, match="No TouchDesigner offline help"):
        _call("td_search_docs", {"query": "blur"})


# --- construction tools ---------------------------------------------------------------------

# name -> (destructiveHint, openWorldHint)
CHANGE_TOOLS = {
    "td_create_operator": (False, False),
    "td_connect": (True, False),
    "td_disconnect": (True, False),
    "td_set_parameters": (True, True),  # an expression is Python that TouchDesigner runs
    "td_delete_operator": (True, False),
    "td_layout": (True, False),
    "td_undo": (True, False),
    "td_redo": (True, False),
    "td_timeline": (True, False),  # overwrites the playhead and range
}


def test_the_server_exposes_exactly_the_expected_tools():
    expected = set(READ_ONLY_TOOLS) | set(CHANGE_TOOLS) | {"td_capture_contact_sheet"}
    assert set(_list_tools()) == expected


def test_the_contact_sheet_is_not_annotated_read_only():
    """With frames it advances feedback loops and writes into Trail CHOPs."""
    annotations = _list_tools()["td_capture_contact_sheet"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is True
    assert annotations.open_world_hint is False


@pytest.mark.parametrize("name", CHANGE_TOOLS)
def test_change_tools_are_annotated_and_have_an_output_schema(name):
    tool = _list_tools()[name]
    assert tool.annotations is not None
    destructive, open_world = CHANGE_TOOLS[name]
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is destructive
    assert tool.annotations.open_world_hint is open_world
    assert tool.output_schema is not None and tool.output_schema.get("properties")
    assert "undo_label" in tool.output_schema["properties"] or name in ("td_undo", "td_redo")


def test_set_parameters_passes_value_types_through_unchanged(monkeypatch):
    seen = {}
    state = {"mode": "CONSTANT", "value": 1, "expr": None, "bind": None}

    def fake(self, path, parameters):
        seen.update(path=path, parameters=parameters)
        return {
            "path": path,
            "parameters": [
                {"name": "a", "previous": state, "adjusted": False, "warning": None, **state}
            ],
            "undo_label": "td-mcp x",
            "audit_warning": None,
        }

    monkeypatch.setattr(server.BridgeClient, "set_parameters", fake)
    changes = [
        {"name": "a", "value": 1},
        {"name": "b", "value": 1.5},
        {"name": "c", "value": True},
        {"name": "d", "value": "1"},
        {"name": "e", "expr": "absTime.seconds"},
        {"name": "f", "bind": "op('/p').par.X"},
    ]

    result = _call("td_set_parameters", {"path": "/p/a", "parameters": changes})

    assert seen["parameters"] == changes
    assert [type(c.get("value")) for c in seen["parameters"][:4]] == [int, float, bool, str]
    assert result.structured_content is not None
    assert result.structured_content["undo_label"] == "td-mcp x"


@pytest.mark.parametrize(
    "tool, args, method, expected",
    [
        (
            "td_create_operator",
            {"parent": "/p", "type": "noiseTOP", "name": "n"},
            "create_operator",
            ("/p", "noiseTOP", "n", None, None),
        ),
        ("td_connect", {"source": "/p/a", "target": "/p/b"}, "connect", ("/p/a", "/p/b", 0, 0)),
        ("td_disconnect", {"target": "/p/b", "target_input": 1}, "disconnect", ("/p/b", 1)),
        ("td_delete_operator", {"path": "/p/a"}, "delete_operator", ("/p/a",)),
        ("td_layout", {"path": "/p"}, "layout", ("/p",)),
        ("td_undo", {"steps": 3}, "undo", (3,)),
        ("td_redo", {}, "redo", (1,)),
    ],
)
def test_change_tools_pass_their_arguments_to_the_client(monkeypatch, tool, args, method, expected):
    seen = []

    def fake(self, *given):
        seen.append(given)
        raise server_errors.BridgeError("stop here")

    monkeypatch.setattr(server.BridgeClient, method, fake)
    with pytest.raises(ToolError, match="stop here"):
        _call(tool, args)
    assert seen == [expected]


# --- performance and data tools (Phase 4) ---------------------------------------------------


def test_get_performance_tool_passes_its_arguments_through(monkeypatch):
    seen = {}
    reply = {
        "fps": 60.0, "target_fps": 60.0, "frame_ms": 16.6, "dropped_frames": 0.0,
        "realtime": True, "cpu_mem_mb": 512.0, "gpu_mem_mb": 256.0, "gpu_mem_total_mb": 8192.0,
        "active_ops": 10.0, "deactivated_op_calls": 0.0, "total_ops": 42.0, "unread_channels": [],
        "path": "/p", "max_depth": 3, "operator_count": 1, "truncated": False, "excluded": [],
        "top_cpu_cost": [
            {"path": "/p/a", "type": "noise", "family": "TOP", "cpu_cook_ms": 1.2,
             "gpu_cook_ms": 0.1, "total_cooks": 100, "cooked_this_frame": True}
        ],
    }
    monkeypatch.setattr(
        server.BridgeClient, "get_performance",
        lambda self, path="/", top_n=20, max_depth=8: seen.update(
            path=path, top_n=top_n, max_depth=max_depth
        ) or reply,
    )
    result = _call("td_get_performance", {"path": "/p", "top_n": 5, "max_depth": 3})
    assert seen == {"path": "/p", "top_n": 5, "max_depth": 3}
    assert result.structured_content == reply


def test_read_chop_tool_passes_its_arguments_through(monkeypatch):
    seen = {}
    reply = {
        "path": "/p/c", "num_channels": 1, "matched_channels": 1, "channels_truncated": False,
        "num_samples": 10, "rate": 60.0, "start": 0, "count": 5, "next_offset": 5,
        "channels": [{"name": "chan1", "values": [0.0, 1.0, 2.0, 3.0, 4.0]}],
    }
    monkeypatch.setattr(
        server.BridgeClient, "read_chop",
        lambda self, path, channels=None, start=0, count=100: seen.update(
            path=path, channels=channels, start=start, count=count
        ) or reply,
    )
    result = _call("td_read_chop", {"path": "/p/c", "channels": "chan*", "count": 5})
    assert seen == {"path": "/p/c", "channels": "chan*", "start": 0, "count": 5}
    assert result.structured_content == reply


def test_read_dat_tool_passes_its_arguments_through(monkeypatch):
    seen = {}
    reply = {
        "path": "/p/d", "is_table": True, "num_rows": 2, "num_cols": 1, "row_offset": 0,
        "row_limit": 2, "next_row_offset": None, "columns_truncated": False,
        "rows": [["a"], ["b"]], "text": None,
    }
    monkeypatch.setattr(
        server.BridgeClient, "read_dat",
        lambda self, path, row_offset=0, row_limit=100: seen.update(
            path=path, row_offset=row_offset, row_limit=row_limit
        ) or reply,
    )
    result = _call("td_read_dat", {"path": "/p/d", "row_limit": 2})
    assert seen == {"path": "/p/d", "row_offset": 0, "row_limit": 2}
    assert result.structured_content == reply


def test_read_sop_info_tool_passes_the_path_through(monkeypatch):
    seen = {}
    reply = {
        "path": "/p/s", "num_points": 8, "num_prims": 6, "num_vertices": 24,
        "min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0], "size": [2.0, 2.0, 2.0],
        "point_groups": [], "prim_groups": [], "point_attributes": [], "prim_attributes": [],
        "vertex_attributes": [],
    }
    monkeypatch.setattr(
        server.BridgeClient, "read_sop_info",
        lambda self, path: seen.update(path=path) or reply,
    )
    result = _call("td_read_sop_info", {"path": "/p/s"})
    assert seen == {"path": "/p/s"}
    assert result.structured_content == reply


def test_timeline_tool_passes_its_arguments_through(monkeypatch):
    seen = {}
    previous = {"frame": 1.0, "rate": 60.0, "start": 0.0, "end": 100.0, "play": False,
                "range_start": 0.0, "range_end": 100.0}
    after = dict(previous, frame=50.0, play=True)
    monkeypatch.setattr(
        server.BridgeClient, "timeline",
        lambda self, play=None, frame=None, start=None, end=None: seen.update(
            play=play, frame=frame, start=start, end=end
        ) or {"previous": previous, "timeline": after, "undo_label": "td-mcp x", "audit_warning": None},
    )
    result = _call("td_timeline", {"play": True, "frame": 50})
    assert seen == {"play": True, "frame": 50, "start": None, "end": None}
    assert result.structured_content is not None
    assert result.structured_content["timeline"] == after


def _png_bytes(size=(4, 4), color=(255, 0, 0)):
    buffer = io.BytesIO()
    PILImage.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_capture_contact_sheet_tiles_cells_into_one_image(monkeypatch):
    cells = [
        ContactCell(path="/p/a", frame=None, data=_png_bytes((4, 4), (255, 0, 0)), width=4, height=4),
        ContactCell(path="/p/b", frame=None, data=_png_bytes((2, 2), (0, 255, 0)), width=2, height=2),
        ContactCell(path="/p/a", frame=1.0, data=_png_bytes((4, 4), (0, 0, 255)), width=4, height=4),
    ]
    seen = {}
    monkeypatch.setattr(
        server.BridgeClient, "contact_cells",
        lambda self, paths, frames=None, fmt="png": seen.update(
            paths=paths, frames=frames, fmt=fmt
        ) or cells,
    )

    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(
                "td_capture_contact_sheet",
                {"paths": ["/p/a", "/p/b"], "frames": [0, 1], "columns": 2},
            )

    result = asyncio.run(run())
    image_block = result.content[0]
    assert image_block.type == "image" and image_block.mime_type == "image/png"
    assert seen == {"paths": ["/p/a", "/p/b"], "frames": [0, 1], "fmt": "png"}
    tiled = PILImage.open(io.BytesIO(base64.b64decode(image_block.data)))
    # 3 cells, 2 columns -> 2 rows of 4x4 slots; the 2x2 cell is centred, not stretched.
    assert tiled.size == (8, 8)
    assert tiled.getpixel((4, 0)) == (32, 32, 32)
    assert tiled.getpixel((5, 1)) == (0, 255, 0)


def test_capture_contact_sheet_rejects_an_unopenable_cell(monkeypatch):
    cells = [ContactCell(path="/p/a", frame=None, data=b"not an image", width=1, height=1)]
    monkeypatch.setattr(
        server.BridgeClient, "contact_cells", lambda self, paths, frames=None, fmt="png": cells
    )
    with pytest.raises(ToolError):
        _call("td_capture_contact_sheet", {"paths": ["/p/a"]})
