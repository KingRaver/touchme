# Architecture

Type: spec
Status: open
Last verified: 2026-11-25 at `e2db2cd`

How the TouchDesigner MCP server is structured and how it must behave. Tools are specified in `specs/TOOL_CATALOG.md`. Why these choices were made is in `decisions/`.

## Design principle

The server exists so Claude can **perceive, act, know, and iterate**:

1. **Perceive**: see rendered output, errors, performance and the operator graph.
2. **Act**: create, wire and parameterize operators reliably and safely.
3. **Know**: use correct operator and parameter names from real docs, not memory.
4. **Iterate**: render, judge, adjust, with undo available.

A tool that does not serve one of these four is cut.

## Components

```
Claude (Code / Desktop)
   │  MCP over stdio
   ▼
td-mcp server (Python, FastMCP, uv)      <- separate process
   │  HTTP/JSON (localhost, token auth)
   ▼
TD bridge component (.tox)               <- inside TouchDesigner
   Web Server DAT + callbacks module (embedded Python 3.11)
   │  main thread
   ▼
TouchDesigner project (ops, TOPs, CHOPs, ...)
```

The bridge is Python by necessity, because TouchDesigner scripting is Python. See `decisions/0001-python-fastmcp.md`.

## Threading

TouchDesigner's API is not thread-safe, so every `op()` / `par` access must run on the main thread.

**Verified** on TouchDesigner 2025.33230 (macOS, 2026-11-25): Web Server DAT callbacks run on the main thread, so handlers may touch operators directly. See `research/PHASE0_SPIKE_RESULTS.md`. Not verified: behavior under concurrent or long-running requests, other builds or platforms. If a build ever runs callbacks elsewhere, handlers must enqueue work and an Execute DAT frame callback must drain the queue.

Long operations must not freeze TouchDesigner. The bridge enforces a per-request time budget and returns partial results with a clear message.

## Security

- Bind to `127.0.0.1` only.
- A random per-session bearer token is generated in the bridge and passed to the server via an env var or a `0600` token file.
- `td_exec_python` is off by default, enabled by a custom parameter on the bridge COMP, and annotated destructive and open-world.
- Every mutating call runs in an undo block and is written to an audit log.
- Path-taking tools (save, load, export) are restricted to a configurable allow-listed root.

## Repository layout

Documentation lives in purpose-named top-level folders. Code folders (`src/`, `bridge/`, `tests/`) now exist; `evals/` is created with the first eval.

```
touchme/
  README.md  CLAUDE.md  CHANGELOG.md        # root files stay at root
  plans/        what is being built (each plan owns its progress)
  specs/        normative behavior (this file, the tool catalog)
  decisions/    why it is this way
  research/     verified facts and reference notes
  guides/       how to install, run, test (created with the first guide)
  audits/       findings as found (created with the first audit)
  incidents/    what went wrong (created with the first incident)

  src/td_mcp/   MCP server: server.py, client.py, tools/, schemas.py, errors.py, docs_index/
  bridge/       TD side: src/ (callbacks.py now; handlers/ and dispatch.py planned), build_spike.py,
                and build/ (the built .tox and its token; gitignored, never committed)
  tests/        unit/ (fake bridge) and integration/ (real TouchDesigner, opt-in)
  evals/        td_eval.xml (10 question evaluation set)
```

The `.tox` is binary. Bridge source stays as plain `.py` in `bridge/src/`, and a script rebuilds the `.tox`.

## Quality bar

- Tool descriptions are concise, with examples in field descriptions.
- Every response has structured output plus short text. Large data is paginated and capped.
- Errors state the next step (for example, an unknown operator type suggests the closest valid names).
- pyright, ruff and unit tests run in CI. Integration tests need a licensed TouchDesigner, so they are opt-in.
