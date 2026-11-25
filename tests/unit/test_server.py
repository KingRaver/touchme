import asyncio

from fastmcp import Client

from td_mcp import server
from td_mcp.client import Capture


def test_tool_is_registered_read_only():
    async def run():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(run())}
    assert "td_capture_top" in tools
    annotations = tools["td_capture_top"].annotations
    assert annotations is not None and annotations.read_only_hint is True


def test_jpg_is_labelled_image_jpeg(monkeypatch):
    jpg = b"\xff\xd8\xff\xe0fake"
    monkeypatch.setattr(
        server.BridgeClient, "capture_top",
        lambda self, path, fmt="png": Capture(data=jpg, format="jpg", width=1, height=1),
    )

    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool("td_capture_top", {"path": "/p/out", "format": "jpg"})

    image = asyncio.run(run()).content[0]
    assert image.type == "image" and image.mime_type == "image/jpeg"


def test_capture_tool_returns_an_mcp_image(monkeypatch):
    png = b"\x89PNG\r\n\x1a\nfake"
    monkeypatch.setattr(
        server.BridgeClient, "capture_top",
        lambda self, path, fmt="png": Capture(data=png, format="png", width=1, height=1),
    )

    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool("td_capture_top", {"path": "/project1/out1"})

    result = asyncio.run(run())
    image = result.content[0]
    assert image.type == "image"
    assert image.mime_type == "image/png"
