"""Live round trip: real stdio server process -> bridge in TouchDesigner -> image.

Opt-in (a plain `pytest` skips it). Needs TouchDesigner open with a bridge built using
`python3 bridge/build_spike.py --test-fixtures` loaded, and:
  TD_MCP_TOKEN_FILE=bridge/build/token.txt  (and TD_MCP_URL if not port 9991)
Run: uv run pytest tests/integration -m integration -p no:cacheprovider

Run without a token it fails, rather than skipping, so a missing setup is never read as a pass.
"""

import asyncio
import base64
import io
import os
import subprocess
import sys
from urllib.parse import urlsplit

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from PIL import Image

from td_mcp.client import BridgeClient

pytestmark = pytest.mark.integration


def test_bridge_listens_on_loopback_only():
    """The DAT's Local Address is written into a hand-made .parm file; prove TouchDesigner honored it."""
    port = urlsplit(BridgeClient().base_url).port
    out = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, check=False
    ).stdout
    listeners = [line.split()[-2] for line in out.splitlines()[1:]]
    assert listeners, f"nothing is listening on port {port}"
    assert all(name.startswith("127.0.0.1:") for name in listeners), listeners


def test_stdio_roundtrip_returns_visible_image():
    if not os.environ.get("TD_MCP_TOKEN_FILE") and not os.environ.get("TD_MCP_TOKEN"):
        pytest.fail("Set TD_MCP_TOKEN_FILE (see this file's docstring). Not skipping on purpose.")
    demo = BridgeClient().get("/ensure-demo")["result"]["demo_top"]

    async def run():
        transport = StdioTransport(
            command=sys.executable, args=["-m", "td_mcp.server"], env=dict(os.environ)
        )
        async with Client(transport) as client:
            return await client.call_tool("td_capture_top", {"path": demo, "format": "png"})

    result = asyncio.run(run())
    image = result.content[0]
    assert image.type == "image" and image.mime_type == "image/png"
    pixels = Image.open(io.BytesIO(base64.b64decode(image.data))).convert("RGB")
    assert pixels.size == (640, 360)
    center = pixels.getpixel((320, 180))
    assert isinstance(center, tuple)
    assert center == pytest.approx((230, 51, 26), abs=3)  # 0.9, 0.2, 0.1 of 255
