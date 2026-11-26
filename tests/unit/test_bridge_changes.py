"""The bridge's change routes, run from the real source against stand-ins for TouchDesigner.

The fakes model only what research/PHASE2_UNDO_RESULTS.md recorded on a live build (one undo
entry per block, newest first; a new block clears the redo stack). They test our logic; the
live behavior is covered by tests/integration/test_construction.py.
"""

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).parents[2] / "bridge" / "src" / "callbacks.py").read_text(encoding="utf-8")
TOKEN = "test-token-123"
BRIDGE = "/project1/td_mcp_bridge"


class ParMode:
    CONSTANT = "ParMode.CONSTANT"
    EXPRESSION = "ParMode.EXPRESSION"
    BIND = "ParMode.BIND"
    EXPORT = "ParMode.EXPORT"


class FakeUndo:
    """Snapshots the whole fake network around each block, so undo and redo really restore it."""

    def __init__(self, world):
        self.world = world
        self.globalState = True
        self.done: list = []  # (label, before, after), newest first
        self.undone: list = []
        self._open = None
        self.callbacks = 0

    @property
    def undoStack(self):
        return [entry[0] for entry in self.done]

    @property
    def redoStack(self):
        return [entry[0] for entry in self.undone]

    def startBlock(self, name):
        self._open = (name, self.world.snapshot())

    def addCallback(self, callback, info=None):
        self.callbacks += 1

    def endBlock(self):
        if self._open is None:
            raise RuntimeError("Cannot end non existent undo operation.")
        name, before = self._open
        self._open = None
        self.done.insert(0, (name, before, self.world.snapshot()))
        self.undone.clear()

    def undo(self):
        entry = self.done.pop(0)
        self.world.restore(entry[1])
        self.undone.insert(0, entry)

    def redo(self):
        entry = self.undone.pop(0)
        self.world.restore(entry[2])
        self.done.insert(0, entry)


class FakePar:
    def __init__(self, owner, name, value, kind="float", **extra):
        self.owner, self.name, self.val, self.kind = owner, name, value, kind
        self.expr = ""
        self.bindExpr = ""
        self.mode = ParMode.CONSTANT
        self.readOnly = False
        self.password = False
        self.exportOP = None
        self.menuNames: list = extra.get("menu") or []
        self.clamp: tuple | None = extra.get("clamp")
        self.isMenu = kind in ("menu", "strmenu")
        self.isString = kind in ("str", "strmenu", "op")
        self.isToggle = kind == "toggle"
        self.isNumber = kind in ("float", "int", "toggle")
        self.isInt = kind == "int"
        self.isPulse = kind == "pulse"
        self.isOP = kind == "op"

    def __setattr__(self, key, value):
        clamp = getattr(self, "clamp", None)
        if key == "val" and clamp is not None:
            value = max(clamp[0], min(clamp[1], value))
        super().__setattr__(key, value)

    def eval(self):
        if self.mode == ParMode.EXPRESSION:
            if "bad" in self.expr:
                raise RuntimeError("Error in parameter " + self.name)
            if self.expr.startswith("float('"):
                return float(self.expr.split("'")[1])
            return 0.5
        if self.mode == ParMode.BIND:
            master = self.bindMaster
            return master.val if master is not None else 0.0
        if self.kind == "op":
            return self.owner.world.ops.get(self.val)
        if self.kind == "menu" and isinstance(self.val, int):
            return self.menuNames[self.val]
        return self.val

    @property
    def bindMaster(self):
        """Fake bind references are written "<operator path>:<parameter name>"."""
        path, _, name = self.bindExpr.partition(":")
        found = self.owner.world.ops.get(path)
        return found.par[name] if found is not None else None


class Pars(dict):
    def __getitem__(self, name):
        return self.get(name)


class Connector:
    def __init__(self, owner, index, is_input):
        self.owner, self.index, self.is_input = owner, index, is_input

    @property
    def connections(self):
        world = self.owner.world
        if self.is_input:
            wired = self.owner.wires
            if self.index < len(wired) and wired[self.index] is not None:
                return [Connector(world.ops[wired[self.index]], 0, False)]
            return []
        return [
            Connector(o, i, True)
            for o in world.ops.values()
            for i, source in enumerate(o.wires)
            if source == self.owner.path
        ]

    def connect(self, target):
        if self.owner.family != target.owner.family:
            raise RuntimeError("Invalid number or type of arguments.")
        wires = target.owner.wires
        wires.extend([None] * (target.index + 1 - len(wires)))
        wires[target.index] = self.owner.path

    def disconnect(self):
        del self.owner.wires[self.index]


class FakeOp:
    def __init__(self, world, path, type_="constant", family="TOP", inputs=1, dock=None):
        self.world, self.path, self.type, self.family = world, path, type_, family
        self.name = path.rsplit("/", 1)[-1]
        self.wires: list = []
        self.max_inputs = inputs
        self.dock = dock
        self.nodeX = self.nodeY = 0
        self.nodeWidth, self.nodeHeight = 130, 90
        self.par = Pars()
        self.storage: dict = {}
        self.destroy_works = True

    def add(self, name, value, kind="float", **extra):
        self.par[name] = FakePar(self, name, value, kind, **extra)
        return self.par[name]

    @property
    def builtinPars(self):
        return list(self.par.values())

    customPars: tuple = ()

    @property
    def inputConnectors(self):
        count = self.max_inputs if self.max_inputs < 10 else len(self.wires) + 1
        return [Connector(self, i, True) for i in range(count)]

    @property
    def outputConnectors(self):
        return [Connector(self, 0, False)]

    @property
    def inputs(self):
        return [self.world.ops[p] for p in self.wires if p is not None]

    @property
    def outputs(self):
        return [o for o in self.world.ops.values() if self.path in o.wires]

    @property
    def docked(self):
        return [o for o in self.world.ops.values() if o.dock is self]

    def findChildren(self, depth=None, maxDepth=None, includeUtility=False):
        prefix = self.path.rstrip("/") + "/"
        below = [o for p, o in self.world.ops.items() if p != self.path and p.startswith(prefix)]
        if depth == 1:
            below = [o for o in below if "/" not in o.path[len(prefix):]]
        return below

    def create(self, optype, name=None):
        if optype == "brokenTOP":
            self.world.add(self.path + "/half_made")
            raise RuntimeError("creation blew up")
        wanted = name or optype[:-3] + "1"
        final, number = wanted, 1
        while self.path + "/" + final in self.world.ops:
            final, number = wanted + str(number), number + 1
        made = self.world.add(self.path + "/" + final, type_=optype[:-3], family=optype[-3:])
        if optype == "glslTOP":
            helper = self.world.add(made.path + "_pixel", type_="text", family="DAT", dock=made)
            helper.nodeY = -120
        return made

    def destroy(self):
        if not self.destroy_works:
            return
        gone = [p for p in self.world.ops if p == self.path or p.startswith(self.path + "/")]
        gone += [o.path for o in self.docked]
        for path in gone:
            self.world.ops.pop(path, None)
        for other in self.world.ops.values():
            other.wires = [w for w in other.wires if w not in gone]

    def fetch(self, key, default, search=True):
        return self.storage.get(key, default)

    def store(self, key, value):
        self.storage[key] = value


class World:
    def __init__(self):
        self.ops: dict = {}
        for path, type_, family in [
            ("/", "root", "COMP"),
            ("/ui", "base", "COMP"),
            ("/sys", "base", "COMP"),
            ("/local", "base", "COMP"),
            ("/local/time", "time", "COMP"),
            ("/project1", "container", "COMP"),
            (BRIDGE, "base", "COMP"),
            (BRIDGE + "/cb", "text", "DAT"),
        ]:
            self.add(path, type_=type_, family=family)

    def add(self, path, type_="constant", family="TOP", inputs=1, dock=None):
        made = self.ops[path] = FakeOp(self, path, type_, family, inputs, dock)
        return made

    def snapshot(self):
        return (
            dict(self.ops),
            {
                path: (
                    list(o.wires),
                    o.nodeX,
                    o.nodeY,
                    {n: (p.val, p.expr, p.bindExpr, p.mode) for n, p in o.par.items()},
                )
                for path, o in self.ops.items()
            },
        )

    def restore(self, snapshot):
        ops, state = snapshot
        self.ops.clear()
        self.ops.update(ops)
        for path, (wires, x, y, pars) in state.items():
            node = self.ops[path]
            node.wires, node.nodeX, node.nodeY = list(wires), x, y
            for name, (val, expr, bind, mode) in pars.items():
                par = node.par[name]
                par.val, par.expr, par.bindExpr, par.mode = val, expr, bind, mode


class Bridge:
    def __init__(self, audit_path: Path):
        self.world = World()
        self.undo = FakeUndo(self.world)
        self.audit_path = audit_path
        source = SOURCE.replace("__TOKEN__", TOKEN).replace("__TEST_FIXTURES__", "0")
        self.source = source.replace('"__AUDIT_LOG__"', json.dumps(str(audit_path)))

    def request(self, route, body=None, *, method="POST", headers=None, raw=None, auth=True):
        # TouchDesigner re-executes the module on every request, so do the same.
        types = [type(name, (), {}) for name in
                 ("constantTOP", "noiseTOP", "levelTOP", "glslTOP", "noiseCHOP", "brokenTOP")]
        ns = {
            "op": lambda path: self.world.ops.get(path),
            "parent": lambda: self.world.ops[BRIDGE],
            "app": SimpleNamespace(build="1", version="2"),
            "families": {"TOP": types},
            "ui": SimpleNamespace(undo=self.undo),
            "ParMode": ParMode,
        }
        exec(compile(self.source, "callbacks.py", "exec"), ns)  # noqa: S102
        sent = {
            "uri": route,
            "pars": {},
            "method": method,
            "Host": "127.0.0.1:9991",
            "Content-Type": "application/json",
            "data": raw if raw is not None else json.dumps(body or {}).encode(),
        }
        if auth:
            sent["Authorization"] = f"Bearer {TOKEN}"
        sent.update(headers or {})
        response: dict = {}
        ns["onHTTPRequest"](None, sent, response)
        return response["statusCode"], json.loads(response["data"])

    def change(self, route, **body):
        status, reply = self.request(route, body)
        assert status == 200, reply
        return reply["result"]

    def audit(self):
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]


@pytest.fixture
def bridge(tmp_path):
    return Bridge(tmp_path / "logs" / "audit.jsonl")


def make(bridge, path, **kwargs):
    return bridge.world.add(path, **kwargs)


def noise(bridge):
    node = make(bridge, "/project1/n")
    node.add("amp", 1.0)
    node.add("res", 256, "int", clamp=(1, 1280))
    node.add("type", "perlin", "menu", menu=["perlin", "simplex"])
    node.add("mono", False, "toggle")
    node.add("label", "", "str")
    node.add("source", "", "op")
    node.add("reset", 0, "pulse")
    node.add("secret", "", "str").password = True
    return node


def set_pars(bridge, *changes, path="/project1/n"):
    return bridge.change("/set-parameters", path=path, parameters=list(changes))


# --- request handling ---------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/create", "/delete", "/set-parameters", "/undo"])
def test_change_routes_refuse_get(bridge, route):
    status, body = bridge.request(route, method="GET")
    assert status == 405 and "needs POST" in body["error"]
    assert bridge.undo.undoStack == []


def test_read_routes_refuse_post(bridge):
    assert bridge.request("/ping", method="POST")[0] == 405


def test_change_routes_need_the_token(bridge):
    status, _ = bridge.request("/delete", {"path": "/project1"}, auth=False)
    assert status == 401 and "/project1" in bridge.world.ops and bridge.audit() == []


def test_change_routes_refuse_a_foreign_origin(bridge):
    status, _ = bridge.request(
        "/delete", {"path": "/project1"}, headers={"Origin": "http://evil.example"}
    )
    assert status == 403 and "/project1" in bridge.world.ops


@pytest.mark.parametrize(
    "headers, raw",
    [
        ({"Content-Type": "text/plain"}, b'{"path": "/project1"}'),
        ({"Content-Type": "application/x-www-form-urlencoded"}, b"path=/project1"),
        ({}, b"not json"),
        ({}, b"[1, 2]"),
        ({}, b""),
        ({}, b'{"path": "' + b"x" * (256 * 1024) + b'"}'),
    ],
)
def test_unusable_bodies_are_400_and_logged(bridge, headers, raw):
    status, _ = bridge.request("/delete", headers=headers, raw=raw)
    assert status == 400
    assert [e["event"] for e in bridge.audit()] == ["refused"]


def test_unknown_fields_are_refused(bridge):
    result = bridge.change("/delete", path="/project1", force=True)
    assert "unknown field(s): force" in result["error"] and "/project1" in bridge.world.ops


# --- undo blocks and audit log ------------------------------------------------------------


def test_a_change_is_one_named_undo_entry_logged_before_and_after(bridge):
    result = bridge.change("/create", parent="/project1", type="noiseTOP", name="n1")

    assert bridge.undo.undoStack == [result["undo_label"]]
    assert result["undo_label"].startswith("td-mcp ")
    assert result["audit_warning"] is None
    assert bridge.undo.callbacks == 1  # the marker that keeps TouchDesigner from renaming it
    started, finished = bridge.audit()
    assert (started["event"], finished["event"]) == ("started", "ok")
    assert started["id"] == finished["id"] and started["action"] == "create"
    assert started["request"]["type"] == "noiseTOP"
    assert finished["result"]["path"] == "/project1/n1"


def test_audit_log_is_private_to_the_user(bridge):
    bridge.change("/create", parent="/project1", type="noiseTOP")
    assert stat.S_IMODE(bridge.audit_path.stat().st_mode) == 0o600


def test_nothing_changes_when_the_audit_log_cannot_be_written(tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    bridge = Bridge(blocker / "audit.jsonl")

    result = bridge.change("/create", parent="/project1", type="noiseTOP", name="n1")

    assert "cannot write the audit log" in result["error"]
    assert "/project1/n1" not in bridge.world.ops and bridge.undo.undoStack == []


def test_nothing_changes_when_undo_is_disabled(bridge):
    bridge.undo.globalState = False
    result = bridge.change("/create", parent="/project1", type="noiseTOP", name="n1")
    assert "undo is disabled" in result["error"] and "/project1/n1" not in bridge.world.ops


def test_a_failed_change_is_rolled_back_logged_and_cannot_be_redone(bridge):
    result = bridge.change("/create", parent="/project1", type="brokenTOP")

    assert "creation blew up" in result["error"] and "Nothing was changed" in result["error"]
    assert "/project1/half_made" not in bridge.world.ops
    assert bridge.undo.undoStack == []
    assert [e["event"] for e in bridge.audit()] == ["started", "failed"]
    redo = bridge.change("/redo")
    assert "rolled back" in redo["error"]
    assert "/project1/half_made" not in bridge.world.ops


def test_refusals_are_logged_without_parameter_values(bridge):
    noise(bridge)
    set_pars(bridge, {"name": "nope", "value": "hunter2"})
    (entry,) = bridge.audit()
    assert entry["event"] == "refused" and "no parameter" in entry["error"]
    assert "hunter2" not in bridge.audit_path.read_text()


def test_password_values_are_never_logged_or_returned(bridge):
    noise(bridge)
    set_pars(bridge, {"name": "secret", "value": "first-secret"})
    result = set_pars(bridge, {"name": "secret", "value": "second-secret"})

    assert "-secret" not in bridge.audit_path.read_text()
    assert "-secret" not in json.dumps(result)
    assert bridge.world.ops["/project1/n"].par["secret"].val == "second-secret"


# --- protected operators ------------------------------------------------------------------


@pytest.mark.parametrize(
    "route, body",
    [
        ("/create", {"parent": BRIDGE, "type": "noiseTOP"}),
        ("/create", {"parent": "/ui", "type": "noiseTOP"}),
        ("/create", {"parent": "/sys", "type": "noiseTOP"}),
        ("/delete", {"path": BRIDGE}),
        ("/delete", {"path": BRIDGE + "/cb"}),
        ("/delete", {"path": "/project1"}),  # contains the bridge
        ("/delete", {"path": "/"}),
        ("/delete", {"path": "/ui"}),
        ("/delete", {"path": "/local"}),
        ("/delete", {"path": "/local/time"}),
        ("/set-parameters", {"path": BRIDGE + "/cb", "parameters": [{"name": "a", "value": 1}]}),
        ("/disconnect", {"target": BRIDGE + "/cb"}),
        ("/connect", {"source": BRIDGE + "/cb", "target": "/project1"}),
        ("/layout", {"path": BRIDGE}),
    ],
)
def test_protected_operators_cannot_be_changed(bridge, route, body):
    before = set(bridge.world.ops)
    status, reply = bridge.request(route, body)
    assert status == 200 and "error" in reply["result"]
    assert set(bridge.world.ops) == before and bridge.undo.undoStack == []


@pytest.mark.parametrize("path", ["/project1/*", "project1/a", "", None, 5])
def test_changes_need_an_exact_absolute_path(bridge, path):
    make(bridge, "/project1/a")
    bridge.world.ops["/project1/*"] = bridge.world.ops["/project1/a"]  # a glob "match"
    assert "error" in bridge.change("/delete", path=path)
    assert "/project1/a" in bridge.world.ops


# --- create -------------------------------------------------------------------------------


def test_unknown_type_suggests_the_closest_names(bridge):
    error = bridge.change("/create", parent="/project1", type="noise")["error"]
    assert "noiseCHOP" in error and "noiseTOP" in error


@pytest.mark.parametrize("name", ["has space", "bang!", "", 3, "café"])
def test_bad_names_are_refused(bridge, name):
    assert "name may contain" in bridge.change(
        "/create", parent="/project1", type="noiseTOP", name=name
    )["error"]


def test_a_taken_name_is_reported_as_renamed(bridge):
    bridge.change("/create", parent="/project1", type="noiseTOP", name="a")
    second = bridge.change("/create", parent="/project1", type="noiseTOP", name="a")
    assert second["renamed"] and second["path"] != "/project1/a"


def test_create_only_inside_a_comp(bridge):
    make(bridge, "/project1/a")
    error = bridge.change("/create", parent="/project1/a", type="noiseTOP")["error"]
    assert "inside a COMP" in error


@pytest.mark.parametrize("position", [{"x": 1}, {"y": 1}, {"x": 1.5, "y": 2}, {"x": True, "y": 2}])
def test_position_needs_two_integers(bridge, position):
    result = bridge.change("/create", parent="/project1", type="noiseTOP", **position)
    assert "error" in result and bridge.undo.undoStack == []


def test_new_nodes_go_right_of_existing_ones_and_docked_ones_move_along(bridge):
    old = make(bridge, "/project1/old")
    old.nodeX, old.nodeY = 100, 40

    result = bridge.change("/create", parent="/project1", type="glslTOP", name="g")

    assert (result["x"], result["y"]) == (100 + 130 + 80, 40)
    assert result["also_created"] == ["/project1/g_pixel"]
    helper = bridge.world.ops["/project1/g_pixel"]
    assert (helper.nodeX, helper.nodeY) == (result["x"], 40 - 120)


# --- wiring -------------------------------------------------------------------------------


def test_connect_replaces_and_reports_the_old_source(bridge):
    for name in ("a", "b", "c"):
        make(bridge, "/project1/" + name)
    bridge.change("/connect", source="/project1/a", target="/project1/c")
    result = bridge.change("/connect", source="/project1/b", target="/project1/c")
    assert result["replaced"] == ["/project1/a"] and result["inputs"] == ["/project1/b"]


def test_connect_across_networks_is_refused_before_touching_the_wire(bridge):
    make(bridge, "/project1/a")
    make(bridge, "/project1/c")
    make(bridge, "/project1/box", family="COMP")
    make(bridge, "/project1/box/far")
    bridge.change("/connect", source="/project1/a", target="/project1/c")

    result = bridge.change("/connect", source="/project1/box/far", target="/project1/c")

    assert "different networks" in result["error"]
    assert bridge.world.ops["/project1/c"].wires == ["/project1/a"]


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"target_input": 4}, "no input connector 4"),
        ({"target_input": -1}, "no input connector -1"),
        ({"source_output": 1}, "no output connector 1"),
        ({"target_input": "0"}, "must be an integer"),
    ],
)
def test_connect_checks_connector_numbers(bridge, extra, message):
    make(bridge, "/project1/a")
    make(bridge, "/project1/b")
    result = bridge.change("/connect", source="/project1/a", target="/project1/b", **extra)
    assert message in result["error"]


def test_connect_to_itself_is_refused(bridge):
    make(bridge, "/project1/a")
    assert "itself" in bridge.change("/connect", source="/project1/a", target="/project1/a")["error"]


def test_a_wire_touchdesigner_refuses_is_reported_and_rolled_back(bridge):
    make(bridge, "/project1/a", family="CHOP")
    make(bridge, "/project1/b")
    result = bridge.change("/connect", source="/project1/a", target="/project1/b")
    assert "refused to wire" in result["error"] and bridge.undo.undoStack == []


def test_disconnect_removes_one_input_and_reports_the_shift(bridge):
    for name in ("a", "b"):
        make(bridge, "/project1/" + name)
    make(bridge, "/project1/m", inputs=999)
    bridge.change("/connect", source="/project1/a", target="/project1/m")
    bridge.change("/connect", source="/project1/b", target="/project1/m", target_input=1)

    result = bridge.change("/disconnect", target="/project1/m", target_input=0)

    assert result["removed"] == ["/project1/a"] and result["inputs"] == ["/project1/b"]


def test_disconnect_with_nothing_connected_is_refused(bridge):
    make(bridge, "/project1/a")
    assert "nothing is connected" in bridge.change("/disconnect", target="/project1/a")["error"]


# --- parameters ---------------------------------------------------------------------------


def test_a_batch_is_one_undo_step(bridge):
    node = noise(bridge)
    result = set_pars(
        bridge,
        {"name": "amp", "value": 0.25},
        {"name": "type", "value": "simplex"},
        {"name": "mono", "value": True},
        {"name": "label", "expr": "me.name"},
    )

    assert [p["name"] for p in result["parameters"]] == ["amp", "type", "mono", "label"]
    assert result["parameters"][0]["previous"]["value"] == 1.0
    assert result["parameters"][3]["mode"] == "EXPRESSION"
    assert len(bridge.undo.undoStack) == 1
    bridge.change("/undo")
    assert (node.par["amp"].val, node.par["type"].val, node.par["label"].mode) == (
        1.0,
        "perlin",
        ParMode.CONSTANT,
    )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"name": "amp", "value": "0.5"}, "is a number"),
        ({"name": "amp", "value": True}, "is a number"),
        ({"name": "res", "value": 2.5}, "whole number"),
        ({"name": "type", "value": "nonsense"}, "perlin, simplex"),
        ({"name": "type", "value": 9}, "is a menu"),
        ({"name": "mono", "value": "yes"}, "is a toggle"),
        ({"name": "label", "value": 3}, "takes a string"),
        ({"name": "reset", "value": 1}, "pulse"),
        ({"name": "ampp", "value": 1}, "Closest: amp"),
        ({"name": "amp"}, "exactly one of"),
        ({"name": "amp", "value": 1, "expr": "2"}, "exactly one of"),
        ({"name": "amp", "expr": ""}, "non-empty string"),
        ({"name": "amp", "export": "x"}, "unknown field"),
    ],
)
def test_invalid_changes_are_refused_and_nothing_else_in_the_batch_applies(bridge, change, message):
    node = noise(bridge)
    first = {"name": "label", "value": "changed"}
    result = set_pars(bridge, first, change)
    assert message in result["error"]
    assert node.par["label"].val == "" and bridge.undo.undoStack == []


def test_same_parameter_twice_is_refused(bridge):
    noise(bridge)
    result = set_pars(bridge, {"name": "amp", "value": 1}, {"name": "amp", "value": 2})
    assert "only once" in result["error"]


def test_read_only_and_exported_parameters_are_refused(bridge):
    node = noise(bridge)
    node.par["amp"].readOnly = True
    node.par["res"].mode = ParMode.EXPORT
    node.par["res"].exportOP = SimpleNamespace(path="/project1/lfo")
    assert "read-only" in set_pars(bridge, {"name": "amp", "value": 2})["error"]
    assert "/project1/lfo" in set_pars(bridge, {"name": "res", "value": 2})["error"]


@pytest.mark.parametrize(
    "failing",
    [
        {"name": "res", "expr": "bad("},
        {"name": "res", "bind": "/project1/missing:amp"},
        {"name": "res", "bind": "/project1/n:res"},
    ],
)
def test_a_dead_expression_or_bind_rolls_the_whole_batch_back(bridge, failing):
    node = noise(bridge)
    result = set_pars(bridge, {"name": "amp", "value": 0.1}, failing)
    assert "Nothing was changed" in result["error"]
    assert node.par["amp"].val == 1.0 and node.par["res"].mode == ParMode.CONSTANT


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_an_expression_that_is_not_finite_is_applied_warned_and_sent_as_text(bridge, value):
    node = noise(bridge)
    expr = f"float('{value}')"

    (row,) = set_pars(bridge, {"name": "amp", "expr": expr})["parameters"]

    assert (row["mode"], row["value"]) == ("EXPRESSION", value)
    assert row["warning"] == f"the expr evaluates to {value}"
    assert node.par["amp"].mode == ParMode.EXPRESSION
    # Replacing it must reply too, although the previous value is not finite.
    (back,) = set_pars(bridge, {"name": "amp", "value": 1.0})["parameters"]
    assert (back["previous"]["value"], back["value"], back["warning"]) == (value, 1.0, None)

    def strict(constant):
        raise AssertionError(f"{constant} in the audit log is not JSON")

    for line in bridge.audit_path.read_text().splitlines():
        json.loads(line, parse_constant=strict)


def test_a_bind_to_a_value_that_is_not_finite_is_warned(bridge):
    noise(bridge)
    make(bridge, "/project1/m").add("amp", float("nan"))

    (row,) = set_pars(bridge, {"name": "amp", "bind": "/project1/m:amp"})["parameters"]

    assert (row["mode"], row["value"]) == ("BIND", "nan")
    assert row["warning"] == "the bind evaluates to nan"


def test_setting_a_value_on_a_bound_parameter_leaves_the_master_alone(bridge):
    node = noise(bridge)
    master = make(bridge, "/project1/m").add("amp", 7.0)
    set_pars(bridge, {"name": "amp", "bind": "/project1/m:amp"})

    result = set_pars(bridge, {"name": "amp", "value": 3})

    assert result["parameters"][0]["mode"] == "CONSTANT"
    assert result["parameters"][0]["previous"]["mode"] == "BIND"
    assert node.par["amp"].val == 3 and master.val == 7.0


def test_clamped_numbers_and_unresolved_operator_paths_are_flagged(bridge):
    noise(bridge)
    result = set_pars(bridge, {"name": "res", "value": 5000}, {"name": "source", "value": "gone"})
    res, source = result["parameters"]
    assert res["value"] == 1280 and res["adjusted"]
    assert "does not resolve" in source["warning"]


# --- delete -------------------------------------------------------------------------------


def test_delete_reports_what_went_and_undo_brings_it_back(bridge):
    make(bridge, "/project1/box", family="COMP")
    make(bridge, "/project1/box/inner")
    make(bridge, "/project1/after").wires = ["/project1/box"]

    result = bridge.change("/delete", path="/project1/box")

    assert result["deleted"] == "/project1/box" and result["descendants"] == 1
    assert "/project1/box/inner" not in bridge.world.ops
    assert result["downstream"] == [{"path": "/project1/after", "inputs": []}]
    bridge.change("/undo")
    assert "/project1/box/inner" in bridge.world.ops


def test_delete_that_does_nothing_is_an_error(bridge):
    make(bridge, "/project1/a").destroy_works = False
    assert "did not delete" in bridge.change("/delete", path="/project1/a")["error"]


# --- layout -------------------------------------------------------------------------------


def test_layout_orders_by_signal_flow_without_overlap(bridge):
    for name in ("z_source", "a_sink", "mid", "other"):
        make(bridge, "/project1/" + name)
    bridge.world.ops["/project1/mid"].wires = ["/project1/z_source"]
    bridge.world.ops["/project1/a_sink"].wires = ["/project1/mid"]
    note = make(bridge, "/project1/note", type_="annotate", family="COMP")
    note.nodeX = 999

    result = bridge.change("/layout", path="/project1")

    at = {o["path"].rsplit("/", 1)[-1]: (o["x"], o["y"]) for o in result["operators"]}
    assert at["z_source"][0] < at["mid"][0] < at["a_sink"][0]
    assert at["other"][0] == at["z_source"][0] and abs(at["other"][1] - at["z_source"][1]) >= 90
    assert result["skipped"] == ["/project1/note", BRIDGE] and note.nodeX == 999
    assert (bridge.world.ops[BRIDGE].nodeX, bridge.world.ops[BRIDGE].nodeY) == (0, 0)


def test_layout_survives_a_loop_and_keeps_docked_nodes_with_their_host(bridge):
    a = make(bridge, "/project1/a")
    b = make(bridge, "/project1/b")
    a.wires, b.wires = ["/project1/b"], ["/project1/a"]
    helper = make(bridge, "/project1/a_cb", family="DAT", dock=a)
    helper.nodeY = -120

    result = bridge.change("/layout", path="/project1")

    assert len(result["operators"]) == 2
    assert (helper.nodeX - a.nodeX, helper.nodeY - a.nodeY) == (0, -120)


def test_layout_handles_a_very_long_chain(bridge):
    previous = None
    for index in range(1000):
        node = make(bridge, f"/project1/n{index:04d}")
        node.wires = [previous] if previous else []
        previous = node.path
    result = bridge.change("/layout", path="/project1")
    xs = [o["x"] for o in result["operators"]]
    assert len(set(xs)) == 1000


def test_layout_of_an_empty_comp_is_refused(bridge):
    make(bridge, "/project1/box", family="COMP")
    assert "no operators" in bridge.change("/layout", path="/project1/box")["error"]


# --- undo and redo ------------------------------------------------------------------------


def create_three(bridge):
    return [
        bridge.change("/create", parent="/project1", type="noiseTOP", name=n)["undo_label"]
        for n in ("a", "b", "c")
    ]


def test_undo_and_redo_step_through_server_changes(bridge):
    labels = create_three(bridge)

    undone = bridge.change("/undo", steps=2)
    assert undone["done"] == [labels[2], labels[1]] and undone["next"] == labels[0]
    assert "/project1/b" not in bridge.world.ops

    redone = bridge.change("/redo", steps=5)
    assert redone["done"] == [labels[1], labels[2]] and "nothing to redo" in redone["stopped"]
    assert "/project1/c" in bridge.world.ops
    assert [e["action"] for e in bridge.audit()][-4:] == ["undo", "undo", "redo", "redo"]


def test_undo_never_touches_an_entry_the_server_did_not_make(bridge):
    create_three(bridge)
    bridge.undo.startBlock("Move Nodes")  # the user's own edit
    bridge.undo.endBlock()

    result = bridge.change("/undo")

    assert "'Move Nodes'" in result["error"]
    assert len(bridge.undo.undoStack) == 4


def test_undo_stops_at_the_first_foreign_entry(bridge):
    bridge.undo.startBlock("Move Nodes")
    bridge.undo.endBlock()
    label = bridge.change("/create", parent="/project1", type="noiseTOP")["undo_label"]

    result = bridge.change("/undo", steps=3)

    assert result["done"] == [label] and "Move Nodes" in result["stopped"]
    assert result["next"] is None and bridge.undo.undoStack == ["Move Nodes"]


@pytest.mark.parametrize("steps", [0, -1, 21, "1", 1.5])
def test_undo_step_count_is_checked(bridge, steps):
    create_three(bridge)
    assert "error" in bridge.change("/undo", steps=steps)
    assert len(bridge.undo.undoStack) == 3
