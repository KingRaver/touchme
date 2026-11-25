"""Run the real bridge source (bridge/src/callbacks.py) against stand-ins for TouchDesigner.

The source uses TouchDesigner's injected globals (op, parent, app), so it is exec'd into a
namespace that provides fakes. This tests our logic, not TouchDesigner's behavior; the live
behavior is covered by tests/integration.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).parents[2] / "bridge" / "src" / "callbacks.py").read_text(encoding="utf-8")
TOKEN = "test-token-123"


class FakeTop:
    family = "TOP"

    def __init__(self, path, width=64, height=32, png=b"\x89PNGfake"):
        self.path, self.width, self.height, self._png = path, width, height, png
        self.par = SimpleNamespace()  # the bridge sets parameters on the demo TOP

    def saveByteArray(self, ext):
        return bytearray(self._png)


def load(ops, *, token: str | None = TOKEN, fixtures="0"):
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

    ns = {
        "op": ops.get,
        "parent": lambda: Box(),
        "app": SimpleNamespace(build="1", version="2"),
        "constantTOP": object,
    }
    source = SOURCE.replace("__TEST_FIXTURES__", fixtures)
    if token is not None:
        source = source.replace("__TOKEN__", token)
    exec(compile(source, "callbacks.py", "exec"), ns)  # noqa: S102
    return ns


def call(ns, path, *, auth: str | None = f"Bearer {TOKEN}", pars=None):
    request = {"uri": {"path": path}, "pars": pars or {}}
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
