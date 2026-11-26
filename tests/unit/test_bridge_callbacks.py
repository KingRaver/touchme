"""Run the real bridge source (bridge/src/callbacks.py) against stand-ins for TouchDesigner.

The source uses TouchDesigner's injected globals (op, parent, app, project), so it is exec'd
into a namespace that provides fakes. This tests our logic, not TouchDesigner's behavior; the
live behavior is covered by tests/integration.
"""

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).parents[2] / "bridge" / "src" / "callbacks.py").read_text(encoding="utf-8")
TOKEN = "test-token-123"


class FakeTop:
    family = "TOP"
    type = "constant"

    def __init__(self, path, width=64, height=32, png=b"\x89PNGfake"):
        self.path, self.width, self.height, self._png = path, width, height, png
        self.par = SimpleNamespace()  # the bridge sets parameters on the demo TOP

    def saveByteArray(self, ext):
        return bytearray(self._png)


class FakePar:
    def __init__(
        self,
        name,
        value,
        *,
        is_default=True,
        expr=None,
        mode="ParMode.CONSTANT",
        min_=None,
        max_=None,
        clamp_min=False,
        clamp_max=False,
        menu_names=None,
        menu_labels=None,
    ):
        self.name = name
        self._value = value
        self.isDefault = is_default
        self.expr = expr
        self.mode = mode
        self.min = min_
        self.max = max_
        self.clampMin = clamp_min
        self.clampMax = clamp_max
        self.menuNames = menu_names
        self.menuLabels = menu_labels

    def eval(self):
        return self._value


class FakeOp:
    """A generic stand-in for an OP/COMP: inspection and perception routes only."""

    def __init__(
        self,
        path,
        *,
        family="COMP",
        type_="base",
        inputs=None,
        outputs=None,
        children=None,
        builtin_pars=None,
        custom_pars=None,
        errors_text="",
        warnings_text="",
        errors_raises=False,
        warnings_raises=False,
    ):
        self.path = path
        self.family = family
        self.type = type_
        self.inputs = inputs or []
        self.outputs = outputs or []
        self.children = children or []
        self.builtinPars = builtin_pars or []
        self.customPars = custom_pars or []
        self._errors_text = errors_text
        self._warnings_text = warnings_text
        self._errors_raises = errors_raises
        self._warnings_raises = warnings_raises
        self.bypass = self.lock = self.selected = self.display = self.render = False
        self.time: FakeTime | None = None  # only set by tests that exercise /project-info

    def errors(self, recurse=False):
        if self._errors_raises:
            raise RuntimeError("errors() boom")
        return self._errors_text

    def warnings(self, recurse=False):
        if self._warnings_raises:
            raise RuntimeError("warnings() boom")
        return self._warnings_text

    def findChildren(self, maxDepth=None):
        return self.children

    def pars(self, pattern):
        return [p for p in (self.builtinPars + self.customPars) if fnmatch.fnmatch(p.name, pattern)]


class FakeTime:
    def __init__(self, frame=12.0, rate=60.0, start=1.0, end=600.0, play=True):
        self.frame, self.rate, self.start, self.end, self.play = frame, rate, start, end, play


def load(ops, *, token: str | None = TOKEN, fixtures="0", project=None, root=None):
    """exec the bridge source with fakes. `ops` maps a requested path to the object op() returns."""
    demo_children = {}

    class Box:
        path = "/bridge"

        def op(self, name):
            return demo_children.get(name)

        def create(self, _type, name):
            top = FakeTop("/bridge/" + name)
            demo_children[name] = top
            return top

    all_ops = dict(ops)
    if root is not None:
        all_ops["/"] = root

    ns = {
        "op": all_ops.get,
        "parent": lambda: Box(),
        "app": SimpleNamespace(build="1", version="2"),
        "project": project or SimpleNamespace(name="demo.toe", folder="/tmp/proj", cookRate=60.0),
        "constantTOP": object,
    }
    source = SOURCE.replace("__TEST_FIXTURES__", fixtures)
    if token is not None:
        source = source.replace("__TOKEN__", token)
    exec(compile(source, "callbacks.py", "exec"), ns)  # noqa: S102
    return ns


def call(
    ns,
    path,
    *,
    auth: str | None = f"Bearer {TOKEN}",
    pars=None,
    host: str | None = "127.0.0.1:9991",
    origin: str | None = None,
):
    request = {"uri": {"path": path}, "pars": pars or {}}
    if host is not None:
        request["Host"] = host
    if origin is not None:
        request["Origin"] = origin
    if auth is not None:
        request["Authorization"] = auth
    response = {}
    ns["onHTTPRequest"](None, request, response)
    return response["statusCode"], json.loads(response["data"])


def test_valid_token_reaches_the_route():
    status, body = call(load({}), "/ping")
    assert status == 200 and body["result"]["api_version"]


@pytest.mark.parametrize("auth", [None, "", "Bearer", "Bearer wrong", f"bearer {TOKEN}", TOKEN])
def test_missing_or_wrong_token_is_401(auth):
    status, _ = call(load({}), "/ping", auth=auth)
    assert status == 401


def test_unsubstituted_token_placeholder_refuses_everything():
    ns = load({}, token=None)
    for guess in ("Bearer __TOKEN__", None, "Bearer anything"):
        status, _ = call(ns, "/ping", auth=guess)
        assert status == 503


def test_unknown_route_is_404():
    assert call(load({}), "/nope")[0] == 404


def test_fixture_route_exists_only_in_test_builds():
    assert call(load({}, fixtures="0"), "/ensure-demo")[0] == 404
    assert call(load({}, fixtures="0"), "/foreign-edit")[0] == 404
    assert call(load({}, fixtures="1"), "/ensure-demo")[0] == 200


def test_capture_returns_the_image_for_an_exact_path():
    ns = load({"/p/out": FakeTop("/p/out", 64, 32)})
    status, body = call(ns, "/capture", pars={"path": "/p/out", "format": "png"})
    assert status == 200
    assert (body["result"]["width"], body["result"]["height"]) == (64, 32)
    assert body["result"]["data_b64"]


def test_glob_that_resolves_to_a_different_operator_is_refused():
    """td.op() returns an arbitrary match for a glob (Phase 0). Its own path will differ."""
    ns = load({"/p/*": FakeTop("/p/glob_beta")})
    status, body = call(ns, "/capture", pars={"path": "/p/*"})
    assert status == 200 and "no operator at exactly" in body["result"]["error"]


@pytest.mark.parametrize("path", [None, "", "relative/path", 123, ["/p/out"]])
def test_non_absolute_or_non_string_paths_never_reach_op(path):
    seen = []
    ns = load({})
    ns["op"] = lambda p: seen.append(p)
    _status, body = call(ns, "/capture", pars={"path": path})
    assert "no operator at exactly" in body["result"]["error"]
    assert seen == []


def test_non_top_is_refused():
    chop = FakeTop("/p/chop")
    chop.family = "CHOP"
    _status, body = call(load({"/p/chop": chop}), "/capture", pars={"path": "/p/chop"})
    assert "not a TOP" in body["result"]["error"]


def test_oversized_image_is_refused_before_encoding():
    big = FakeTop("/p/big", 4096, 4096)
    encoded = []
    big.saveByteArray = lambda ext: encoded.append(ext) or bytearray(b"x")  # type: ignore[method-assign]
    _status, body = call(load({"/p/big": big}), "/capture", pars={"path": "/p/big"})
    assert "pixel limit" in body["result"]["error"]
    assert encoded == []  # never encoded


def test_oversized_encoded_result_is_refused():
    top = FakeTop("/p/x", 64, 32, png=b"x" * (3 * 1024 * 1024 + 1))
    _status, body = call(load({"/p/x": top}), "/capture", pars={"path": "/p/x"})
    assert "byte limit" in body["result"]["error"]


def test_bad_format_is_refused():
    _status, body = call(load({"/p/x": FakeTop("/p/x")}), "/capture", pars={"path": "/p/x", "format": "gif"})
    assert "png or jpg" in body["result"]["error"]


def test_handler_exception_still_gets_a_reply():
    ns = load({})

    def boom(_pars):
        raise RuntimeError("kaboom")

    ns["ROUTES"]["/ping"] = boom
    status, body = call(ns, "/ping")
    assert status == 500 and body["error"] == "RuntimeError: kaboom"
    assert "traceback" not in body  # file paths and source layout stay in the textport


def test_failure_before_authentication_leaks_nothing_to_the_caller():
    """A malformed request raises before the token check; the caller must learn nothing."""
    ns = load({})
    response = {}
    ns["onHTTPRequest"](None, None, response)  # request.items() raises AttributeError
    assert response["statusCode"] == 500
    body = response["data"]
    assert "traceback" not in body and "AttributeError" not in body and "callbacks" not in body


def test_png_has_a_lower_pixel_limit_than_jpg():
    top = FakeTop("/p/big", 1600, 1200)  # 1.9M pixels: over the PNG limit, under the JPG limit
    ns = load({"/p/big": top})
    _s, png = call(ns, "/capture", pars={"path": "/p/big", "format": "png"})
    _s, jpg = call(ns, "/capture", pars={"path": "/p/big", "format": "jpg"})
    assert "pixel limit for png" in png["result"]["error"] and "use jpg" in png["result"]["error"]
    assert "data_b64" in jpg["result"]


# --- Host / Origin (decision 0003, Phase 1) -------------------------------------------------


@pytest.mark.parametrize("host", [None, "evil.com", "evil.com:9991", "127.0.0.1.evil.com:9991"])
def test_non_loopback_host_is_refused_before_the_token(host):
    status, body = call(load({}), "/ping", host=host)
    assert status == 403
    assert "Host" in body["error"]


@pytest.mark.parametrize("origin", ["http://evil.com", "https://evil.com:1234", "evil.com"])
def test_non_loopback_origin_is_refused(origin):
    status, body = call(load({}), "/ping", origin=origin)
    assert status == 403
    assert "Origin" in body["error"]


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:9991", "http://127.0.0.1"])
def test_absent_or_loopback_origin_is_allowed(origin):
    status, _ = call(load({}), "/ping", origin=origin)
    assert status == 200


def test_bad_host_is_checked_before_the_token_is_even_read():
    """A wrong token must not be the reported reason when the Host is already disqualifying."""
    status, body = call(load({}), "/ping", host="evil.com", auth="Bearer wrong")
    assert status == 403 and "token" not in body["error"]


def test_no_response_ever_carries_a_cors_header():
    response = {}
    ns = load({})
    ns["onHTTPRequest"](None, {"uri": {"path": "/ping"}, "Host": "127.0.0.1:9991", "Authorization": f"Bearer {TOKEN}"}, response)
    assert not any("access-control" in str(k).lower() for k in response)


def test_every_reply_carries_the_bridges_api_version_envelope():
    _status, body = call(load({}), "/nope")  # even a 404 gets the envelope-level field
    assert body["api_version"] == "0.3.0"


# --- /project-info -----------------------------------------------------------------------


def test_project_info_reports_build_project_and_timeline():
    root = FakeOp("/", type_="base", children=[])
    root.time = FakeTime(frame=42.0, rate=60.0, start=1.0, end=600.0, play=True)
    project = SimpleNamespace(name="show.toe", folder="/Users/op/show", cookRate=60.0)
    ns = load({}, project=project, root=root)
    status, body = call(ns, "/project-info")
    result = body["result"]
    assert status == 200
    assert result["project_name"] == "show.toe"
    assert result["project_folder"] == "/Users/op/show"
    assert result["cook_rate"] == 60.0
    assert result["timeline"] == {"frame": 42.0, "rate": 60.0, "start": 1.0, "end": 600.0, "play": True}


# --- /errors -------------------------------------------------------------------------------


def test_errors_route_is_clean_when_both_streams_are_empty():
    target = FakeOp("/p", errors_text="", warnings_text="")
    status, body = call(load({"/p": target}), "/errors", pars={"path": "/p"})
    result = body["result"]
    assert status == 200
    assert result["status"] == "clean" and result["unread_streams"] == []
    assert result["errors"] == [] and result["warnings"] == []
    assert result["caveats"]  # the Script-OP blind spot is always disclosed


def test_errors_route_parses_both_stream_formats():
    target = FakeOp(
        "/p",
        errors_text="/p/a:  Error: broke\n/p/b:  Error: also broke",
        warnings_text="/p/c:Warning: missing file",
    )
    _status, body = call(load({"/p": target}), "/errors", pars={"path": "/p"})
    result = body["result"]
    assert result["status"] == "has_issues"
    assert result["errors"] == [
        {"path": "/p/a", "message": "broke"},
        {"path": "/p/b", "message": "also broke"},
    ]
    assert result["warnings"] == [{"path": "/p/c", "message": "missing file"}]


def test_errors_route_reports_incomplete_when_a_stream_cannot_be_read():
    target = FakeOp("/p", errors_text="", warnings_raises=True)
    _status, body = call(load({"/p": target}), "/errors", pars={"path": "/p"})
    result = body["result"]
    assert result["status"] == "incomplete"
    assert result["unread_streams"] == ["warnings"]
    assert result["errors"] == []  # the stream that *was* read is still reported


def test_errors_route_refuses_an_inexact_path():
    _status, body = call(load({}), "/errors", pars={"path": "/missing"})
    assert "no operator at exactly" in body["result"]["error"]


# --- /snapshot -----------------------------------------------------------------------------


def test_snapshot_includes_the_root_and_its_descendants_with_wiring():
    leaf_a = FakeOp("/p/a", family="TOP", type_="constant")
    leaf_b = FakeOp("/p/b", family="TOP", type_="constant", inputs=[leaf_a])
    root = FakeOp("/p", family="COMP", type_="base", children=[leaf_a, leaf_b])
    _status, body = call(load({"/p": root}), "/snapshot", pars={"path": "/p"})
    result = body["result"]
    assert result["operator_count"] == 3
    paths = {o["path"] for o in result["operators"]}
    assert paths == {"/p", "/p/a", "/p/b"}
    b_entry = next(o for o in result["operators"] if o["path"] == "/p/b")
    assert b_entry["inputs"] == ["/p/a"]


def test_snapshot_rejects_a_negative_max_depth():
    root = FakeOp("/p")
    _status, body = call(load({"/p": root}), "/snapshot", pars={"path": "/p", "maxdepth": "-1"})
    assert "maxdepth" in body["result"]["error"]


# --- /children -----------------------------------------------------------------------------


def _demo_children():
    return [
        FakeOp("/p/top1", family="TOP", type_="constant"),
        FakeOp("/p/top2", family="TOP", type_="noise"),
        FakeOp("/p/chop1", family="CHOP", type_="lfo"),
    ]


def test_children_route_filters_by_family():
    root = FakeOp("/p", children=_demo_children())
    _status, body = call(load({"/p": root}), "/children", pars={"path": "/p", "family": "TOP"})
    result = body["result"]
    assert result["total"] == 2
    assert {c["path"] for c in result["children"]} == {"/p/top1", "/p/top2"}


def test_children_route_filters_by_type():
    root = FakeOp("/p", children=_demo_children())
    _status, body = call(load({"/p": root}), "/children", pars={"path": "/p", "type": "noise"})
    result = body["result"]
    assert result["total"] == 1 and result["children"][0]["path"] == "/p/top2"


def test_children_route_paginates():
    root = FakeOp("/p", children=_demo_children())
    _status, body = call(load({"/p": root}), "/children", pars={"path": "/p", "offset": "1", "limit": "1"})
    result = body["result"]
    assert result["total"] == 3 and result["offset"] == 1
    assert len(result["children"]) == 1


def test_children_route_rejects_bad_pagination():
    root = FakeOp("/p", children=_demo_children())
    _status, body = call(load({"/p": root}), "/children", pars={"path": "/p", "limit": "0"})
    assert "limit" in body["result"]["error"]


# --- /operator and /parameters --------------------------------------------------------------


def _op_with_pars():
    changed = FakePar("resolutionw", 640, is_default=False)
    default = FakePar("resolutionh", 360, is_default=True)
    return FakeOp(
        "/p/out",
        family="TOP",
        type_="constant",
        builtin_pars=[changed, default],
        inputs=[FakeOp("/p/in", family="TOP")],
        outputs=[],
    )


def test_operator_route_reports_only_non_default_parameters():
    target = _op_with_pars()
    _status, body = call(load({"/p/out": target}), "/operator", pars={"path": "/p/out"})
    result = body["result"]
    assert result["type"] == "constant" and result["family"] == "TOP"
    assert result["inputs"] == ["/p/in"]
    names = [p["name"] for p in result["non_default_parameters"]]
    assert names == ["resolutionw"]


def test_parameters_route_returns_full_detail_for_every_parameter():
    target = _op_with_pars()
    _status, body = call(load({"/p/out": target}), "/parameters", pars={"path": "/p/out"})
    result = body["result"]
    assert result["parameter_count"] == 2
    by_name = {p["name"]: p for p in result["parameters"]}
    assert by_name["resolutionw"]["value"] == 640
    assert by_name["resolutionw"]["is_default"] is False
    assert by_name["resolutionh"]["is_default"] is True


def test_parameters_route_name_pattern_filters_the_list():
    target = _op_with_pars()
    _status, body = call(load({"/p/out": target}), "/parameters", pars={"path": "/p/out", "name": "resolutionw"})
    result = body["result"]
    assert result["parameter_count"] == 1
    assert result["parameters"][0]["name"] == "resolutionw"


# --- Review fixes -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["127.0.0.1:80@evil.com", "http://127.0.0.1:1@evil.com", "a@127.0.0.1"])
def test_userinfo_cannot_smuggle_a_foreign_host_or_origin(value):
    assert call(load({}), "/ping", host=value)[0] == 403
    assert call(load({}), "/ping", origin=value)[0] == 403


def test_outer_fallback_error_still_carries_api_version():
    ns = load({})
    response = {}
    ns["onHTTPRequest"](None, None, response)
    assert json.loads(response["data"])["api_version"] == "0.3.0"


def test_multiline_message_continues_the_previous_row():
    target = FakeOp("/p", errors_text="/p/a:  Error: first line\nTraceback: line 3\n  detail: x")
    _s, body = call(load({"/p": target}), "/errors", pars={"path": "/p"})
    rows = body["result"]["errors"]
    assert len(rows) == 1 and rows[0]["path"] == "/p/a"
    assert "Traceback: line 3" in rows[0]["message"] and "detail: x" in rows[0]["message"]


def test_snapshot_clamps_depth_and_truncates_the_operator_list():
    kids = [FakeOp(f"/p/k{i}") for i in range(1100)]
    root = FakeOp("/p", children=kids)
    seen = []
    root.findChildren = lambda maxDepth=None: seen.append(maxDepth) or kids  # type: ignore[method-assign]
    _s, body = call(load({"/p": root}), "/snapshot", pars={"path": "/p", "maxdepth": "999"})
    result = body["result"]
    assert seen == [8] and result["max_depth"] == 8
    assert result["operator_count"] == 1000 and result["truncated"] is True


def test_snapshot_is_not_truncated_when_small():
    root = FakeOp("/p", children=[FakeOp("/p/a")])
    _s, body = call(load({"/p": root}), "/snapshot", pars={"path": "/p"})
    assert body["result"]["truncated"] is False


def test_children_reports_the_applied_limit_and_next_offset():
    kids = [FakeOp(f"/p/k{i}") for i in range(800)]
    ns = load({"/p": FakeOp("/p", children=kids)})
    _s, first = call(ns, "/children", pars={"path": "/p", "limit": "1000"})
    assert first["result"]["limit"] == 500 and first["result"]["next_offset"] == 500
    _s, last = call(ns, "/children", pars={"path": "/p", "offset": "500", "limit": "1000"})
    assert len(last["result"]["children"]) == 300 and last["result"]["next_offset"] is None


def test_unreadable_parameter_is_listed_not_dropped():
    class Broken(FakePar):
        def eval(self):
            raise RuntimeError("bad expression")

    broken = Broken("tx", 0, is_default=False)
    target = FakeOp("/p/x", builtin_pars=[broken, FakePar("ty", 1)])
    _s, body = call(load({"/p/x": target}), "/operator", pars={"path": "/p/x"})
    params = body["result"]["non_default_parameters"]
    assert [p["name"] for p in params] == ["tx"]
    assert params[0]["readable"] is False and params[0]["value"] is None


# --- Fixes from the live Phase 1 run (research/PHASE1_LIVE_RESULTS.md) ------------------------


def test_each_warning_from_one_operator_is_its_own_row():
    text = (
        '/p/a:Warning: Export not found for parameter "Width" (/p/x)\n'
        'Warning: Export not found for parameter "Height" (/p/y)\n'
        "/p/b:Warning: missing file"
    )
    target = FakeOp("/p", warnings_text=text)
    _s, body = call(load({"/p": target}), "/errors", pars={"path": "/p"})
    assert body["result"]["warnings"] == [
        {"path": "/p/a", "message": 'Export not found for parameter "Width" (/p/x)'},
        {"path": "/p/a", "message": 'Export not found for parameter "Height" (/p/y)'},
        {"path": "/p/b", "message": "missing file"},
    ]


def test_bare_error_lines_split_the_same_way():
    target = FakeOp("/p", errors_text="/p/a:  Error: one\nError: two\n  detail")
    _s, body = call(load({"/p": target}), "/errors", pars={"path": "/p"})
    assert body["result"]["errors"] == [
        {"path": "/p/a", "message": "one"},
        {"path": "/p/a", "message": "two\ndetail"},
    ]


def test_root_errors_leave_out_internal_networks_and_say_so():
    text = (
        "/ui/a:Warning: internal\nWarning: internal too\n"
        "/sys:Warning: internal\n"
        "/uiThing:Warning: mine\n"
        "/project1/a:Warning: also mine"
    )
    root = FakeOp("/", warnings_text=text)
    _s, body = call(load({}, root=root), "/errors", pars={"path": "/"})
    result = body["result"]
    assert [r["path"] for r in result["warnings"]] == ["/uiThing", "/project1/a"]
    assert result["excluded"] == ["/ui", "/sys"]


def test_root_errors_are_clean_when_only_internal_networks_have_issues():
    root = FakeOp("/", errors_text="/sys/a:  Error: internal", warnings_text="/ui/a:Warning: x")
    _s, body = call(load({}, root=root), "/errors")
    assert body["result"]["status"] == "clean"
    assert body["result"]["excluded"] == ["/ui", "/sys"]


def test_an_internal_network_can_be_asked_for_directly():
    target = FakeOp("/ui", warnings_text="/ui/a:Warning: internal")
    ns = load({"/ui": target})
    _s, errors = call(ns, "/errors", pars={"path": "/ui"})
    assert errors["result"]["excluded"] == [] and len(errors["result"]["warnings"]) == 1
    target.children = [FakeOp("/ui/a")]
    _s, snap = call(ns, "/snapshot", pars={"path": "/ui"})
    assert snap["result"]["excluded"] == [] and snap["result"]["operator_count"] == 2


def test_root_snapshot_leaves_out_internal_networks_before_the_cap():
    internal = [FakeOp(f"/ui/k{i}") for i in range(1100)]
    kids = [FakeOp("/ui"), *internal, FakeOp("/sys"), FakeOp("/project1"), FakeOp("/system")]
    root = FakeOp("/", children=kids)
    _s, body = call(load({}, root=root), "/snapshot", pars={"path": "/"})
    result = body["result"]
    assert [o["path"] for o in result["operators"]] == ["/", "/project1", "/system"]
    assert result["excluded"] == ["/ui", "/sys"] and result["truncated"] is False


@pytest.mark.parametrize(
    ("clamp_min", "clamp_max", "expected"),
    [(False, False, (None, None)), (True, False, (1, None)), (False, True, (None, 9)), (True, True, (1, 9))],
)
def test_parameter_limits_are_reported_only_when_clamped(clamp_min, clamp_max, expected):
    par = FakePar("w", 640, min_=1, max_=9, clamp_min=clamp_min, clamp_max=clamp_max)
    target = FakeOp("/p/x", builtin_pars=[par])
    _s, body = call(load({"/p/x": target}), "/parameters", pars={"path": "/p/x"})
    row = body["result"]["parameters"][0]
    assert (row["min"], row["max"]) == expected
