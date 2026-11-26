"""HTTP client for the TouchDesigner bridge (loopback only, bearer token)."""

from __future__ import annotations

import base64
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from td_mcp.errors import BridgeError

_log = logging.getLogger("td_mcp")

DEFAULT_URL = "http://127.0.0.1:9991"
DEFAULT_TIMEOUT_S = 10.0
# The bridge's own API version (bridge/src/callbacks.py's API_VERSION), independent of this
# package's version. MIN is the oldest bridge build this client will talk to; EXPECTED is
# what it was built and tested against. A bridge below MIN is refused outright so a stale
# bridge fails with "rebuild it" instead of a confusing 404 on a route it does not have yet.
MIN_API_VERSION = "0.5.0"
EXPECTED_API_VERSION = "0.5.0"
# The bridge's Web Server DAT listens on IPv4 127.0.0.1 only (Phase 0), and "localhost" can
# resolve to ::1 first and be refused, so no other spelling is accepted.
_LOOPBACK_HOSTS = {"127.0.0.1"}
_IPV4_ONLY_ALIASES = {"localhost", "::1"}
_UNEXPECTED = (
    "The bridge sent a reply this server does not understand. The loaded bridge may be out "
    "of date, or another program is using the port. Rebuild and reload the bridge."
)


def _read_token() -> str:
    token = os.environ.get("TD_MCP_TOKEN", "").strip()
    if token:
        return token
    token_file = os.environ.get("TD_MCP_TOKEN_FILE", "").strip()
    if not token_file:
        raise BridgeError(
            "No bridge token. Set TD_MCP_TOKEN, or TD_MCP_TOKEN_FILE to a 0600 file."
        )
    path = Path(token_file).expanduser()
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise BridgeError(f"{path} is readable by other users. Run: chmod 600 {path}")
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BridgeError(f"Cannot read token file {path}: {exc}") from exc
    if not token:
        raise BridgeError(f"Token file {path} is empty. Rebuild the bridge to write a new one.")
    return token


def _parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _check_api_version(body: dict[str, Any]) -> None:
    """Raise unless the bridge's own reported API version meets MIN_API_VERSION.

    Every bridge reply carries `api_version` at the top level (bridge/src/callbacks.py's
    `reply()`), so this runs on every call, not just `/ping`.
    """
    reported = body.get("api_version")
    if not isinstance(reported, str) or not reported:
        raise BridgeError(
            "The bridge did not report an API version. Rebuild and reload the bridge "
            "(python3 bridge/build_spike.py) from the current bridge/src/ sources."
        )
    try:
        reported_t = _parse_version(reported)
    except ValueError as exc:
        raise BridgeError(f"The bridge reported an unreadable API version {reported!r}.") from exc
    if reported_t < _parse_version(MIN_API_VERSION):
        raise BridgeError(
            f"The bridge is API version {reported}, older than the {MIN_API_VERSION} this "
            "server requires. Rebuild and reload the bridge."
        )
    if reported != EXPECTED_API_VERSION:
        _log.warning(
            "Bridge API version %s differs from the %s this server was tested against.",
            reported,
            EXPECTED_API_VERSION,
        )


@dataclass(frozen=True)
class Capture:
    data: bytes
    format: str
    width: int
    height: int


@dataclass(frozen=True)
class ContactCell:
    path: str
    frame: float | None
    data: bytes
    width: int
    height: int


class BridgeClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("TD_MCP_URL") or DEFAULT_URL).rstrip("/")
        parts = urlsplit(self._base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BridgeError(
                f"TD_MCP_URL {self._base_url!r} is not a URL. Use a form like http://127.0.0.1:9991."
            )
        if parts.hostname in _IPV4_ONLY_ALIASES:
            raise BridgeError(
                f"Use http://127.0.0.1:<port>, not {parts.hostname!r}. The bridge listens on "
                "IPv4 only, and this name can resolve to an address it does not listen on."
            )
        if parts.hostname not in _LOOPBACK_HOSTS:
            if os.environ.get("TD_MCP_ALLOW_REMOTE") != "1":
                raise BridgeError(
                    f"Refusing to send the bridge token to {parts.hostname!r}: the bridge is "
                    "loopback only (127.0.0.1). Fix TD_MCP_URL, or set TD_MCP_ALLOW_REMOTE=1 "
                    "and use an https:// URL if you mean it."
                )
            if parts.scheme != "https":
                raise BridgeError(
                    "A non-loopback bridge must use https://. The bearer token would otherwise "
                    "cross the network in cleartext (decision 0003)."
                )
        self._token = token
        self._timeout_s = timeout_s
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def get(self, route: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a bridge route and return the decoded JSON object. Raises BridgeError."""
        return self._request("GET", route, params=params)

    def post(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a change to the bridge. Never retried: a repeat could apply the change twice."""
        return self._request("POST", route, payload=payload)

    def _request(
        self,
        method: str,
        route: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._token or _read_token()
        try:
            # trust_env=False: never route the token through HTTP_PROXY/ALL_PROXY.
            with httpx.Client(
                timeout=self._timeout_s, transport=self._transport, trust_env=False
            ) as http:
                resp = http.request(
                    method,
                    self._base_url + route,
                    params=params,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BridgeError(
                f"Cannot reach the bridge at {self._base_url}. Is TouchDesigner open with "
                f"the bridge loaded? ({type(exc).__name__})"
            ) from exc
        except httpx.HTTPError as exc:
            if method != "GET":
                # The request may have reached TouchDesigner, so the outcome is unknown.
                raise BridgeError(
                    f"No reply from the bridge ({type(exc).__name__}). The change may or may "
                    "not have been applied. Check with td_get_operator or td_snapshot_graph "
                    "before trying again."
                ) from exc
            if not isinstance(exc, httpx.TimeoutException):
                raise BridgeError(
                    f"Cannot reach the bridge at {self._base_url}. Is TouchDesigner open with "
                    f"the bridge loaded? ({type(exc).__name__})"
                ) from exc
            raise BridgeError(
                f"TouchDesigner did not answer within {self._timeout_s:g}s. "
                "It may be busy cooking; try again."
            ) from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise BridgeError(f"The bridge returned non-JSON (HTTP {resp.status_code}).") from exc
        if not isinstance(body, dict):
            raise BridgeError(_UNEXPECTED)
        if resp.status_code == 401:
            # Only the bridge's own {"error": ...} body counts as a token rejection; a 401 from
            # something else on the port is a different problem.
            if isinstance(body.get("error"), str):
                raise BridgeError("The bridge rejected the token. Rebuild or reload the bridge.")
            raise BridgeError(_UNEXPECTED)
        if resp.status_code == 403 and isinstance(body.get("error"), str):
            raise BridgeError(
                f"The bridge refused this request ({body['error']}). It accepts only Host "
                "127.0.0.1 and loopback Origins (decision 0003), so a remote or reverse-proxied "
                "bridge is not supported."
            )
        if resp.status_code != 200:
            # A real bridge error body may lack api_version (the outer fallback of an older
            # build), and a foreign service never has one, so only check it when present:
            # an old bridge still gets the rebuild message, anything else gets the HTTP error.
            if "api_version" in body:
                _check_api_version(body)
            raise BridgeError(f"Bridge error (HTTP {resp.status_code}): {body.get('error', body)}")
        _check_api_version(body)
        return body

    def _result(self, route: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.get(route, params).get("result")
        if not isinstance(result, dict):
            raise BridgeError(_UNEXPECTED)
        return result

    def _checked_result(self, route: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Like `_result`, but also turns a bridge-level `{"error": ...}` into a BridgeError."""
        result = self._result(route, params)
        if "error" in result:
            raise BridgeError(str(result["error"]))
        return result

    def _change(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a change. Fields that are None are left out; a bridge refusal raises."""
        body = {key: value for key, value in payload.items() if value is not None}
        result = self.post(route, body).get("result")
        if not isinstance(result, dict):
            raise BridgeError(_UNEXPECTED)
        if "error" in result:
            raise BridgeError(str(result["error"]))
        return result

    def create_operator(
        self,
        parent: str,
        type: str,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        return self._change(
            "/create", {"parent": parent, "type": type, "name": name, "x": x, "y": y}
        )

    def connect(
        self, source: str, target: str, source_output: int = 0, target_input: int = 0
    ) -> dict[str, Any]:
        return self._change(
            "/connect",
            {
                "source": source,
                "target": target,
                "source_output": source_output,
                "target_input": target_input,
            },
        )

    def disconnect(self, target: str, target_input: int = 0) -> dict[str, Any]:
        return self._change("/disconnect", {"target": target, "target_input": target_input})

    def set_parameters(self, path: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
        return self._change("/set-parameters", {"path": path, "parameters": parameters})

    def delete_operator(self, path: str) -> dict[str, Any]:
        return self._change("/delete", {"path": path})

    def layout(self, path: str) -> dict[str, Any]:
        return self._change("/layout", {"path": path})

    def undo(self, steps: int = 1) -> dict[str, Any]:
        return self._change("/undo", {"steps": steps})

    def redo(self, steps: int = 1) -> dict[str, Any]:
        return self._change("/redo", {"steps": steps})

    def ping(self) -> dict[str, Any]:
        return self._result("/ping")

    def capture_top(self, path: str, fmt: str = "png") -> Capture:
        result = self._checked_result("/capture", {"path": path, "format": fmt})
        try:
            return Capture(
                data=base64.b64decode(result["data_b64"], validate=True),
                format=str(result["format"]),
                width=int(result["width"]),
                height=int(result["height"]),
            )
        except (KeyError, TypeError, ValueError) as exc:  # binascii.Error is a ValueError
            raise BridgeError(_UNEXPECTED) from exc

    def project_info(self) -> dict[str, Any]:
        return self._checked_result("/project-info")

    def get_errors(self, path: str = "/") -> dict[str, Any]:
        return self._checked_result("/errors", {"path": path})

    def snapshot_graph(self, path: str = "/", max_depth: int = 1) -> dict[str, Any]:
        return self._checked_result("/snapshot", {"path": path, "maxdepth": max_depth})

    def list_children(
        self,
        path: str = "/",
        family: str | None = None,
        type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"path": path, "offset": offset, "limit": limit}
        if family:
            params["family"] = family
        if type:
            params["type"] = type
        return self._checked_result("/children", params)

    def get_operator(self, path: str) -> dict[str, Any]:
        return self._checked_result("/operator", {"path": path})

    def describe_operator_type(
        self,
        type: str,
        name: str | None = None,
        page: str | None = None,
        offset: int = 0,
        limit: int = 60,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"type": type, "offset": offset, "limit": limit}
        if name:
            params["name"] = name
        if page:
            params["page"] = page
        return self._checked_result("/describe-type", params)

    def get_parameters(self, path: str, name: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"path": path}
        if name:
            params["name"] = name
        return self._checked_result("/parameters", params)

    def get_performance(self, path: str = "/", top_n: int = 20, max_depth: int = 8) -> dict[str, Any]:
        return self._checked_result(
            "/performance", {"path": path, "top_n": top_n, "maxdepth": max_depth}
        )

    def read_chop(
        self, path: str, channels: str | None = None, start: int = 0, count: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"path": path, "start": start, "count": count}
        if channels:
            params["channels"] = channels
        return self._checked_result("/chop", params)

    def read_dat(self, path: str, row_offset: int = 0, row_limit: int = 100) -> dict[str, Any]:
        return self._checked_result(
            "/dat", {"path": path, "row_offset": row_offset, "row_limit": row_limit}
        )

    def read_sop_info(self, path: str) -> dict[str, Any]:
        return self._checked_result("/sop-info", {"path": path})

    def timeline(
        self,
        play: bool | None = None,
        frame: float | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> dict[str, Any]:
        return self._change("/timeline", {"play": play, "frame": frame, "start": start, "end": end})

    def contact_cells(
        self, paths: list[str], frames: list[float] | None = None, fmt: str = "png"
    ) -> list[ContactCell]:
        params: dict[str, Any] = {"paths": ",".join(paths), "format": fmt}
        if frames:
            params["frames"] = ",".join(str(f) for f in frames)
        result = self._checked_result("/contact-cells", params)
        try:
            cells = [
                ContactCell(
                    path=str(cell["path"]),
                    frame=float(cell["frame"]) if cell.get("frame") is not None else None,
                    data=base64.b64decode(cell["data_b64"], validate=True),
                    width=int(cell["width"]),
                    height=int(cell["height"]),
                )
                for cell in result["cells"]
            ]
        except (KeyError, TypeError, ValueError) as exc:  # binascii.Error is a ValueError
            raise BridgeError(_UNEXPECTED) from exc
        return cells
