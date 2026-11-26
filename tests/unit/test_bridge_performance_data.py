"""Phase 4's new bridge routes (/performance, /chop, /dat, /sop-info, /contact-cells,
/timeline), run from the real bridge source against stand-ins for TouchDesigner.

The fakes model what research/PHASE4_LIVE_RESULTS.md recorded on a live build. They test our
logic; TouchDesigner's own behavior is covered by tests/integration/test_performance_data.py.
"""

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).parents[2] / "bridge" / "src" / "callbacks.py").read_text(encoding="utf-8")
TOKEN = "test-token-123"
BRIDGE = "/project1/td_mcp_bridge"


class FakeUndo:
    """One entry per block, newest first, replaying the block's callbacks on undo and redo."""

    def __init__(self):
        self.globalState = True
        self.done: list = []  # (label, [(callback, info)])
        self.undone: list = []
        self._open = None

    @property
    def undoStack(self):
        return [entry[0] for entry in self.done]

    @property
    def redoStack(self):
        return [entry[0] for entry in self.undone]

    def startBlock(self, name):
        self._open = (name, [])

    def addCallback(self, callback, info=None):
        assert self._open is not None
        self._open[1].append((callback, info))

    def endBlock(self):
        if self._open is None:
            raise RuntimeError("Cannot end non existent undo operation.")
        self.done.insert(0, self._open)
        self.undone.clear()
        self._open = None

    def undo(self):
        entry = self.done.pop(0)
        for callback, info in reversed(entry[1]):
            callback(True, info)
        self.undone.insert(0, entry)

    def redo(self):
        entry = self.undone.pop(0)
        for callback, info in entry[1]:
            callback(False, info)
        self.done.insert(0, entry)


class FakeTime:
    """The root Time COMP: the playhead wraps into rangeStart..rangeEnd, which setting start or
    end does not move."""

    def __init__(self, frame: float = 10, rate=60.0, start=1, end=600, play=False):
        self.rate, self.start, self.end, self.play = rate, start, end, play
        self.rangeStart, self.rangeEnd = start, end
        self._frame = frame

    @property
    def frame(self):
        return self._frame

    @frame.setter
    def frame(self, value):
        length = self.rangeEnd - self.rangeStart + 1
        self._frame = (value - self.rangeStart) % length + self.rangeStart


class FakeRoot:
    path = "/"

    def __init__(self, time=None):
        self.time = time


class FakePerfPar:
    def __init__(self, name):
        self.name, self.val = name, False


class FakeChannel:
    def __init__(self, name, values):
        self.name, self._values = name, list(values)

    def __getitem__(self, index):
        return self._values[index]

    def __len__(self):
        return len(self._values)

    def eval(self):
        return self._values[-1] if self._values else 0.0


# Toggle parameter -> channel, as read on the live build.
LIVE_PERF_CHANNELS = {
    "fps": "fps", "msec": "msec", "droppedframes": "dropped_frames",
    "gpumemused": "gpu_mem_used", "totalgpumem": "total_gpu_mem", "cpumemused": "cpu_mem_used",
    "activeops": "active_ops", "deactivatedops": "deactivated_ops", "totalops": "total_ops",
    "cookrate": "cookrate", "cookrealtime": "cookrealtime",
}


class FakePerformChop:
    """The temporary Perform CHOP: a channel exists only while its toggle is on."""

    family = "CHOP"

    def __init__(self, path, channel_values):
        self.path = path
        self._channel_values = dict(channel_values)
        self.par = SimpleNamespace(**{name: FakePerfPar(name) for name in LIVE_PERF_CHANNELS})
        self.cooked = False
        self.destroyed = False

    def cook(self, force=False, recurse=False, includeUtility=False):
        self.cooked = True

    def chan(self, name):
        on = {c for par, c in LIVE_PERF_CHANNELS.items() if getattr(self.par, par).val}
        if not self.cooked or name not in on or name not in self._channel_values:
            return None
        return FakeChannel(name, [self._channel_values[name]])

    def destroy(self):
        self.destroyed = True


class FakeBridgeBox:
    """Stands in for parent(): the bridge's own container, used by /performance to create
    and destroy its temporary Perform CHOP."""

    path = BRIDGE

    def __init__(self, perf_values):
        self._perf_values = perf_values
        self.children: dict[str, FakePerformChop] = {}
        self.storage: dict = {}

    def fetch(self, key, default, search=True):
        return self.storage.get(key, default)

    def store(self, key, value):
        self.storage[key] = value

    def op(self, name):
        return self.children.get(name)

    def create(self, optype, name):
        assert optype == "performCHOP", optype
        chop = FakePerformChop(self.path + "/" + name, self._perf_values)
        self.children[name] = chop
        self.made = chop
        return chop


class FakeOp:
    """A generic stand-in for an OP: path/type/family and findChildren only."""

    def __init__(self, path, *, family="COMP", type_="base", children=None,
                 cpu_cook_ms=None, gpu_cook_ms=None, total_cooks=None, cooked_this_frame=None):
        self.path, self.family, self.type = path, family, type_
        self.children = children or []
        self.cpuCookTime = cpu_cook_ms
        self.gpuCookTime = gpu_cook_ms
        self.totalCooks = total_cooks
        self.cookedThisFrame = cooked_this_frame

    def findChildren(self, maxDepth=None):
        return self.children


class FakeChopOp:
    family = "CHOP"

    def __init__(self, path, channels, rate=60.0, num_samples=None, bad_pattern=None):
        self.path = path
        self._channels = [FakeChannel(name, values) for name, values in channels.items()]
        self.rate = rate
        self.numChans = len(self._channels)
        self.numSamples = num_samples if num_samples is not None else (
            max((len(c) for c in self._channels), default=0)
        )
        self._bad_pattern = bad_pattern

    def chans(self, *patterns):
        if self._bad_pattern is not None and self._bad_pattern in patterns:
            raise ValueError("bad pattern")
        if not patterns:
            return list(self._channels)
        return [c for c in self._channels if any(fnmatch.fnmatchcase(c.name, p) for p in patterns)]


class FakeCell:
    def __init__(self, val):
        self.val = val


class FakeDatOp:
    family = "DAT"

    def __init__(self, path, rows=None, text=None, is_table=True):
        self.path = path
        self.isTable = is_table
        self._rows = rows or []
        self.text = text or ""
        self.numRows = len(self._rows)
        self.numCols = len(self._rows[0]) if self._rows else 0

    def __getitem__(self, key):
        row, col = key
        if 0 <= row < len(self._rows) and 0 <= col < len(self._rows[row]):
            return FakeCell(self._rows[row][col])
        return None


class FakeAttribute:
    def __init__(self, name):
        self.name = name


class _Unenumerable:
    """Stands in for a td.Attributes collection this build cannot iterate."""

    def __iter__(self):
        raise TypeError("not iterable on this build")


class FakeSopOp:
    family = "SOP"

    def __init__(self, path, *, num_points=0, num_prims=0, num_vertices=0, bounds=None,
                 point_groups=None, prim_groups=None, point_attribs=(), prim_attribs=None,
                 vertex_attribs=()):
        self.path = path
        self.numPoints, self.numPrims, self.numVertices = num_points, num_prims, num_vertices
        self.min, self.max, self.size = bounds or ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        self.pointGroups = point_groups or {}
        self.primGroups = prim_groups or {}
        self.pointAttribs = point_attribs
        self.primAttribs = prim_attribs if prim_attribs is not None else _Unenumerable()
        self.vertexAttribs = vertex_attribs


class FakeContactTop:
    family = "TOP"

    def __init__(self, path, width=4, height=2, png=b"\x89PNGfake"):
        self.path, self.width, self.height, self._png = path, width, height, png
        self.cook_calls: list[float | None] = []

    def saveByteArray(self, ext):
        return bytearray(self._png)

    def cook(self, force=False, recurse=False, includeUtility=False):
        self.cook_calls.append(force)


def load(ops, *, perf_values=None, root=None, token=TOKEN, audit_path=None):
    """exec the bridge source with fakes. `ops` maps a requested path to the object op()
    returns; `root` additionally serves op("/")."""
    perf_values = perf_values if perf_values is not None else {}
    box = FakeBridgeBox(perf_values)
    all_ops = dict(ops)
    if root is not None:
        all_ops["/"] = root
    undo = FakeUndo()
    ns = {
        "op": all_ops.get,
        "parent": lambda: box,
        "app": SimpleNamespace(build="1", version="2"),
        "project": SimpleNamespace(name="demo.toe", folder="/tmp/proj", cookRate=60.0),
        "ui": SimpleNamespace(undo=undo),
    }
    source = SOURCE.replace("__TEST_FIXTURES__", "0")
    if token is not None:
        source = source.replace("__TOKEN__", token)
    # Give change routes (only /timeline, in this file) a private audit log, never the real
    # user's ~/.td-mcp/audit.jsonl that an unsubstituted placeholder would fall back to.
    source = source.replace('"__AUDIT_LOG__"', json.dumps(str(audit_path or "/tmp/td-mcp-test-audit-unused.jsonl")))
    exec(compile(source, "callbacks.py", "exec"), ns)  # noqa: S102
    ns["_box"] = box
    ns["_undo"] = undo
    return ns


def call(ns, path, *, pars=None, method="GET", body=None):
    request = {
        "uri": {"path": path},
        "pars": pars or {},
        "method": method,
        "Host": "127.0.0.1:9991",
        "Authorization": f"Bearer {TOKEN}",
    }
    if body is not None:
        request["Content-Type"] = "application/json"
        request["data"] = json.dumps(body).encode()
    response: dict = {}
    ns["onHTTPRequest"](None, request, response)
    return response["statusCode"], json.loads(response["data"])


# --- /performance ----------------------------------------------------------------------------

ALL_PERF_VALUES = {
    "fps": 59.8, "msec": 16.7, "dropped_frames": 2.0, "gpu_mem_used": 256.0,
    "total_gpu_mem": 8192.0, "cpu_mem_used": 512.0, "active_ops": 12.0, "deactivated_ops": 1.0,
    "total_ops": 90.0, "cookrate": 60.0, "cookrealtime": 1.0,
}


def test_performance_reads_process_wide_values_from_a_temporary_perform_chop():
    root = FakeOp("/", family="COMP")
    ns = load({"/": root}, perf_values=ALL_PERF_VALUES, root=root)
    status, body = call(ns, "/performance", pars={"path": "/"})
    result = body["result"]
    assert status == 200
    assert (result["fps"], result["target_fps"], result["frame_ms"]) == (59.8, 60.0, 16.7)
    assert result["dropped_frames"] == 2.0
    assert result["realtime"] is True
    assert (result["cpu_mem_mb"], result["gpu_mem_mb"], result["gpu_mem_total_mb"]) == (512.0, 256.0, 8192.0)
    assert (result["active_ops"], result["deactivated_op_calls"], result["total_ops"]) == (12.0, 1.0, 90.0)
    assert result["unread_channels"] == []
    assert ns["_box"].made.destroyed


def test_performance_names_the_channels_it_could_not_read():
    root = FakeOp("/", family="COMP")
    values = dict(ALL_PERF_VALUES, msec=float("nan"))
    del values["total_ops"]
    ns = load({"/": root}, perf_values=values, root=root)
    _status, body = call(ns, "/performance", pars={"path": "/"})
    result = body["result"]
    assert result["unread_channels"] == ["msec", "total_ops"]
    assert result["frame_ms"] is None and result["total_ops"] is None


def test_performance_ranks_the_most_expensive_operators_by_cpu_cook_time():
    cheap = FakeOp("/p/cheap", family="TOP", cpu_cook_ms=0.1)
    pricey = FakeOp("/p/pricey", family="TOP", cpu_cook_ms=12.5)
    root = FakeOp("/p", family="COMP", children=[cheap, pricey])
    ns = load({"/p": root, "/": FakeOp("/", family="COMP")}, perf_values=ALL_PERF_VALUES, root=FakeOp("/"))
    status, body = call(ns, "/performance", pars={"path": "/p", "top_n": 1})
    assert status == 200
    top = body["result"]["top_cpu_cost"]
    assert len(top) == 1
    assert top[0]["path"] == "/p/pricey"
    assert top[0]["cpu_cook_ms"] == 12.5


def test_performance_excludes_ui_and_sys_at_the_project_root():
    hidden = FakeOp("/ui/x", family="COMP", cpu_cook_ms=999.0)
    root = FakeOp("/", family="COMP", children=[hidden])
    ns = load({"/": root}, perf_values=ALL_PERF_VALUES, root=root)
    status, body = call(ns, "/performance", pars={"path": "/"})
    result = body["result"]
    assert status == 200
    assert result["excluded"] == ["/ui", "/sys"]
    assert all(row["path"] != "/ui/x" for row in result["top_cpu_cost"])


def test_performance_rejects_a_bad_path():
    ns = load({}, root=FakeOp("/"))
    status, body = call(ns, "/performance", pars={"path": "/missing"})
    assert status == 200 and "no operator at exactly" in body["result"]["error"]


@pytest.mark.parametrize("pars", [{"top_n": "x"}, {"maxdepth": "x"}, {"top_n": "0"}, {"maxdepth": "-1"}])
def test_performance_rejects_bad_top_n_or_maxdepth(pars):
    root = FakeOp("/")
    ns = load({"/": root}, root=root)
    status, body = call(ns, "/performance", pars={"path": "/", **pars})
    assert status == 200 and "error" in body["result"]


# --- /chop -------------------------------------------------------------------------------------


def test_read_chop_returns_a_sample_range_and_pages_with_next_offset():
    chop = FakeChopOp("/p/c", {"tx": [0.0, 1.0, 2.0, 3.0, 4.0], "ty": [5.0, 6.0, 7.0, 8.0, 9.0]})
    ns = load({"/p/c": chop})
    status, body = call(ns, "/chop", pars={"path": "/p/c", "start": 1, "count": 2})
    result = body["result"]
    assert status == 200
    assert result["num_samples"] == 5 and result["start"] == 1 and result["count"] == 2
    assert result["next_offset"] == 3
    by_name = {c["name"]: c["values"] for c in result["channels"]}
    assert by_name == {"tx": [1.0, 2.0], "ty": [6.0, 7.0]}


def test_read_chop_filters_by_name_pattern():
    chop = FakeChopOp("/p/c", {"tx": [1.0], "ty": [2.0], "rz": [3.0]})
    ns = load({"/p/c": chop})
    status, body = call(ns, "/chop", pars={"path": "/p/c", "channels": "t?"})
    result = body["result"]
    assert status == 200
    assert result["matched_channels"] == 2
    assert {c["name"] for c in result["channels"]} == {"tx", "ty"}


def test_read_chop_last_page_has_no_next_offset():
    chop = FakeChopOp("/p/c", {"tx": [1.0, 2.0]})
    ns = load({"/p/c": chop})
    status, body = call(ns, "/chop", pars={"path": "/p/c", "count": 10})
    result = body["result"]
    assert status == 200
    assert result["count"] == 2 and result["next_offset"] is None


def test_read_chop_rejects_non_chop_family():
    ns = load({"/p/d": FakeDatOp("/p/d")})
    status, body = call(ns, "/chop", pars={"path": "/p/d"})
    assert status == 200 and "not a CHOP" in body["result"]["error"]


def test_read_chop_bad_pattern_is_refused():
    chop = FakeChopOp("/p/c", {"tx": [1.0]}, bad_pattern="[[[")
    ns = load({"/p/c": chop})
    status, body = call(ns, "/chop", pars={"path": "/p/c", "channels": "[[["})
    assert status == 200 and "bad channel pattern" in body["result"]["error"]


def test_read_chop_truncates_a_large_channel_count():
    channels = {f"c{i}": [0.0] for i in range(70)}
    chop = FakeChopOp("/p/c", channels)
    ns = load({"/p/c": chop})
    status, body = call(ns, "/chop", pars={"path": "/p/c"})
    result = body["result"]
    assert status == 200
    assert result["matched_channels"] == 70
    assert result["channels_truncated"] is True
    assert len(result["channels"]) == 64


def test_read_chop_takes_space_separated_patterns():
    chop = FakeChopOp("/p/c", {"c1": [1.0], "c2": [2.0], "c3": [3.0]})
    ns = load({"/p/c": chop})
    _status, body = call(ns, "/chop", pars={"path": "/p/c", "channels": "c1 c3"})
    assert [c["name"] for c in body["result"]["channels"]] == ["c1", "c3"]


def test_read_chop_names_values_that_are_not_finite():
    chop = FakeChopOp("/p/c", {"v": [float("nan"), float("inf"), float("-inf"), 1.0]})
    ns = load({"/p/c": chop})
    _status, body = call(ns, "/chop", pars={"path": "/p/c"})
    assert body["result"]["channels"][0]["values"] == ["nan", "inf", "-inf", 1.0]


def test_read_chop_shares_one_value_budget_across_channels():
    chop = FakeChopOp("/p/c", {f"c{i}": [0.0] * 500 for i in range(64)})
    ns = load({"/p/c": chop})
    _status, body = call(ns, "/chop", pars={"path": "/p/c", "count": 500})
    result = body["result"]
    assert result["count"] == 125 and result["next_offset"] == 125


# --- /dat --------------------------------------------------------------------------------------


def test_read_dat_table_pages_rows_with_next_row_offset():
    dat = FakeDatOp("/p/d", rows=[["a", "1"], ["b", "2"], ["c", "3"]])
    ns = load({"/p/d": dat})
    status, body = call(ns, "/dat", pars={"path": "/p/d", "row_offset": 1, "row_limit": 1})
    result = body["result"]
    assert status == 200
    assert result["is_table"] is True
    assert result["rows"] == [["b", "2"]]
    assert result["next_row_offset"] == 2


def test_read_dat_non_table_returns_text():
    dat = FakeDatOp("/p/d", text="hello\nworld", is_table=False)
    ns = load({"/p/d": dat})
    status, body = call(ns, "/dat", pars={"path": "/p/d"})
    result = body["result"]
    assert status == 200
    assert result["is_table"] is False
    assert result["rows"] == []
    assert result["text"] == "hello\nworld"


def test_read_dat_rejects_non_dat_family():
    ns = load({"/p/c": FakeChopOp("/p/c", {})})
    status, body = call(ns, "/dat", pars={"path": "/p/c"})
    assert status == 200 and "not a DAT" in body["result"]["error"]


def test_read_dat_truncates_wide_tables():
    rows = [[str(i) for i in range(70)]]
    dat = FakeDatOp("/p/d", rows=rows)
    ns = load({"/p/d": dat})
    status, body = call(ns, "/dat", pars={"path": "/p/d"})
    result = body["result"]
    assert status == 200
    assert result["num_cols"] == 70
    assert result["columns_truncated"] is True
    assert len(result["rows"][0]) == 64


def test_read_dat_clips_long_cell_text():
    dat = FakeDatOp("/p/d", rows=[["x" * 600]])
    ns = load({"/p/d": dat})
    status, body = call(ns, "/dat", pars={"path": "/p/d"})
    cell = body["result"]["rows"][0][0]
    assert status == 200
    assert len(cell) == 500 and cell.endswith("...")


def test_read_dat_offset_past_the_end_returns_no_rows():
    ns = load({"/p/d": FakeDatOp("/p/d", rows=[["a"], ["b"]])})
    _status, body = call(ns, "/dat", pars={"path": "/p/d", "row_offset": 500})
    result = body["result"]
    assert (result["row_offset"], result["row_limit"], result["rows"]) == (2, 0, [])
    assert result["next_row_offset"] is None


@pytest.mark.parametrize("is_table", [True, False])
def test_read_dat_never_returns_the_bridge_token(is_table):
    script = "x" * 480 + f' TOKEN = "{TOKEN}"'  # straddles the 500-character cell cut
    dat = FakeDatOp("/p/d", rows=[[script]], text=script, is_table=is_table)
    ns = load({"/p/d": dat})
    _status, body = call(ns, "/dat", pars={"path": "/p/d"})
    assert TOKEN[:8] not in json.dumps(body)


def test_read_dat_stops_at_the_reply_budget_and_pages_on():
    rows = [["x" * 500] * 64 for _ in range(5)]  # 32,000 characters each
    ns = load({"/p/d": FakeDatOp("/p/d", rows=rows)})
    _status, body = call(ns, "/dat", pars={"path": "/p/d"})
    result = body["result"]
    assert result["row_limit"] == 1 and result["next_row_offset"] == 1


# --- /sop-info -----------------------------------------------------------------------------


def test_read_sop_info_returns_counts_bounds_and_groups():
    sop = FakeSopOp(
        "/p/s", num_points=8, num_prims=6, num_vertices=24,
        bounds=((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0), (2.0, 4.0, 6.0)),
        point_groups={"left": 1, "__sopptgroup_pointclosure_": 1},
        prim_groups={"top": 1, "__sopptgroup_primclosure_": 1},
    )
    ns = load({"/p/s": sop})
    status, body = call(ns, "/sop-info", pars={"path": "/p/s"})
    result = body["result"]
    assert status == 200
    assert (result["num_points"], result["num_prims"], result["num_vertices"]) == (8, 6, 24)
    assert result["min"] == [-1.0, -2.0, -3.0]
    assert result["max"] == [1.0, 2.0, 3.0]
    assert result["size"] == [2.0, 4.0, 6.0]
    assert result["point_groups"] == ["left"] and result["prim_groups"] == ["top"]


def test_read_sop_info_attribute_names_are_best_effort():
    sop = FakeSopOp(
        "/p/s",
        point_attribs=[FakeAttribute("P"), FakeAttribute("N")],
        vertex_attribs=[FakeAttribute("uv")],
        # primAttribs defaults to an _Unenumerable(): this build cannot iterate it.
    )
    ns = load({"/p/s": sop})
    status, body = call(ns, "/sop-info", pars={"path": "/p/s"})
    result = body["result"]
    assert status == 200
    assert result["point_attributes"] == ["N", "P"]
    assert result["vertex_attributes"] == ["uv"]
    assert result["prim_attributes"] == []  # fell back cleanly, did not fail the call


def test_read_sop_info_rejects_non_sop_family():
    ns = load({"/p/c": FakeChopOp("/p/c", {})})
    status, body = call(ns, "/sop-info", pars={"path": "/p/c"})
    assert status == 200 and "not a SOP" in body["result"]["error"]


# --- /contact-cells ------------------------------------------------------------------------


def test_contact_cells_captures_several_tops_without_touching_the_timeline():
    a, b = FakeContactTop("/p/a"), FakeContactTop("/p/b")
    clock = FakeTime(frame=42.0)
    root = FakeRoot(time=clock)
    ns = load({"/p/a": a, "/p/b": b, "/": root}, root=root)
    status, body = call(ns, "/contact-cells", pars={"paths": "/p/a,/p/b"})
    result = body["result"]
    assert status == 200
    assert [c["path"] for c in result["cells"]] == ["/p/a", "/p/b"]
    assert all(c["frame"] is None for c in result["cells"])
    assert clock.frame == 42.0  # untouched: no frames were requested
    assert a.cook_calls == [] and b.cook_calls == []  # not force-cooked when not time-sampling


def test_contact_cells_with_frames_moves_and_restores_the_timeline():
    a = FakeContactTop("/p/a")
    clock = FakeTime(frame=10.0)
    root = FakeRoot(time=clock)
    seen_frames = []
    original_cook = a.cook

    def spying_cook(force=False, recurse=False, includeUtility=False):
        seen_frames.append(clock.frame)
        return original_cook(force=force, recurse=recurse, includeUtility=includeUtility)

    a.cook = spying_cook
    ns = load({"/p/a": a, "/": root}, root=root)
    status, body = call(ns, "/contact-cells", pars={"paths": "/p/a", "frames": "20,30"})
    result = body["result"]
    assert status == 200
    assert [c["frame"] for c in result["cells"]] == [20.0, 30.0]
    assert seen_frames == [20.0, 30.0]  # cooked once per requested frame, in order
    assert clock.frame == 10.0  # restored


def test_contact_cells_rejects_a_non_top_path():
    ns = load({"/p/d": FakeDatOp("/p/d")})
    status, body = call(ns, "/contact-cells", pars={"paths": "/p/d"})
    assert status == 200 and "not a TOP" in body["result"]["error"]


def test_contact_cells_requires_at_least_one_path():
    ns = load({})
    status, body = call(ns, "/contact-cells", pars={"paths": ""})
    assert status == 200 and "at least one" in body["result"]["error"]


def test_contact_cells_rejects_too_many_paths():
    ops = {f"/p/{i}": FakeContactTop(f"/p/{i}") for i in range(17)}
    ns = load(ops)
    status, body = call(ns, "/contact-cells", pars={"paths": ",".join(ops)})
    assert status == 200 and "at most 16 paths" in body["result"]["error"]


def test_contact_cells_rejects_too_many_frames():
    a = FakeContactTop("/p/a")
    root = FakeRoot(time=FakeTime())
    ns = load({"/p/a": a, "/": root}, root=root)
    frames = ",".join(str(i) for i in range(13))
    status, body = call(ns, "/contact-cells", pars={"paths": "/p/a", "frames": frames})
    assert status == 200 and "at most 12 frames" in body["result"]["error"]


def test_contact_cells_rejects_too_many_combined_cells():
    tops = {f"/p/{i}": FakeContactTop(f"/p/{i}") for i in range(8)}
    root = FakeRoot(time=FakeTime())
    ns = load({**tops, "/": root}, root=root)
    status, body = call(
        ns, "/contact-cells",
        pars={"paths": ",".join(tops), "frames": "1,2,3,4,5,6,7"},
    )
    assert status == 200 and "cell limit" in body["result"]["error"]


def test_contact_cells_scales_down_a_large_top():
    big = FakeContactTop("/p/big", width=1280, height=720)
    ns = load({"/p/big": big})
    ns_calls = []

    def fake_scaled(target, fmt):
        ns_calls.append((target.path, fmt))
        return b"small", 640, 360

    # The module is executed once per load(), so replacing the function affects this call.
    ns["_scaled_down"] = fake_scaled
    _status, body = call(ns, "/contact-cells", pars={"paths": "/p/big"})
    cell = body["result"]["cells"][0]
    assert ns_calls == [("/p/big", "png")]
    assert (cell["width"], cell["height"]) == (640, 360)


def test_contact_cells_refuses_a_top_too_large_to_download():
    huge = FakeContactTop("/p/huge", width=4096, height=4096)
    ns = load({"/p/huge": huge})
    _status, body = call(ns, "/contact-cells", pars={"paths": "/p/huge"})
    assert "pixel limit" in body["result"]["error"]


@pytest.mark.parametrize("frames, message", [("700", "outside"), ("0", "outside"), ("2.5", "whole")])
def test_contact_cells_refuses_frames_touchdesigner_would_change(frames, message):
    clock = FakeTime(frame=10)
    root = FakeRoot(time=clock)
    ns = load({"/p/a": FakeContactTop("/p/a"), "/": root}, root=root)
    _status, body = call(ns, "/contact-cells", pars={"paths": "/p/a", "frames": frames})
    assert message in body["result"]["error"]
    assert clock.frame == 10


def test_contact_cells_rejects_a_bad_frame_number():
    a = FakeContactTop("/p/a")
    root = FakeRoot(time=FakeTime())
    ns = load({"/p/a": a, "/": root}, root=root)
    status, body = call(ns, "/contact-cells", pars={"paths": "/p/a", "frames": "nope"})
    assert status == 200 and "bad frame number" in body["result"]["error"]


def test_contact_cells_rejects_a_bad_format():
    ns = load({"/p/a": FakeContactTop("/p/a")})
    status, body = call(ns, "/contact-cells", pars={"paths": "/p/a", "format": "gif"})
    assert status == 200 and "png or jpg" in body["result"]["error"]


# --- /timeline (POST change route) ----------------------------------------------------------


def timeline(tmp_path, clock, **body):
    root = FakeRoot(time=clock)
    ns = load({"/": root}, root=root, audit_path=tmp_path / "audit.jsonl")
    status, reply = call(ns, "/timeline", method="POST", body=body)
    assert status == 200, reply
    return ns, reply["result"]


def test_timeline_sets_the_given_fields_and_reports_previous_state(tmp_path):
    clock = FakeTime(frame=1, start=1, end=100)
    _ns, result = timeline(tmp_path, clock, play=True, frame=50)
    assert result["previous"] == {
        "frame": 1, "rate": 60.0, "start": 1, "end": 100, "range_start": 1, "range_end": 100,
        "play": False,
    }
    assert (result["timeline"]["frame"], result["timeline"]["play"]) == (50, True)
    assert result["undo_label"] is not None


def test_timeline_moves_the_playback_range_before_the_frame(tmp_path):
    clock = FakeTime(frame=10)
    clock.rangeStart, clock.rangeEnd = 80, 90  # stale sub range
    _ns, result = timeline(tmp_path, clock, frame=300, start=1, end=600)
    assert result["timeline"]["frame"] == 300
    assert (clock.rangeStart, clock.rangeEnd) == (1, 600)


def test_timeline_stops_playback_before_seeking(tmp_path):
    written = []

    class Recording(FakeTime):
        def __setattr__(self, name, value):
            written.append(name)
            super().__setattr__(name, value)

    clock = Recording(frame=10, play=True)
    written.clear()
    timeline(tmp_path, clock, frame=100, play=False)
    assert [n for n in written if n in ("play", "frame")] == ["play", "frame"]


def test_timeline_moves_a_playhead_left_outside_a_new_range(tmp_path):
    clock = FakeTime(frame=200)
    _ns, result = timeline(tmp_path, clock, start=300, end=400)
    assert result["timeline"]["frame"] == 300


def test_timeline_undo_and_redo_restore_the_state(tmp_path):
    clock = FakeTime(frame=120)
    ns, _result = timeline(tmp_path, clock, frame=40, start=10, end=50)

    ns["ui"].undo.undo()
    assert (clock.start, clock.end, clock.rangeStart, clock.rangeEnd, clock.frame) == (1, 600, 1, 600, 120)
    ns["ui"].undo.redo()
    assert (clock.start, clock.end, clock.rangeStart, clock.rangeEnd, clock.frame) == (10, 50, 10, 50, 40)


@pytest.mark.parametrize(
    "body, message",
    [
        ({"frame": 700}, "outside the playback range"),
        ({"frame": 2.5}, "whole number"),
        ({"start": 80, "end": 20}, "end before it starts"),
        ({"start": 700}, "end before it starts"),
    ],
)
def test_timeline_refuses_what_touchdesigner_would_silently_change(tmp_path, body, message):
    clock = FakeTime(frame=10)
    _ns, result = timeline(tmp_path, clock, **body)
    assert message in result["error"]
    assert (clock.frame, clock.start, clock.end) == (10, 1, 600)


def test_timeline_rolls_back_when_a_value_does_not_stick(tmp_path):
    class Stubborn(FakeTime):
        def __setattr__(self, name, value):
            if name == "play" and "play" in self.__dict__:
                return  # ignores changes
            super().__setattr__(name, value)

    clock = Stubborn(frame=10)
    _ns, result = timeline(tmp_path, clock, play=True, frame=20)
    assert "did not set" in result["error"] and "Nothing was changed" in result["error"]
    assert clock.frame == 10


def test_timeline_requires_at_least_one_field(tmp_path):
    root = FakeRoot(time=FakeTime())
    ns = load({"/": root}, root=root, audit_path=tmp_path / "audit.jsonl")
    status, body = call(ns, "/timeline", method="POST", body={})
    assert status == 200 and "at least one" in body["result"]["error"]


def test_timeline_rejects_unknown_fields(tmp_path):
    root = FakeRoot(time=FakeTime())
    ns = load({"/": root}, root=root, audit_path=tmp_path / "audit.jsonl")
    status, body = call(ns, "/timeline", method="POST", body={"loop": True})
    assert status == 200 and "unknown field" in body["result"]["error"]


def test_timeline_rejects_a_non_bool_play(tmp_path):
    root = FakeRoot(time=FakeTime())
    ns = load({"/": root}, root=root, audit_path=tmp_path / "audit.jsonl")
    status, body = call(ns, "/timeline", method="POST", body={"play": "yes"})
    assert status == 200 and "true or false" in body["result"]["error"]


def test_timeline_rejects_a_non_number_frame(tmp_path):
    root = FakeRoot(time=FakeTime())
    ns = load({"/": root}, root=root, audit_path=tmp_path / "audit.jsonl")
    status, body = call(ns, "/timeline", method="POST", body={"frame": "soon"})
    assert status == 200 and "whole number" in body["result"]["error"]


def test_timeline_is_refused_when_root_has_no_time_component(tmp_path):
    root = FakeRoot(time=None)
    ns = load({"/": root}, root=root, audit_path=tmp_path / "audit.jsonl")
    status, body = call(ns, "/timeline", method="POST", body={"frame": 5})
    assert status == 200 and "no Time Component" in body["result"]["error"]


def test_timeline_writes_a_started_and_ok_line_to_the_audit_log(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    root = FakeRoot(time=FakeTime())
    ns = load({"/": root}, root=root, audit_path=audit_path)
    status, _body = call(ns, "/timeline", method="POST", body={"frame": 5})
    assert status == 200
    lines = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [line["event"] for line in lines] == ["started", "ok"]
    assert all(line["action"] == "timeline" for line in lines)
