# TouchDesigner MCP Server: Build Plan

Type: plan
Status: open
Last verified: 2026-11-26 at `fd2cdbd`

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

- [x] Minimal bridge: Web Server DAT with one `GET /ping` endpoint
  - Verified 2026-11-25: throwaway probe component loaded into TouchDesigner 2025.33230; results in `research/PHASE0_SPIKE_RESULTS.md`.
- [x] Confirm whether callbacks run on the main thread
  - Verified: `MainThread` on every request (9 routes).
- [x] Confirm TOP to PNG bytes works (`TOP.saveByteArray`)
  - Verified: `.png` and `.jpg` both work (flat 640x360: PNG 1,830 bytes in about 12 ms, JPEG 4,312 bytes in about 4 ms).
- [x] Round trip: stdio MCP server to live TouchDesigner, returning an image Claude can see
  - Verified 2026-11-25: `tests/integration/test_roundtrip.py` starts the real stdio server (`python -m td_mcp.server`), calls `td_capture_top` against the bridge built from `bridge/src/` and gets back a PNG (640x360, expected colour). The saved image was opened and viewed. Glob and missing paths are refused. Unit tests (53) pass with ruff and pyright clean. `tests/integration` (2 tests, including an `lsof` assertion that the bridge listens on `127.0.0.1` only) passes live against a `--test-fixtures` build of the committed bridge source (verified at `e2db2cd`, 2026-11-25). Wildcard paths and non-TOP operators are refused live. Not covered live: the over-size limits and the pre-authentication error handler (unit tests with a stubbed TouchDesigner only).
- [x] Probe on build 2025.33230: whether a glob path such as `/project1/*` passed to `td.op()` resolves to an unnamed node (decides how strict our exact-path check must be)
  - Verified: yes, and arbitrarily (`glob_*` returned `glob_beta` though `glob_alpha` was created first). Every mutating tool must require `operator.path == requested path`.
- [x] Set the Web Server DAT's Local Address to `127.0.0.1` and confirm with `lsof` that nothing listens on other interfaces; confirm IPv4-only behavior (the prior art recorded `localhost` resolving to `::1` and failing)
  - Verified: `127.0.0.1:9990 (LISTEN)` IPv4 only; the LAN address (192.168.1.106) could not connect. The `localhost`-resolves-to-`::1` failure was not separately tested; use `127.0.0.1` in all configuration.
- [x] Evaluate the DAT's built-in Basic auth (`authenticateBasic`) and Secure (TLS) / Verify Client options as the bridge's authentication, versus our own bearer-token check
  - Evaluated 2026-11-25. Basic works but needs a plaintext password dict in the callbacks DAT, so it adds nothing over a bearer token. TLS worked once with a self-signed certificate, then 3 of 3 attempts with CA-signed certificates left the instance not listening or took the process down (cause not isolated). Verify Client has no documented trust-store parameter and one attempt took the instance down. Proposed outcome: keep the bearer token over loopback, in `decisions/0003-bridge-auth-bearer-over-loopback.md` (approved by the operator 2026-11-25).
- [x] Re-verify on 2025.33230 the TouchDesigner behaviors the prior art recorded (name-collision rename, error and warning stream formats, Script OP exceptions reaching no stream, `op(<dict>)` hang) before relying on them; list in `research/PRIOR_ART_TOUCHDESIGNER_MCP.md`
  - Verified 2026-11-25 (verdict table in `research/PRIOR_ART_TOUCHDESIGNER_MCP.md`). Rename, stream formats and Script OP exceptions hold. **`op(<dict>)` did not hang** with `{"a": 1}` (returned `None` in 5 ms); other payloads untried, so keep the type check on `path`. An Error DAT check was inconclusive.
- [x] Compare capture options: `saveByteArray('.jpg')` as the prior art uses it, versus a path that does not create a temporary operator when downscaling
  - Measured 2026-11-25 on a 1280x720 noise TOP downscaled to 512x288: temporary Resolution TOP 15 ms, `numpyArray()` + flip + `cv2.resize` + `cv2.imencode` 16 ms with no temporary operator, visually equivalent (0.35/255 mean difference). The array must be flipped (`arr[::-1]`). `cv2` and `numpy` ship in TouchDesigner's Python. Table in `research/PHASE0_SPIKE_RESULTS.md`.

### Phase 1: Core and perception (read-only)

- [x] Auth, error model, client with timeouts
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: `src/td_mcp/client.py` already had token auth, loopback/IPv4 checks, timeouts and `BridgeError` messages from Phase 0; extended with an `_checked_result` helper and methods for every new route below. `uv run pytest tests/unit/test_client.py` passes.
- [x] Server/`.tox` API-version gate (minimum and expected version, independent of the package version)
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: `MIN_API_VERSION`/`EXPECTED_API_VERSION` in `src/td_mcp/client.py`; the bridge's `API_VERSION` bumped to `0.1.0` (`bridge/src/callbacks.py`) and now carried at the top level of every reply, not just `/ping`'s result. A bridge below `MIN_API_VERSION` is refused with a rebuild message; a newer one is accepted. Unit-tested in `tests/unit/test_client.py` (`test_old_bridge_is_refused_and_told_to_rebuild`, `test_missing_api_version_is_reported`, `test_unreadable_api_version_is_reported`, `test_newer_bridge_than_expected_is_still_accepted`) and `tests/unit/test_bridge_callbacks.py::test_every_reply_carries_the_bridges_api_version_envelope`.
  - Note 2026-11-26: both versions raised to `0.2.0` at `369aabd`, because the `/errors` and `/snapshot` replies gained a required `excluded` field.
- [x] `td_get_errors` reads both the errors and warnings streams and reports `incomplete` rather than an all-clear when a stream is unread
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: `/errors` route in `bridge/src/callbacks.py` reads `errors(recurse=True)` and `warnings(recurse=True)` independently; either one raising marks it `unread` and forces `status: "incomplete"`. The response always carries a `caveats` note that a Script operator's own uncaught exception reaches neither stream (research/PHASE0_SPIKE_RESULTS.md), so a "clean" status is never read as an unconditional guarantee. Unit-tested in `tests/unit/test_bridge_callbacks.py` (`test_errors_route_*`, 4 tests).
- [x] Bridge rejects any non-loopback Host or Origin and sends no wildcard CORS header
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: `_handle()` in `bridge/src/callbacks.py` checks Host and Origin (when present) against `127.0.0.1` before even reading the token (decision 0003's "stronger defense" layer), and no response ever sets a CORS header. Unit-tested (`test_non_loopback_host_is_refused_before_the_token`, `test_non_loopback_origin_is_refused`, `test_absent_or_loopback_origin_is_allowed`, `test_bad_host_is_checked_before_the_token_is_even_read`, `test_no_response_ever_carries_a_cors_header`).
- [x] Tool annotations and `outputSchema` on every tool
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: all 7 tools carry `readOnlyHint`/`openWorldHint`. The 6 tools returning JSON data get a real `outputSchema` generated from `src/td_mcp/schemas.py` TypedDicts (confirmed via `fastmcp.Client.list_tools()` and `tests/unit/test_server.py::test_every_structured_tool_has_an_output_schema`). `td_capture_top` has none by MCP design: it returns an `Image` content block, not structured JSON, so there is no output schema to generate — not a gap.
- [x] Build the bridge `.tox` from the `bridge/src/` sources in CI and fail the build if the committed `.tox` embeds different text than the source (the prior art shipped a `.tox` older than its own scripts)
  - Adapted 2026-11-25 on the uncommitted working tree over `3df1a28`: this project never commits a `.tox` (`bridge/build/` is gitignored because the build embeds a fresh per-session token), so there is no committed binary to diff against — the prior art's exact failure mode cannot occur here. What remains is guarded instead: `bridge/build_spike.py` now exposes `prepare_script()`, which always re-reads `bridge/src/callbacks.py` from disk, and `tests/unit/test_build_spike.py` proves that (byte-identical output, and a "reads a decoy file instead of a monkeypatched path" check) without needing TouchDesigner. Added `.github/workflows/ci.yml`, which runs ruff, pyright and this test suite on push/PR. **Left unchecked**: this session has no way to trigger a real GitHub Actions run, so the workflow YAML itself (action versions, runner behavior) is implemented but unverified; only the underlying `uv run pytest` it calls has been run and passes locally. Verify on the first real PR and check this off then.
  - Verified 2026-11-26: the first real run passed on PR #3 at `15d10c9` (ruff, pyright, 126 unit tests including both in `tests/unit/test_build_spike.py`): https://github.com/KingRaver/touchme/actions/runs/45769767778. Checked off in the adapted form above: CI does not build a `.tox`, which needs TouchDesigner.
- [x] Pin the server launch to an exact version in the install guide instead of `@latest` (the prior art's users were exposed to the March 2026 axios compromise through `npx -y ...@latest`)
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: `guides/INSTALL.md` (new; first doc in `guides/`, so the folder is created per `CLAUDE.md`). `td-mcp` is not yet on PyPI, so it documents pinning to a chosen commit sha instead of tracking `main`, and the eventual `uvx td-mcp==<version>` form (never `@latest`) once released, plus a Claude Code config snippet and the bridge build/load steps.
- [x] `td_project_info`, `td_capture_top`, `td_get_errors`, `td_snapshot_graph`
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: all four implemented in `src/td_mcp/server.py` against new `bridge/src/callbacks.py` routes (`/project-info`, `/capture` unchanged, `/errors`, `/snapshot`), with unit tests at both the bridge layer (`tests/unit/test_bridge_callbacks.py`) and the tool layer (`tests/unit/test_server.py`). `td_project_info`'s "resolution" is intentionally not reported — see the note added to `specs/TOOL_CATALOG.md`. Not re-verified live against TouchDesigner in this session (no TD attached); that remains the job of `tests/integration` (opt-in).
  - Note 2026-11-26: run live against TouchDesigner 2025.33230. The first run found three problems in `td_get_errors` and `td_snapshot_graph`; fixed at `369aabd` and confirmed live. Evidence in `research/PHASE1_LIVE_RESULTS.md`.
- [x] `td_list_children`, `td_get_operator`, `td_get_parameters`
  - Verified 2026-11-25 on the uncommitted working tree over `3df1a28`: all three implemented against new routes (`/children`, `/operator`, `/parameters`), unit-tested at both layers. Same caveat as above: not re-verified live against TouchDesigner in this session.
  - Note 2026-11-26: run live against TouchDesigner 2025.33230. The first run found that parameter `min`/`max` were reported when not enforced; fixed at `369aabd` and confirmed live. Evidence in `research/PHASE1_LIVE_RESULTS.md`.
- [ ] Find why the bridge stops answering while TouchDesigner is in the background, then fix it or document the setting that prevents it, and correct the client's timeout message (found 2026-11-26, `research/PHASE1_LIVE_RESULTS.md`)

### Phase 2: Construction

- [x] Undo blocks and audit log (before any mutating tool ships)
  - Note 2026-11-26: the verified code is committed as `a569928`.
  - Verified 2026-11-26 on the uncommitted working tree over `a2d8312`: one named undo entry per change, rollback on failure and an audit line before and after, in `bridge/src/callbacks.py` (bridge API `0.3.0`). Live on 2025.33230: undo, redo, rollback, refusal to undo an entry the server did not make, and the log's contents and `0600` mode (`tests/integration/test_construction.py`). Unit tests only: an unwritable log and disabled undo both leave the project unchanged (`tests/unit/test_bridge_changes.py`). TouchDesigner behavior the design depends on is in `research/PHASE2_UNDO_RESULTS.md`.
- [x] `td_create_operator`, `td_connect`, `td_disconnect`, `td_set_parameters`, `td_delete_operator`, `td_layout`, `td_undo`, `td_redo`
  - Note 2026-11-26: the verified code is committed as `a569928`.
  - Verified 2026-11-26 on the uncommitted working tree over `a2d8312`: ruff and pyright clean, 268 unit tests pass, and all 21 integration tests passed in one live run through the stdio server with TouchDesigner in front. Behavior is specified in `specs/TOOL_CATALOG.md`. `td_set_parameters` sets bind references, not CHOP or DAT exports, which the parameter API exposes read-only. Not checked: how `td_layout`'s result looks in the editor.

### Phase 3: Knowledge

- [x] `td_describe_operator_type` from the live build
  - Note 2026-11-26: the verified code is committed as `fd2cdbd`.
  - Verified 2026-11-26 on the uncommitted working tree over `e257c98` (bridge API `0.4.0`): ruff and pyright clean, 340 unit tests pass, and all 37 integration tests (7 of them for this tool) passed in one live run through the stdio server on 2025.33230, with TouchDesigner in front. The run logged no camera permission request and no camera service activity. Evidence, including why hardware types are never created and are described from the help file, is in `research/PHASE3_KNOWLEDGE_RESULTS.md`.
- [x] Offline docs index and `td_search_docs`
  - Note 2026-11-26: the verified code is committed as `fd2cdbd`.
  - Verified 2026-11-26 on the uncommitted working tree over `e257c98`: `src/td_mcp/docs_index.py` indexes the installed build's offline help. 9 tests in `tests/integration/test_knowledge.py -k search_docs` passed through the real stdio server, building a fresh index; unit tests cover the parser, ranking and caching. TouchDesigner was not running, as the tool does not need it.

### Phase 4: Performance and data

- [x] `td_get_performance`, CHOP/DAT/SOP readers, `td_timeline`, `td_capture_contact_sheet`
  - Implemented 2026-11-26 on the uncommitted working tree over `5e90090` (bridge API `0.5.0`):
    all five tools (`td_get_performance`, `td_read_chop`, `td_read_dat`, `td_read_sop_info`,
    `td_timeline`, `td_capture_contact_sheet`) in `bridge/src/callbacks.py` and
    `src/td_mcp/{client,schemas,server}.py`. ruff and pyright clean; 407 unit tests pass
    (57 new, against fakes modeling the OP/CHOP/DAT/SOP/TimeCOMP/Perform CHOP classes as
    docs.derivative.ca's offline help — reachable this session without TouchDesigner running,
    from the installed build at `/Applications/TouchDesigner.app` — describes them). Behavior
    is specified in `specs/TOOL_CATALOG.md`.
  - **Left unchecked:** no licensed TouchDesigner was open this session, so none of this has
    been run live (`tests/integration` needs one). Specific open points for that run, beyond
    the usual first-live-pass fixes Phases 1-3 each needed:
    - `_perform_values()` (bridge) assumes the operator type string is `performCHOP` and that
      its channel-toggle parameters are named exactly like their channels (`fps`, `msec`,
      `droppedframes`, `gpumemused`, `totalgpumem`, `cpumemused`, `activeops`,
      `deactivatedops`, `totalops`, `cookrate`, `cookrealtime`); a freshly created, force-cooked
      Perform CHOP reporting correct values has not been checked.
    - `td_read_sop_info`'s attribute-name lists depend on a `td.Attributes` collection being
      iterable in Python, which docs.derivative.ca does not document either way (handled with a
      try/except fallback to an empty list, so a wrong guess degrades rather than fails).
    - `td_capture_contact_sheet`'s `frames` mode (direct `op('/').time.frame` sets plus
      `op.cook(force=True)`) has not been checked against a real stateful network (Feedback
      TOP, Trail CHOP) or a real time-dependent one, to confirm the caveat in its docstring is
      accurate and that the timeline is always restored even after bridge errors mid-call.
    - Whether setting `op('/').time`'s `frame`/`play`/`start`/`end` attributes creates a real
      TouchDesigner undo entry (as a parameter change does) is unknown; `td_timeline`'s
      `undo_label` is `null` if it does not, which is handled but unconfirmed either way.
    - CHOP/DAT reads were not checked against very large real channel/table data for
      performance or correctness of the sample-range and row/column paging math.
  - Note 2026-11-26: run live on 2025.33230 the same day, which found 13 problems the unit
    tests had passed. Fixed on the same uncommitted tree and checked off: ruff and pyright
    clean, 428 unit tests pass, and all 73 integration tests (36 new) passed in one run through
    the stdio server with TouchDesigner in front. The points above are answered in
    `research/PHASE4_LIVE_RESULTS.md`, which also lists two problems found in earlier phases'
    tools and not fixed, and what was not tested.
  - Note 2026-11-26: the two problems in earlier phases' tools (non-finite parameter values and
    space-separated name patterns) are fixed on the same uncommitted tree. ruff and pyright
    clean, 442 unit tests pass, and all 78 integration tests passed in one live run on
    2025.33230. Evidence in `research/PHASE4_LIVE_RESULTS.md`.

### Phase 5: Recipes and polish

- [ ] Recipe library, `td_instantiate_palette`, `td_apply_recipe`
- [ ] Optional event streaming over WebSocket
- [ ] Gated `td_exec_python` (if approved)
- [ ] Packaging: `uvx td-mcp`, install guide in `guides/`, versioned releases, Claude Code config snippet

### Phase 6: Evaluation

- [ ] 10 question eval set (`evals/td_eval.xml`)
- [ ] Visual regression suite: known networks, capture, compare to golden images

## Open questions

- **Q1.** Threading model of Web Server DAT callbacks. **Answered 2026-11-25:** `MainThread` on 2025.33230 (macOS). See `research/PHASE0_SPIKE_RESULTS.md`. Untested: behavior under concurrent or long-running requests.
- **Q2.** TouchDesigner license tier. **Answered 2026-11-25:** non-commercial (operator), confirmed by a 1280x1280 resolution cap measured on the instance.
- **Q3.** Which design domains to prioritize for recipes (generative visuals, audio-reactive, projection mapping, installations).
- **Q4.** Should `td_exec_python` ship at all, or be omitted for safety?
- **Q5.** Approve or amend the tool list and phase order.

## Risks

- Image return size costs context. Default to a modest size with an override.
- Parameter names change across builds. This is why `td_describe_operator_type` reads from the live app.
- Integration tests need a licensed TouchDesigner, so they cannot run in CI.
