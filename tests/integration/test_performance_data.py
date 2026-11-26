"""Live checks for the performance, data, timeline and contact-sheet tools: real stdio server
-> bridge in TouchDesigner.

Opt-in (a plain `pytest` skips it). Same setup as test_construction.py: a bridge built with
`python3 bridge/build_spike.py --test-fixtures` loaded in TouchDesigner, and TD_MCP_TOKEN_FILE
set. Run with TouchDesigner in front.

Everything happens inside one new COMP at the project root, which is deleted at the end. The
timeline is moved during the run and put back to frame, range 1-600 and its play state. The
changes stay in TouchDesigner's undo history.
"""

import asyncio
import base64
import io
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from PIL import Image, ImageChops, ImageStat

from td_mcp.client import BridgeClient

pytestmark = pytest.mark.integration


class Refused(Exception):
    pass


def call(tool: str, **args: Any) -> Any:
    """The tool's structured result, or its image."""
    if not os.environ.get("TD_MCP_TOKEN_FILE") and not os.environ.get("TD_MCP_TOKEN"):
        pytest.fail("Set TD_MCP_TOKEN_FILE (see this file's docstring). Not skipping on purpose.")

    async def run():
        transport = StdioTransport(
            command=sys.executable, args=["-m", "td_mcp.server"], env=dict(os.environ)
        )
        async with Client(transport) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    result = asyncio.run(run())
    if result.is_error:
        raise Refused(str(getattr(result.content[0], "text", result.content[0])))
    if result.structured_content is not None:
        return result.structured_content
    data = base64.b64decode(getattr(result.content[0], "data", ""))
    return Image.open(io.BytesIO(data)).convert("RGB")


def refused(tool: str, **args: Any) -> str:
    with pytest.raises(Refused) as caught:
        call(tool, **args)
    return str(caught.value)


def new(parent: str, optype: str, name: str) -> str:
    return call("td_create_operator", parent=parent, type=optype, name=name)["path"]


def put(path: str, **values: Any) -> None:
    """Set parameters; a value written "expr:..." is set as an expression."""
    changes = [
        {"name": n, "expr": v[5:]}
        if isinstance(v, str) and v.startswith("expr:")
        else {"name": n, "value": v}
        for n, v in values.items()
    ]
    call("td_set_parameters", path=path, parameters=changes)


def wire(source: str, target: str, index: int = 0) -> None:
    call("td_connect", source=source, target=target, target_input=index)


def flat(box: str, name: str, width: int = 32, height: int = 32, **values: Any) -> str:
    path = new(box, "constantTOP", name)
    put(path, outputresolution="custom", resolutionw=width, resolutionh=height, **values)
    return path


def gray(frame_expr: str) -> dict[str, Any]:
    return {name: "expr:" + frame_expr for name in ("colorr", "colorg", "colorb")}


def reds(sheet: Any, cells: int, size: int = 32) -> list[int]:
    return [sheet.getpixel((size // 2 + size * i, size // 2))[0] for i in range(cells)]


def timeline() -> dict[str, Any]:
    return call("td_project_info")["timeline"]


@pytest.fixture(scope="module", autouse=True)
def restore_timeline() -> Iterator[None]:
    before = timeline()
    call("td_timeline", start=1, end=600, play=False)
    yield
    call(
        "td_timeline", start=1, end=600, frame=min(max(int(before["frame"]), 1), 600),
        play=before["play"],
    )


@pytest.fixture
def box() -> Iterator[str]:
    path = new("/", "baseCOMP", "td_mcp_data_test")
    yield path
    call("td_delete_operator", path=path)


# --- performance ----------------------------------------------------------------------------


def test_every_process_wide_value_is_read(box):
    report = call("td_get_performance", path=box)

    assert report["unread_channels"] == []
    for key in (
        "fps", "target_fps", "frame_ms", "dropped_frames", "realtime", "cpu_mem_mb",
        "gpu_mem_mb", "gpu_mem_total_mb", "active_ops", "deactivated_op_calls", "total_ops",
    ):
        assert report[key] is not None, key
    assert report["cpu_mem_mb"] > 0 and report["gpu_mem_total_mb"] > report["gpu_mem_mb"] > 0
    assert report["total_ops"] >= report["active_ops"] > 0


def test_ranking_is_sorted_and_the_project_is_left_alone(box):
    top = new(box, "noiseTOP", "n")
    call("td_capture_top", path=top)
    before = call("td_list_children", path="/td_mcp_bridge")

    report = call("td_get_performance", path=box, top_n=2)

    costs = [row["cpu_cook_ms"] for row in report["top_cpu_cost"]]
    assert costs == sorted(costs, reverse=True) and costs[0] > 0
    assert report["top_cpu_cost"][0]["path"] == top
    assert call("td_list_children", path="/td_mcp_bridge") == before
    # No undo entry of its own: the next undo is still the last real change.
    assert "create noiseTOP" in call("td_undo")["done"][0]


def test_the_project_root_leaves_out_the_system_networks():
    report = call("td_get_performance")
    assert report["excluded"] == ["/ui", "/sys"]
    assert not any(row["path"].startswith(("/ui/", "/sys/")) for row in report["top_cpu_cost"])


# --- CHOP, DAT and SOP readers ----------------------------------------------------------------


def test_chop_patterns_paging_and_the_value_budget(box):
    chop = new(box, "noiseCHOP", "many")
    put(chop, channelname="c[1-80]")

    named = call("td_read_chop", path=chop, channels="c1 c3", count=2)
    assert [c["name"] for c in named["channels"]] == ["c1", "c3"]

    wide = call("td_read_chop", path=chop, count=2000)
    assert wide["num_channels"] == 80 and wide["channels_truncated"]
    assert len(wide["channels"]) == 64 and wide["count"] == 125
    assert wide["next_offset"] == 125

    last = call("td_read_chop", path=chop, channels="c1", start=10_000)
    assert last["count"] == 0 and last["next_offset"] is None


def test_chop_values_that_are_not_finite_are_named(box):
    big = new(box, "constantCHOP", "big")
    put(big, const0name="v", const0value=1e38)
    over = new(box, "mathCHOP", "over")
    wire(big, over)
    put(over, gain=1e38)  # overflows a 32-bit sample
    under = new(box, "mathCHOP", "under")
    wire(big, under)
    put(under, gain=-1e38)
    both = new(box, "mathCHOP", "both")
    wire(over, both, 0)
    wire(under, both, 1)
    put(both, chopop="add")  # inf + -inf

    assert call("td_read_chop", path=over)["channels"][0]["values"] == ["inf"]
    assert call("td_read_chop", path=under)["channels"][0]["values"] == ["-inf"]
    assert call("td_read_chop", path=both)["channels"][0]["values"] == ["nan"]


def test_dat_tables_page_and_text_dats_return_text(box):
    grid = new(box, "gridSOP", "grid")
    table = new(box, "soptoDAT", "table")
    put(table, sop=grid)

    first = call("td_read_dat", path=table, row_limit=3)
    assert first["is_table"] and first["num_rows"] == 401 and first["row_limit"] == 3
    assert first["rows"][0][0] == "index" and first["next_row_offset"] == 3

    past = call("td_read_dat", path=table, row_offset=5000)
    assert (past["row_offset"], past["row_limit"], past["rows"]) == (401, 0, [])

    big = call("td_read_dat", path=table, row_limit=500)
    assert big["next_row_offset"] is None or big["row_limit"] == big["next_row_offset"]

    glsl = call("td_create_operator", parent=box, type="glslTOP", name="g")
    pixel = next(p for p in glsl["also_created"] if p.endswith("pixel"))
    text = call("td_read_dat", path=pixel)
    assert not text["is_table"] and "void main()" in text["text"]


def test_dat_wide_tables_are_cut(box):
    noise = new(box, "noiseCHOP", "n")
    rows = new(box, "choptoDAT", "rows")
    put(rows, chop=noise)
    wide = new(box, "transposeDAT", "wide")
    wire(rows, wide)

    found = call("td_read_dat", path=wide)
    assert found["num_cols"] == 600 and found["columns_truncated"]
    assert len(found["rows"][0]) == 64


def test_sop_counts_bounds_groups_and_attributes(box):
    cube = new(box, "boxSOP", "cube")
    group = new(box, "groupSOP", "group")
    wire(cube, group)
    put(group, entity="point", crname="mypoints", usenumber=True)
    point = new(box, "pointSOP", "point")
    wire(group, point)
    put(point, doclr="on", attr0name="weight2")

    info = call("td_read_sop_info", path=point)

    assert (info["num_points"], info["num_prims"], info["num_vertices"]) == (24, 6, 24)
    assert info["min"] == [-0.5, -0.5, -0.5] and info["size"] == [1.0, 1.0, 1.0]
    assert info["point_groups"] == ["mypoints"] and info["prim_groups"] == []
    assert info["point_attributes"] == ["Cd", "N", "weight2"]
    assert info["vertex_attributes"] == ["uv"]


def test_readers_refuse_the_wrong_family(box):
    cube = new(box, "boxSOP", "cube")
    assert "not a CHOP" in refused("td_read_chop", path=cube)
    assert "not a DAT" in refused("td_read_dat", path=cube)
    assert "not a SOP" in refused("td_read_sop_info", path=box)
    assert "not a TOP" in refused("td_capture_contact_sheet", paths=[cube])


# --- timeline ---------------------------------------------------------------------------------


def test_timeline_sets_range_before_frame_and_undo_restores_it():
    call("td_timeline", start=1, end=600, frame=120, play=False)

    narrowed = call("td_timeline", start=10, end=50, frame=40)
    now = narrowed["timeline"]
    assert (now["start"], now["end"], now["range_start"], now["range_end"]) == (10, 50, 10, 50)
    assert now["frame"] == 40

    call("td_undo")
    restored = timeline()
    assert (restored["start"], restored["end"], restored["frame"]) == (1, 600, 120)
    call("td_redo")
    assert (timeline()["start"], timeline()["frame"]) == (10, 40)

    # Widening again must free the playhead, not leave it in the narrow range.
    wide = call("td_timeline", start=1, end=600, frame=300)["timeline"]
    assert (wide["frame"], wide["range_start"], wide["range_end"]) == (300, 1, 600)
    ends = call("td_timeline", frame=600)["timeline"]
    assert ends["frame"] == 600
    assert call("td_timeline", frame=1)["timeline"]["frame"] == 1


def test_timeline_stop_and_seek_lands_on_the_frame():
    call("td_timeline", play=True)
    assert call("td_timeline", play=False, frame=100)["timeline"]["frame"] == 100
    assert timeline()["frame"] == 100


def test_timeline_a_range_that_excludes_the_playhead_moves_it_at_once():
    call("td_timeline", frame=200, play=False)
    reply = call("td_timeline", start=300, end=400)["timeline"]
    assert reply["frame"] == 300
    assert timeline()["frame"] == 300
    call("td_timeline", start=1, end=600)


def test_timeline_play_and_its_undo():
    call("td_timeline", frame=100, play=False)
    call("td_timeline", play=True)
    first = timeline()["frame"]
    assert timeline()["frame"] != first
    call("td_undo")
    held = timeline()
    assert held["play"] is False and timeline()["frame"] == held["frame"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"frame": 700}, "outside the playback range"),
        ({"frame": 0}, "outside the playback range"),
        ({"frame": 20.5}, "whole number"),
        ({"start": 80, "end": 20}, "end before it starts"),
        ({}, "at least one"),
    ],
)
def test_timeline_refuses_what_would_be_silently_changed(change, message):
    call("td_timeline", frame=100, play=False)
    assert message in refused("td_timeline", **change)
    assert timeline()["frame"] == 100


# --- contact sheet ------------------------------------------------------------------------------


def test_sheet_tiles_several_tops_in_order(box):
    colors = {"red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1)}
    paths = [
        flat(box, name, 64, 32, colorr=r, colorg=g, colorb=b)
        for name, (r, g, b) in colors.items()
    ]
    sheet = call("td_capture_contact_sheet", paths=paths, columns=2)
    assert sheet.size == (128, 64)
    assert [sheet.getpixel(p) for p in ((32, 16), (96, 16), (32, 48))] == [
        tuple(255 * v for v in c) for c in colors.values()
    ]


def test_sheet_keeps_the_shape_of_differently_sized_tops(box):
    wide = flat(box, "wide", 64, 32)
    tall = flat(box, "tall", 32, 64)
    sheet = call("td_capture_contact_sheet", paths=[wide, tall])
    assert sheet.size == (128, 64)
    background, white = (32, 32, 32), (255, 255, 255)
    assert [sheet.getpixel(p) for p in ((32, 32), (32, 4), (96, 4), (68, 32))] == [
        white, background, white, background
    ]


def test_the_bridge_token_never_comes_back_from_a_dat(box):
    token_file = os.environ.get("TD_MCP_TOKEN_FILE")
    token = os.environ.get("TD_MCP_TOKEN") or Path(str(token_file)).read_text().strip()
    bridge = BridgeClient().ping()["bridge_path"]
    copy = new(box, "selectDAT", "copy")
    put(copy, dat=bridge + "/cb")

    for path in (bridge + "/cb", copy):
        reply = json.dumps(call("td_read_dat", path=path))
        assert "<redacted>" in reply and token not in reply


@pytest.mark.parametrize("through_chop", [False, True])
def test_sheet_frames_follow_the_timeline_and_put_it_back(box, through_chop):
    if through_chop:
        clock = new(box, "timelineCHOP", "clock")
        put(clock, frame=True)
        expression = "op('clock')['frame']/255"
    else:
        expression = "me.time.frame/255"
    source = flat(box, "source", **gray(expression))
    inverted = new(box, "levelTOP", "inverted")
    wire(source, inverted)
    put(inverted, invert=1.0)
    call("td_timeline", frame=7, play=False)

    sheet = call(
        "td_capture_contact_sheet", paths=[source, inverted], frames=[10, 100, 200, 50], columns=4
    )

    assert reds(sheet, 4) == pytest.approx([10, 100, 200, 50], abs=1)
    lower = [sheet.getpixel((16 + 32 * i, 48))[0] for i in range(4)]
    assert lower == pytest.approx([245, 155, 55, 205], abs=1)
    assert timeline()["frame"] == 7


def test_sheet_refuses_frames_outside_the_range_and_restores_after_an_error(box):
    small = flat(box, "small", **gray("me.time.frame/255"))
    heavy = new(box, "noiseTOP", "heavy")
    put(heavy, outputresolution="custom", resolutionw=480, resolutionh=480, mono=False,
        format="rgba32float", period=0.001)
    call("td_timeline", frame=7, play=False)

    assert "outside" in refused("td_capture_contact_sheet", paths=[small], frames=[700])
    assert "whole" in refused("td_capture_contact_sheet", paths=[small], frames=[2.5])
    # A second path that cannot be a cell fails after the timeline has already moved.
    assert "byte" in refused(
        "td_capture_contact_sheet", paths=[small, heavy], frames=[100, 200]
    )
    assert timeline()["frame"] == 7


def test_sheet_with_a_feedback_loop_gives_identical_cells_and_advances_one_step(box):
    """What the tool's description warns about."""
    seed = flat(box, "seed", colorr=0.02, colorg=0.02, colorb=0.02)
    loop = new(box, "feedbackTOP", "loop")
    total = new(box, "compositeTOP", "total")
    wire(seed, loop)
    wire(seed, total, 0)
    wire(loop, total, 1)
    put(total, operand="add")
    put(loop, top=total)
    call("td_timeline", frame=7, play=False)

    def level() -> int:
        return call("td_capture_top", path=total).getpixel((16, 16))[0]

    before = level()
    assert level() == before, "a plain capture does not advance a paused loop"
    cells = reds(call("td_capture_contact_sheet", paths=[total], frames=[10, 11, 12]), 2)
    assert len(set(cells)) == 1
    assert level() == cells[0] > before


@pytest.mark.parametrize("capture_mode", ["timeslice", "inputcooks"])
def test_sheet_frames_are_recorded_by_a_trail_chop(box, capture_mode):
    """What the tool's description warns about."""
    clock = new(box, "timelineCHOP", "clock")
    put(clock, frame=True)
    trail = new(box, "trailCHOP", "trail")
    wire(clock, trail)
    put(trail, wlength=8, wlengthunit="samples", capture=capture_mode)
    newest = "op('trail')[0][op('trail').numSamples-1]/255"
    view = flat(box, "view", **gray(newest))

    def history() -> list[int]:
        return [round(v) for v in call("td_read_chop", path=trail)["channels"][0]["values"]]

    call("td_timeline", frame=7, play=False)
    history()
    call("td_timeline", frame=9)
    before = history()
    assert history() == before, "reading does not advance a paused trail"
    call("td_capture_contact_sheet", paths=[view])
    assert history() == before, "a sheet without frames records nothing"

    sheet = call("td_capture_contact_sheet", paths=[view], frames=[50, 100, 200])

    assert reds(sheet, 2) == pytest.approx([50, 100], abs=1)
    assert history()[-4:] == [50, 100, 200, 9]
    assert timeline()["frame"] == 9


@pytest.mark.parametrize("pixel_format", ["rgba8fixed", "rgba32float", "mono8fixed",
                                          "mono32float", "rg16float"])
@pytest.mark.parametrize("image_format", ["png", "jpg"])
def test_a_large_top_is_scaled_down_faithfully(box, pixel_format, image_format):
    """Colour noise catches a vertical flip or swapped channels, compared with the full capture."""
    top = new(box, "noiseTOP", "n")
    put(top, outputresolution="custom", resolutionw=1280, resolutionh=720, mono=False,
        format=pixel_format, period=0.6)
    full = call("td_capture_top", path=top, format="jpg")  # a float PNG is over its limit
    expected = full.resize((640, 360), Image.Resampling.BOX)

    cell = call("td_capture_contact_sheet", paths=[top], format=image_format)

    assert cell.size == (640, 360)
    difference = ImageStat.Stat(ImageChops.difference(cell, expected)).mean
    assert max(difference) < 4, difference
    upside_down = expected.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    assert max(ImageStat.Stat(ImageChops.difference(cell, upside_down)).mean) > max(difference)


def test_replies_are_valid_json_sizes(box):
    chop = new(box, "noiseCHOP", "many")
    put(chop, channelname="c[1-80]")
    assert len(json.dumps(call("td_read_chop", path=chop, count=2000))) < 250_000
