"""Live construction checks: real stdio server -> bridge in TouchDesigner.

Opt-in (a plain `pytest` skips it). Needs the same setup as test_roundtrip.py: a bridge built
with `python3 bridge/build_spike.py --test-fixtures` loaded in TouchDesigner, and
TD_MCP_TOKEN_FILE set. If the bridge was built with --audit-log, also set TD_MCP_TEST_AUDIT_LOG
to that path.

Everything happens inside one new COMP at the project root, which is deleted at the end. The
changes stay in TouchDesigner's undo history.
"""

import asyncio
import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from td_mcp.client import BridgeClient

pytestmark = pytest.mark.integration


class Td:
    """Calls tools on a real stdio server, one process per call."""

    def __call__(self, tool: str, **args: Any) -> dict[str, Any]:
        async def run():
            transport = StdioTransport(
                command=sys.executable, args=["-m", "td_mcp.server"], env=dict(os.environ)
            )
            async with Client(transport) as client:
                return await client.call_tool(tool, args, raise_on_error=False)

        result = asyncio.run(run())
        if result.is_error:
            raise Refused(str(getattr(result.content[0], "text", result.content[0])))
        assert result.structured_content is not None
        return result.structured_content

    def refused(self, tool: str, **args: Any) -> str:
        with pytest.raises(Refused) as caught:
            self(tool, **args)
        return str(caught.value)

    def inputs(self, path: str) -> list[str]:
        return self("td_get_operator", path=path)["inputs"]

    def par(self, path: str, name: str) -> dict[str, Any]:
        (found,) = self("td_get_parameters", path=path, name=name)["parameters"]
        return found

    def exists(self, path: str) -> bool:
        try:
            self("td_get_operator", path=path)
        except Refused:
            return False
        return True


class Refused(Exception):
    pass


@pytest.fixture(scope="module")
def td() -> Td:
    if not os.environ.get("TD_MCP_TOKEN_FILE") and not os.environ.get("TD_MCP_TOKEN"):
        pytest.fail("Set TD_MCP_TOKEN_FILE (see this file's docstring). Not skipping on purpose.")
    return Td()


@pytest.fixture
def box(td: Td) -> Iterator[str]:
    made = td("td_create_operator", parent="/", type="baseCOMP", name="td_mcp_test")
    yield made["path"]
    if td.exists(made["path"]):
        td("td_delete_operator", path=made["path"])


def new(td: Td, parent: str, optype: str, name: str) -> str:
    return td("td_create_operator", parent=parent, type=optype, name=name)["path"]


def test_create_reports_renames_docked_operators_and_undoes_cleanly(td, box):
    first = td("td_create_operator", parent=box, type="noiseTOP", name="n")
    second = td("td_create_operator", parent=box, type="noiseTOP", name="n")
    assert first["path"] == box + "/n" and not first["renamed"]
    assert second["renamed"] and second["path"] != first["path"]
    assert second["x"] > first["x"], "new nodes must not stack"
    assert first["undo_label"] and first["audit_warning"] is None

    glsl = td("td_create_operator", parent=box, type="glslTOP", name="g", x=0, y=-400)
    assert (glsl["x"], glsl["y"]) == (0, -400)
    assert glsl["also_created"], "a GLSL TOP comes with docked DATs"

    td("td_undo")
    assert not td.exists(glsl["path"])
    assert not any(td.exists(path) for path in glsl["also_created"])
    td("td_redo")
    assert all(td.exists(path) for path in [glsl["path"], *glsl["also_created"]])


def test_an_annotation_can_be_created_and_undone(td, box):
    made = td("td_create_operator", parent=box, type="annotateCOMP", name="note")
    assert made["undo_label"]
    td("td_undo")
    assert not td.exists(made["path"])


def test_bad_creates_are_refused_with_help(td, box):
    assert "noiseTOP" in td.refused("td_create_operator", parent=box, type="noise")
    assert "letters" in td.refused(
        "td_create_operator", parent=box, type="noiseTOP", name="no spaces"
    )
    assert "no operator" in td.refused("td_create_operator", parent=box + "/*", type="noiseTOP")
    assert "protected" in td.refused("td_create_operator", parent="/ui", type="baseCOMP")


def test_wiring(td, box):
    a, b, c = (new(td, box, "constantTOP", name) for name in "abc")
    level = new(td, box, "levelTOP", "level")
    comp = new(td, box, "compositeTOP", "comp")

    assert td("td_connect", source=a, target=level)["inputs"] == [a]
    replaced = td("td_connect", source=b, target=level)
    assert replaced["replaced"] == [a] and replaced["inputs"] == [b]
    td("td_undo")
    assert td.inputs(level) == [a]

    for index, source in enumerate((a, b, c)):
        td("td_connect", source=source, target=comp, target_input=index)
    removed = td("td_disconnect", target=comp, target_input=0)
    assert removed["removed"] == [a] and removed["inputs"] == [b, c]
    td("td_undo")
    assert td.inputs(comp) == [a, b, c]

    chop = new(td, box, "constantCHOP", "chop")
    assert "refused to wire" in td.refused("td_connect", source=chop, target=level)
    inner_box = new(td, box, "baseCOMP", "inner")
    far = new(td, inner_box, "constantTOP", "far")
    assert "different networks" in td.refused("td_connect", source=far, target=level)
    assert td.inputs(level) == [a], "a refused connect must leave the existing wire"
    assert "connector 7" in td.refused("td_connect", source=a, target=level, target_input=7)
    assert "itself" in td.refused("td_connect", source=level, target=level)


def test_set_parameters_is_atomic_and_reversible(td, box):
    top = new(td, box, "constantTOP", "c")
    master = new(td, box, "constantTOP", "m")

    changed = ("colorr", "colorg", "colorb", "outputresolution", "resolutionw")

    def state():
        return [(n, td.par(top, n)["value"], td.par(top, n)["mode"]) for n in changed]

    original = state()
    result = td(
        "td_set_parameters",
        path=top,
        parameters=[
            {"name": "colorr", "value": 0.25},
            {"name": "colorg", "expr": "0.5 + 0.25"},
            {"name": "colorb", "bind": "op('m').par.colorb"},
            {"name": "outputresolution", "value": "custom"},
            {"name": "resolutionw", "value": 320},
        ],
    )
    rows = {row["name"]: row for row in result["parameters"]}
    assert rows["colorr"]["value"] == 0.25
    assert (rows["colorg"]["mode"], rows["colorg"]["value"]) == ("EXPRESSION", 0.75)
    assert rows["colorb"]["mode"] == "BIND"
    assert rows["resolutionw"]["value"] == 320 and not rows["resolutionw"]["adjusted"]

    # Values and modes come back. TouchDesigner keeps the unused bind text, so is_default
    # is not compared (research/PHASE2_UNDO_RESULTS.md).
    td("td_undo")
    assert state() == original
    td("td_redo")
    assert td.par(top, "colorg")["value"] == 0.75

    # A value on a bound parameter changes that parameter only.
    before = td.par(master, "colorb")["value"]
    unbound = td("td_set_parameters", path=top, parameters=[{"name": "colorb", "value": 0.1}])
    assert unbound["parameters"][0]["mode"] == "CONSTANT"
    assert unbound["parameters"][0]["previous"]["mode"] == "BIND"
    assert td.par(master, "colorb")["value"] == before
    td("td_undo")
    assert td.par(top, "colorb")["mode"].endswith("BIND")

    clamped = td("td_set_parameters", path=top, parameters=[{"name": "resolutionw", "value": -5}])
    assert clamped["parameters"][0]["value"] == 1 and clamped["parameters"][0]["adjusted"]


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"name": "colorg", "expr": "1 +"}, "does not evaluate"),
        ({"name": "colorg", "expr": "no_such_name"}, "does not evaluate"),
        ({"name": "colorg", "bind": "op('missing').par.colorg"}, "does not resolve"),
        ({"name": "colorg", "bind": "me.par.colorg"}, "itself"),
        ({"name": "outputresolution", "value": "nonsense"}, "menu"),
        ({"name": "outputresolution", "value": 99}, "menu"),
        ({"name": "colour", "value": 1}, "no parameter"),
    ],
)
def test_one_bad_change_leaves_every_parameter_untouched(td, box, bad, message):
    top = new(td, box, "constantTOP", "c")
    good = {"name": "colorr", "value": 0.25}

    assert message in td.refused("td_set_parameters", path=top, parameters=[good, bad])

    assert td.par(top, "colorr")["value"] == 1.0
    assert td.par(top, "colorg")["mode"].endswith("CONSTANT")
    # A rolled-back change must not be replayable.
    try:
        td("td_redo")
    except Refused:
        pass
    assert td.par(top, "colorr")["value"] == 1.0


def test_password_values_stay_out_of_replies_and_the_audit_log(td, box):
    server = new(td, box, "webserverDAT", "w")
    result = td(
        "td_set_parameters",
        path=server,
        parameters=[{"name": "password", "value": "live-secret-value"}],
    )
    assert "live-secret-value" not in json.dumps(result)
    assert "live-secret-value" not in audit_path().read_text(encoding="utf-8")


def test_delete_and_undo_restore_wires_and_settings(td, box):
    a = new(td, box, "constantTOP", "a")
    mid = new(td, box, "levelTOP", "mid")
    out = new(td, box, "levelTOP", "out")
    td("td_connect", source=a, target=mid)
    td("td_connect", source=mid, target=out)
    td("td_set_parameters", path=mid, parameters=[{"name": "opacity", "value": 0.5}])

    gone = td("td_delete_operator", path=mid)
    assert gone["disconnected_inputs"] == [a] and gone["disconnected_outputs"] == [out]
    # TouchDesigner wires the source straight into what the deleted node fed.
    assert not td.exists(mid)
    assert gone["downstream"] == [{"path": out, "inputs": [a]}]
    assert td.inputs(out) == [a]

    td("td_undo")
    assert td.inputs(mid) == [a] and td.inputs(out) == [mid]
    assert td.par(mid, "opacity")["value"] == 0.5


def test_deleting_a_host_takes_its_docked_operators_and_undo_restores_them(td, box):
    glsl = td("td_create_operator", parent=box, type="glslTOP", name="g")
    gone = td("td_delete_operator", path=glsl["path"])
    assert gone["also_deleted"] == glsl["also_created"]
    td("td_undo")
    assert all(td.exists(path) for path in glsl["also_created"])


def test_the_bridge_and_system_networks_are_protected(td, box):
    bridge = BridgeClient().ping()["bridge_path"]
    for path in (bridge, bridge + "/cb", "/ui", "/sys", "/local"):
        td.refused("td_delete_operator", path=path)
        assert td.exists(path)
    assert "project root" in td.refused("td_delete_operator", path="/")


def test_layout_arranges_by_signal_flow_and_undo_restores(td, box):
    paths = {n: new(td, box, "levelTOP", n) for n in ("sink", "mid", "other")}
    paths["source"] = new(td, box, "constantTOP", "source")
    glsl = td("td_create_operator", parent=box, type="glslTOP", name="g", x=0, y=0)
    td("td_connect", source=paths["source"], target=paths["mid"])
    td("td_connect", source=paths["mid"], target=paths["sink"])

    def next_free_position():
        # A new node goes right of the rightmost one, so this reads the current positions.
        made = td("td_create_operator", parent=box, type="constantTOP", name="ruler")
        td("td_undo")
        return made["x"], made["y"]

    unarranged = next_free_position()
    laid = td("td_layout", path=box)
    at = {o["path"]: (o["x"], o["y"]) for o in laid["operators"]}
    assert at[paths["source"]][0] < at[paths["mid"]][0] < at[paths["sink"]][0]
    assert len(set(at.values())) == len(at), "no two operators share a position"
    assert not set(glsl["also_created"]) & set(at), "docked operators follow their host"

    arranged = next_free_position()
    assert arranged != unarranged
    again = td("td_layout", path=box)
    assert {o["path"]: (o["x"], o["y"]) for o in again["operators"]} == at, "layout is stable"
    undone = td("td_undo", steps=2)
    assert len(undone["done"]) == 2
    assert next_free_position() == unarranged, "undo restores positions"


def test_undo_refuses_an_entry_the_server_did_not_make(td, box):
    new(td, box, "constantTOP", "a")
    BridgeClient().get("/foreign-edit")

    assert "Test Foreign Edit" in td.refused("td_undo")
    assert td.exists(box + "/a")


def audit_path() -> Path:
    configured = os.environ.get("TD_MCP_TEST_AUDIT_LOG")
    return Path(configured) if configured else Path.home() / ".td-mcp" / "audit.jsonl"


def test_changes_are_in_the_private_audit_log(td, box):
    made = td("td_create_operator", parent=box, type="constantTOP", name="logged")
    entry_id = made["undo_label"].split()[1].rstrip(":")
    path = audit_path()

    events = [
        entry
        for entry in map(json.loads, path.read_text(encoding="utf-8").splitlines())
        if entry.get("id") == entry_id
    ]

    assert [e["event"] for e in events] == ["started", "ok"]
    assert events[0]["request"]["name"] == "logged"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
