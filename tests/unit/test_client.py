import base64
import json

import httpx
import pytest

from td_mcp.client import MIN_API_VERSION, BridgeClient
from td_mcp.errors import BridgeError

PNG = b"\x89PNG\r\n\x1a\nfake"


def _client(handler) -> BridgeClient:
    return BridgeClient(
        base_url="http://127.0.0.1:9991", token="t0k", transport=httpx.MockTransport(handler)
    )


def _ok(result, *, api_version=MIN_API_VERSION):
    """A well-formed 200 bridge envelope: every real reply carries api_version at this level."""
    return httpx.Response(200, json={"result": result, "api_version": api_version})


def test_capture_top_decodes_image_and_sends_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["query"] = dict(request.url.params)
        return _ok({"format": "png", "width": 4, "height": 2, "data_b64": base64.b64encode(PNG).decode()})

    cap = _client(handler).capture_top("/project1/out1", "png")
    assert cap.data == PNG
    assert (cap.format, cap.width, cap.height) == ("png", 4, 2)
    assert seen["auth"] == "Bearer t0k"
    assert seen["query"] == {"path": "/project1/out1", "format": "png"}


def test_bridge_level_error_is_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"error": "no operator at exactly '/x/*'"})

    with pytest.raises(BridgeError, match="no operator"):
        _client(handler).capture_top("/x/*")


def test_wrong_token_gives_actionable_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": "missing or wrong bearer token", "api_version": MIN_API_VERSION}
        )

    with pytest.raises(BridgeError, match="rejected the token"):
        _client(handler).ping()


def test_unreachable_bridge_gives_actionable_message():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(BridgeError, match="Is TouchDesigner open"):
        _client(handler).ping()


def test_timeout_message_mentions_busy():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(BridgeError, match="did not answer"):
        _client(handler).ping()


def test_missing_token_is_reported(monkeypatch):
    monkeypatch.delenv("TD_MCP_TOKEN", raising=False)
    monkeypatch.delenv("TD_MCP_TOKEN_FILE", raising=False)
    client = BridgeClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(BridgeError, match="No bridge token"):
        client.ping()


def test_token_file_must_be_private(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("abc\n")
    token.chmod(0o644)
    monkeypatch.delenv("TD_MCP_TOKEN", raising=False)
    monkeypatch.setenv("TD_MCP_TOKEN_FILE", str(token))
    client = BridgeClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(BridgeError, match="chmod 600"):
        client.ping()


@pytest.mark.parametrize(
    "status,payload",
    [
        (200, {"api_version": MIN_API_VERSION}),  # no "result"
        (200, {"result": "nope", "api_version": MIN_API_VERSION}),  # result is not an object
        (200, ["not", "an", "object"]),
        (500, ["not", "an", "object"]),
        (200, {"result": {"format": "png", "width": 1, "height": 1}, "api_version": MIN_API_VERSION}),  # no data_b64
        (
            200,
            {
                "result": {"format": "png", "width": "x", "height": 1, "data_b64": "AAAA"},
                "api_version": MIN_API_VERSION,
            },
        ),
        (
            200,
            {
                "result": {"format": "png", "width": 1, "height": 1, "data_b64": "***"},
                "api_version": MIN_API_VERSION,
            },
        ),
    ],
)
def test_malformed_replies_become_bridge_errors(status, payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    with pytest.raises(BridgeError, match="does not understand"):
        _client(handler).capture_top("/project1/out1")


def test_empty_token_file_is_reported(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("\n")
    token.chmod(0o600)
    monkeypatch.delenv("TD_MCP_TOKEN", raising=False)
    monkeypatch.setenv("TD_MCP_TOKEN_FILE", str(token))
    client = BridgeClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(BridgeError, match="is empty"):
        client.ping()


@pytest.mark.parametrize("url", ["http://example.com:9991", "http://192.168.1.106:9991"])
def test_non_loopback_url_is_refused(url, monkeypatch):
    monkeypatch.delenv("TD_MCP_ALLOW_REMOTE", raising=False)
    with pytest.raises(BridgeError, match="loopback only"):
        BridgeClient(base_url=url, token="t")


def test_ipv4_loopback_url_is_accepted():
    BridgeClient(base_url="http://127.0.0.1:9991", token="t")


@pytest.mark.parametrize("url", ["http://localhost:9991", "http://[::1]:9991"])
def test_localhost_and_ipv6_are_refused_with_the_reason(url):
    """The bridge is IPv4-only; these names would connect to the wrong address."""
    with pytest.raises(BridgeError, match="IPv4 only"):
        BridgeClient(base_url=url, token="t")


def test_remote_needs_opt_in_and_https(monkeypatch):
    monkeypatch.setenv("TD_MCP_ALLOW_REMOTE", "1")
    BridgeClient(base_url="https://example.com:9991", token="t")  # allowed
    with pytest.raises(BridgeError, match="must use https"):
        BridgeClient(base_url="http://example.com:9991", token="t")  # opt-in alone is not enough


@pytest.mark.parametrize("url", ["127.0.0.1:9991", "ftp://127.0.0.1", "http://"])
def test_malformed_url_gets_a_clear_message(url):
    with pytest.raises(BridgeError, match="is not a URL"):
        BridgeClient(base_url=url, token="t")


def test_401_from_something_else_is_not_blamed_on_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "proxy auth required", "api_version": MIN_API_VERSION})

    with pytest.raises(BridgeError, match="does not understand"):
        _client(handler).ping()


def test_client_never_trusts_proxy_environment(monkeypatch):
    """The token must never be routed through HTTP_PROXY / ALL_PROXY."""
    real_client = httpx.Client
    seen = {}

    def recording_client(*args, **kwargs):
        seen.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("td_mcp.client.httpx.Client", recording_client)
    transport = httpx.MockTransport(lambda r: _ok({"ok": True}))
    BridgeClient(base_url="http://127.0.0.1:9991", token="t", transport=transport).ping()
    assert seen["trust_env"] is False


# --- API version gate (Phase 1) -------------------------------------------------------------


def test_old_bridge_is_refused_and_told_to_rebuild():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"ok": True}, "api_version": "0.0.1"})

    with pytest.raises(BridgeError, match="older than"):
        _client(handler).ping()


def test_missing_api_version_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"ok": True}})

    with pytest.raises(BridgeError, match="did not report an API version"):
        _client(handler).ping()


def test_unreadable_api_version_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"ok": True}, "api_version": "not-a-version"})

    with pytest.raises(BridgeError, match="unreadable API version"):
        _client(handler).ping()


def test_newer_bridge_than_expected_is_still_accepted():
    """Only MIN_API_VERSION blocks; a newer bridge than this server was built against is fine."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"ok": True}, "api_version": "9.9.9"})

    assert _client(handler).ping() == {"ok": True}


# --- New Phase 1 routes ----------------------------------------------------------------------


def test_project_info_returns_the_result_object():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"project_name": "show.toe", "cook_rate": 60.0})

    assert _client(handler).project_info() == {"project_name": "show.toe", "cook_rate": 60.0}


def test_get_errors_sends_the_path_and_checks_for_an_error_result():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"status": "clean"})

    _client(handler).get_errors("/project1")
    assert seen["query"] == {"path": "/project1"}


def test_snapshot_graph_sends_path_and_maxdepth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"operators": []})

    _client(handler).snapshot_graph("/p", max_depth=3)
    assert seen["query"] == {"path": "/p", "maxdepth": "3"}


def test_list_children_omits_empty_filters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"children": []})

    _client(handler).list_children("/p")
    assert seen["query"] == {"path": "/p", "offset": "0", "limit": "100"}


def test_list_children_includes_family_and_type_when_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"children": []})

    _client(handler).list_children("/p", family="TOP", type="constant")
    assert seen["query"]["family"] == "TOP"
    assert seen["query"]["type"] == "constant"


def test_get_operator_raises_bridge_error_for_a_bad_path():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"error": "no operator at exactly '/missing'"})

    with pytest.raises(BridgeError, match="no operator"):
        _client(handler).get_operator("/missing")


def test_get_parameters_sends_optional_name_pattern():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"parameters": []})

    _client(handler).get_parameters("/p/out", name="reso*")
    assert seen["query"] == {"path": "/p/out", "name": "reso*"}


# --- performance and data (Phase 4) ------------------------------------------------------------


def test_get_performance_sends_path_top_n_and_maxdepth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"fps": 60.0, "top_cpu_cost": []})

    _client(handler).get_performance("/p", top_n=5, max_depth=2)
    assert seen["query"] == {"path": "/p", "top_n": "5", "maxdepth": "2"}


def test_read_chop_omits_channels_when_not_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"channels": []})

    _client(handler).read_chop("/p/c")
    assert seen["query"] == {"path": "/p/c", "start": "0", "count": "100"}


def test_read_chop_includes_channels_when_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"channels": []})

    _client(handler).read_chop("/p/c", channels="tx ty", start=10, count=5)
    assert seen["query"] == {"path": "/p/c", "start": "10", "count": "5", "channels": "tx ty"}


def test_read_dat_sends_row_offset_and_limit():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"rows": []})

    _client(handler).read_dat("/p/d", row_offset=20, row_limit=50)
    assert seen["query"] == {"path": "/p/d", "row_offset": "20", "row_limit": "50"}


def test_read_sop_info_sends_the_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"num_points": 0})

    _client(handler).read_sop_info("/p/s")
    assert seen["query"] == {"path": "/p/s"}


def test_contact_cells_decodes_each_cell():
    cell_png = b"\x89PNG fake cell"

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "format": "png",
                "cells": [
                    {
                        "path": "/p/a", "frame": 3.0, "width": 4, "height": 2,
                        "data_b64": base64.b64encode(cell_png).decode(),
                    }
                ],
            }
        )

    cells = _client(handler).contact_cells(["/p/a"], [3.0])
    assert len(cells) == 1
    cell = cells[0]
    assert (cell.path, cell.frame, cell.data, cell.width, cell.height) == ("/p/a", 3.0, cell_png, 4, 2)


def test_contact_cells_sends_comma_joined_paths_and_frames():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"cells": []})

    _client(handler).contact_cells(["/p/a", "/p/b"], [1.0, 2.0], fmt="jpg")
    assert seen["query"] == {"paths": "/p/a,/p/b", "frames": "1.0,2.0", "format": "jpg"}


def test_contact_cells_omits_frames_param_when_not_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return _ok({"cells": []})

    _client(handler).contact_cells(["/p/a"])
    assert seen["query"] == {"paths": "/p/a", "format": "png"}


def test_contact_cells_raises_on_a_malformed_reply():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"cells": [{"path": "/p/a"}]})  # no data_b64

    with pytest.raises(BridgeError):
        _client(handler).contact_cells(["/p/a"])


# --- Review fixes -----------------------------------------------------------------------------


def test_bridge_fallback_500_without_api_version_reports_the_real_error():
    """The bridge's outer fallback (and any foreign service) may send no api_version."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "unhandled bridge error; see textport"})

    with pytest.raises(BridgeError, match=r"HTTP 500.*unhandled bridge error"):
        _client(handler).ping()


def test_foreign_502_json_is_not_blamed_on_an_old_bridge():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "bad gateway"})

    with pytest.raises(BridgeError, match="HTTP 502"):
        _client(handler).ping()


def test_old_bridge_on_a_missing_route_still_gets_the_rebuild_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "no route /errors", "api_version": "0.0.1"})

    with pytest.raises(BridgeError, match="older than"):
        _client(handler).get_errors()


def test_host_or_origin_refusal_explains_loopback_only():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": "Host must be 127.0.0.1", "api_version": MIN_API_VERSION}
        )

    with pytest.raises(BridgeError, match="loopback Origins"):
        _client(handler).ping()


def test_bridge_version_other_than_expected_is_logged(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"ok": True}, api_version="9.9.9")

    with caplog.at_level("WARNING", logger="td_mcp"):
        _client(handler).ping()
    assert "9.9.9" in caplog.text


# --- changes (POST) -------------------------------------------------------------------------


def changing(handler):
    return BridgeClient(token="t", transport=httpx.MockTransport(handler))


def ok_change(result):
    return httpx.Response(200, json={"api_version": MIN_API_VERSION, "result": result})


@pytest.mark.parametrize(
    "call, route, body",
    [
        (
            lambda c: c.create_operator("/p", "noiseTOP"),
            "/create",
            {"parent": "/p", "type": "noiseTOP"},
        ),
        (
            lambda c: c.create_operator("/p", "noiseTOP", "n", 0, -10),
            "/create",
            {"parent": "/p", "type": "noiseTOP", "name": "n", "x": 0, "y": -10},
        ),
        (
            lambda c: c.connect("/p/a", "/p/b"),
            "/connect",
            {"source": "/p/a", "target": "/p/b", "source_output": 0, "target_input": 0},
        ),
        (
            lambda c: c.disconnect("/p/b", 2),
            "/disconnect",
            {"target": "/p/b", "target_input": 2},
        ),
        (
            lambda c: c.set_parameters("/p/a", [{"name": "amp", "value": 0}]),
            "/set-parameters",
            {"path": "/p/a", "parameters": [{"name": "amp", "value": 0}]},
        ),
        (lambda c: c.delete_operator("/p/a"), "/delete", {"path": "/p/a"}),
        (lambda c: c.layout("/p"), "/layout", {"path": "/p"}),
        (lambda c: c.undo(2), "/undo", {"steps": 2}),
        (lambda c: c.redo(), "/redo", {"steps": 1}),
        (lambda c: c.timeline(play=True, frame=50), "/timeline", {"play": True, "frame": 50}),
        (lambda c: c.timeline(frame=50.0), "/timeline", {"frame": 50.0}),
    ],
)
def test_changes_are_posted_as_json_with_the_token(call, route, body):
    seen = []

    def handler(request):
        seen.append(request)
        return ok_change({"done": True})

    assert call(changing(handler)) == {"done": True}
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", route)
    assert request.headers["authorization"] == "Bearer t"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == body


def test_a_refused_change_raises_the_bridges_reason():
    client = changing(lambda request: ok_change({"error": "/p is protected"}))
    with pytest.raises(BridgeError, match="/p is protected"):
        client.delete_operator("/p")


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError])
def test_a_lost_reply_to_a_change_says_the_outcome_is_unknown_and_does_not_retry(failure):
    calls = []

    def handler(request):
        calls.append(request)
        raise failure("lost", request=request)

    with pytest.raises(BridgeError, match="may or may not have been applied"):
        changing(handler).delete_operator("/p/a")
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
def test_a_change_that_never_connected_is_reported_as_unreachable(failure):
    def handler(request):
        raise failure("no", request=request)

    with pytest.raises(BridgeError, match="Cannot reach the bridge"):
        changing(handler).delete_operator("/p/a")


def test_describe_operator_type_sends_only_the_filters_given():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return ok_change({"type": "noiseTOP"})

    client = changing(handler)
    client.describe_operator_type("noiseTOP")
    client.describe_operator_type("noiseTOP", name="t?", page="Transform", offset=2, limit=3)
    assert seen == [
        {"type": "noiseTOP", "offset": "0", "limit": "60"},
        {"type": "noiseTOP", "offset": "2", "limit": "3", "name": "t?", "page": "Transform"},
    ]


def test_a_read_only_bridge_build_is_refused_before_its_reply_is_used():
    client = changing(
        lambda request: httpx.Response(404, json={"api_version": "0.2.0", "error": "no route"})
    )
    with pytest.raises(BridgeError, match="Rebuild and reload"):
        client.delete_operator("/p/a")
