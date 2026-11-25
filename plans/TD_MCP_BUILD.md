# TouchDesigner MCP Server: Build Plan

Type: plan
Status: open
Last verified: 2026-11-25 at `no git repo yet`

This plan owns all progress for building the server. Other documents link here and do not restate status.

## Goal

A local MCP server that lets Claude perceive, act on, and iterate within a live TouchDesigner project. "Professional grade" here means a reliable feedback loop (see, act, check, adjust), safe mutation, and accurate knowledge of the installed build. It does not mean a guarantee of design quality. See `specs/ARCHITECTURE.md`.

## Agreed decisions

| ID | Date | Decision | Record |
|---|---|---|---|
| A1 | 2026-11-25 | Target is TouchDesigner | n/a |
| A2 | 2026-11-25 | Local stdio server | `decisions/0002-stdio-server-http-bridge.md` |
| A3 | 2026-11-25 | Python with FastMCP | `decisions/0001-python-fastmcp.md` |
| A4 | 2026-11-25 | Docs organized by purpose, following the docs-reorg taxonomy | `CLAUDE.md` |
| A5 | 2026-11-25 | Continue our own Python build and borrow ideas from the existing project, rather than adopting or forking it | `research/PRIOR_ART_TOUCHDESIGNER_MCP.md` |

## Inputs

- Prior art (source-level review): `research/PRIOR_ART_TOUCHDESIGNER_MCP.md`
- Environment facts: `research/TD_ENVIRONMENT.md`
- Architecture and constraints: `specs/ARCHITECTURE.md`
- Tools to build: `specs/TOOL_CATALOG.md`

## Phases

### Phase 0: Spike (de-risk)

- [ ] Minimal bridge: Web Server DAT with one `GET /ping` endpoint
- [ ] Confirm whether callbacks run on the main thread
- [ ] Confirm TOP to PNG bytes works (`TOP.saveByteArray`)
- [ ] Round trip: stdio MCP server to live TouchDesigner, returning an image Claude can see
- [ ] Probe on build 2025.33230: whether a glob path such as `/project1/*` passed to `td.op()` resolves to an unnamed node (decides how strict our exact-path check must be)
- [ ] Set the Web Server DAT's Local Address to `127.0.0.1` and confirm with `lsof` that nothing listens on other interfaces; confirm IPv4-only behavior (the prior art recorded `localhost` resolving to `::1` and failing)
- [ ] Evaluate the DAT's built-in Basic auth (`authenticateBasic`) and Secure (TLS) / Verify Client options as the bridge's authentication, versus our own bearer-token check
- [ ] Re-verify on 2025.33230 the TouchDesigner behaviors the prior art recorded (name-collision rename, error and warning stream formats, Script OP exceptions reaching no stream, `op(<dict>)` hang) before relying on them; list in `research/PRIOR_ART_TOUCHDESIGNER_MCP.md`
- [ ] Compare capture options: `saveByteArray('.jpg')` as the prior art uses it, versus a path that does not create a temporary operator when downscaling

### Phase 1: Core and perception (read-only)

- [ ] Auth, error model, client with timeouts
- [ ] Server/`.tox` API-version gate (minimum and expected version, independent of the package version)
- [ ] `td_get_errors` reads both the errors and warnings streams and reports `incomplete` rather than an all-clear when a stream is unread
- [ ] Bridge rejects any non-loopback Host or Origin and sends no wildcard CORS header
- [ ] Tool annotations and `outputSchema` on every tool
- [ ] Build the bridge `.tox` from the `bridge/src/` sources in CI and fail the build if the committed `.tox` embeds different text than the source (the prior art shipped a `.tox` older than its own scripts)
- [ ] Pin the server launch to an exact version in the install guide instead of `@latest` (the prior art's users were exposed to the March 2026 axios compromise through `npx -y ...@latest`)
- [ ] `td_project_info`, `td_capture_top`, `td_get_errors`, `td_snapshot_graph`
- [ ] `td_list_children`, `td_get_operator`, `td_get_parameters`

### Phase 2: Construction

- [ ] Undo blocks and audit log (before any mutating tool ships)
- [ ] `td_create_operator`, `td_connect`, `td_disconnect`, `td_set_parameters`, `td_delete_operator`, `td_layout`, `td_undo`, `td_redo`

### Phase 3: Knowledge

- [ ] `td_describe_operator_type` from the live build
- [ ] Offline docs index and `td_search_docs`

### Phase 4: Performance and data

- [ ] `td_get_performance`, CHOP/DAT/SOP readers, `td_timeline`, `td_capture_contact_sheet`

### Phase 5: Recipes and polish

- [ ] Recipe library, `td_instantiate_palette`, `td_apply_recipe`
- [ ] Optional event streaming over WebSocket
- [ ] Gated `td_exec_python` (if approved)
- [ ] Packaging: `uvx td-mcp`, install guide in `guides/`, versioned releases, Claude Code config snippet

### Phase 6: Evaluation

- [ ] 10 question eval set (`evals/td_eval.xml`)
- [ ] Visual regression suite: known networks, capture, compare to golden images

## Open questions

- **Q1.** Threading model of Web Server DAT callbacks. Answered by Phase 0. Supporting evidence only so far: the prior art touches operators directly inside the callback with no hand-off (`research/PRIOR_ART_TOUCHDESIGNER_MCP.md`), but it was observed on a newer build and does not say which thread.
- **Q2.** TouchDesigner license tier (non-commercial, commercial, pro). Needed to know capture resolution limits.
- **Q3.** Which design domains to prioritize for recipes (generative visuals, audio-reactive, projection mapping, installations).
- **Q4.** Should `td_exec_python` ship at all, or be omitted for safety?
- **Q5.** Approve or amend the tool list and phase order.

## Risks

- Image return size costs context. Default to a modest size with an override.
- Parameter names change across builds. This is why `td_describe_operator_type` reads from the live app.
- Integration tests need a licensed TouchDesigner, so they cannot run in CI.
