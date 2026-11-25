import base64

import httpx
import pytest

from td_mcp.client import BridgeClient
from td_mcp.errors import BridgeError

PNG = b"\x89PNG\r\n\x1a\nfake"


def _client(handler) -> BridgeClient:
    return BridgeClient(
        base_url="http://127.0.0.1:9991", token="t0k", transport=httpx.MockTransport(handler)
    )


def test_capture_top_decodes_image_and_sends_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"result": {"format": "png", "width": 4, "height": 2,
                             "data_b64": base64.b64encode(PNG).decode()}},
        )

    cap = _client(handler).capture_top("/project1/out1", "png")
    assert cap.data == PNG
    assert (cap.format, cap.width, cap.height) == ("png", 4, 2)
    assert seen["auth"] == "Bearer t0k"
    assert seen["query"] == {"path": "/project1/out1", "format": "png"}


def test_bridge_level_error_is_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"error": "no operator at exactly '/x/*'"}})

    with pytest.raises(BridgeError, match="no operator"):
        _client(handler).capture_top("/x/*")


def test_wrong_token_gives_actionable_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "missing or wrong bearer token"})

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
        (200, {}),  # no "result"
        (200, {"result": "nope"}),  # result is not an object
        (200, ["not", "an", "object"]),
        (500, ["not", "an", "object"]),
        (200, {"result": {"format": "png", "width": 1, "height": 1}}),  # no data_b64
        (200, {"result": {"format": "png", "width": "x", "height": 1, "data_b64": "AAAA"}}),
        (200, {"result": {"format": "png", "width": 1, "height": 1, "data_b64": "***"}}),
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
        return httpx.Response(401, json={"message": "proxy auth required"})

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
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"result": {"ok": True}}))
    BridgeClient(base_url="http://127.0.0.1:9991", token="t", transport=transport).ping()
    assert seen["trust_env"] is False
