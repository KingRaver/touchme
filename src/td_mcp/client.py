"""HTTP client for the TouchDesigner bridge (loopback only, bearer token)."""

from __future__ import annotations

import base64
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from td_mcp.errors import BridgeError

DEFAULT_URL = "http://127.0.0.1:9991"
DEFAULT_TIMEOUT_S = 10.0
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


@dataclass(frozen=True)
class Capture:
    data: bytes
    format: str
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
        token = self._token or _read_token()
        try:
            # trust_env=False: never route the token through HTTP_PROXY/ALL_PROXY.
            with httpx.Client(
                timeout=self._timeout_s, transport=self._transport, trust_env=False
            ) as http:
                resp = http.get(
                    self._base_url + route,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.TimeoutException as exc:
            raise BridgeError(
                f"TouchDesigner did not answer within {self._timeout_s:g}s. "
                "It may be busy cooking; try again."
            ) from exc
        except httpx.HTTPError as exc:
            raise BridgeError(
                f"Cannot reach the bridge at {self._base_url}. Is TouchDesigner open with "
                f"the bridge loaded? ({type(exc).__name__})"
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
        if resp.status_code != 200:
            raise BridgeError(f"Bridge error (HTTP {resp.status_code}): {body.get('error', body)}")
        return body

    def _result(self, route: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.get(route, params).get("result")
        if not isinstance(result, dict):
            raise BridgeError(_UNEXPECTED)
        return result

    def ping(self) -> dict[str, Any]:
        return self._result("/ping")

    def capture_top(self, path: str, fmt: str = "png") -> Capture:
        result = self._result("/capture", {"path": path, "format": fmt})
        if "error" in result:
            raise BridgeError(str(result["error"]))
        try:
            return Capture(
                data=base64.b64decode(result["data_b64"], validate=True),
                format=str(result["format"]),
                width=int(result["width"]),
                height=int(result["height"]),
            )
        except (KeyError, TypeError, ValueError) as exc:  # binascii.Error is a ValueError
            raise BridgeError(_UNEXPECTED) from exc
