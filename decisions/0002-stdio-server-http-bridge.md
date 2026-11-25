# 0002: stdio MCP server with an HTTP bridge into TouchDesigner

Type: decision
Status: complete
Last verified: 2026-11-25 at `no git repo yet`

Transport decided 2026-11-25 by the operator (local only, stdio). The bridge transport is the proposed default and stays open to change after the Phase 0 spike.

## Decision

- **Client to server:** MCP over stdio. The server runs locally and is launched by Claude Code or Claude Desktop.
- **Server to TouchDesigner:** JSON over HTTP to a Web Server DAT inside a `.tox`, on `127.0.0.1` with a bearer token.

## Why

- Local, single-user use makes stdio the simplest option, with no remote auth or deployment.
- A Web Server DAT is built into TouchDesigner, so the bridge adds no dependency to a project.
- A `.tox` drops into any project.

## Alternative kept in reserve

WebSocket DAT, if server-push (event streaming) proves necessary. Planned for Phase 5 in `plans/TD_MCP_BUILD.md`.

## Revisit if

The Phase 0 spike shows the Web Server DAT cannot run handlers on the main thread without a queue, or has unacceptable latency for image capture.
