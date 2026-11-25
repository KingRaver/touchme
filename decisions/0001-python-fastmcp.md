# 0001: Python with FastMCP for the server

Type: decision
Status: complete
Last verified: 2026-11-25 at `no git repo yet`

Decided 2026-11-25 by the operator, after a TypeScript vs Python comparison.

## Decision

Build the MCP server in Python using FastMCP and Pydantic, managed with `uv`.

## Why

- The TouchDesigner-side bridge must be Python (TouchDesigner scripting is Python), so one language means one set of schemas and no protocol drift.
- The hard parts of this project are TouchDesigner semantics (operator families, cook order, parameter expressions, main-thread access). Those are Python-shaped.
- Image handling for captures and contact sheets is easier with Pillow and numpy.
- `uv` is already installed (`research/TD_ENVIRONMENT.md`), and `uvx` gives a one-line install.

## Trade-offs accepted

- TypeScript SDK v2 has stricter static typing by default. We compensate with pyright and Pydantic.
- Distribution needs `uv` or Python rather than `npx`.

## Revisit if

The project needs to be published to npm, or the tool surface grows large enough that strict typing becomes the main bottleneck.
