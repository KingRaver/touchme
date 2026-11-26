# Changelog

Newest first. Progress and evidence for planned work live in [plans/TD_MCP_BUILD.md](plans/TD_MCP_BUILD.md); this file records what changed and why.

## 2026-11-26: Phase 2, construction

### Added
- Eight tools that change the open project: `td_create_operator`, `td_connect`, `td_disconnect`, `td_set_parameters`, `td_delete_operator`, `td_layout`, `td_undo`, `td_redo`. Behavior is specified in `specs/TOOL_CATALOG.md`.
- Each successful change is one TouchDesigner undo entry. A change that fails part way is rolled back and cannot be redone. `td_undo` and `td_redo` stop at any entry the server did not make.
- Audit log of every change the bridge is asked to make, including refused ones, in `~/.td-mcp/audit.jsonl` (mode `0600`; `build_spike.py --audit-log PATH` moves it). If it cannot be written, nothing is changed.
- Bridge `POST` routes with a JSON body. The bridge refuses to change its own component, `/ui` or `/sys`, and refuses to delete `/`, `/local` or anything containing itself.
- `research/PHASE2_UNDO_RESULTS.md`: how undo, creation, wiring and parameters behave on the live build.

### Changed
- The bridge is no longer read-only. The bridge `API_VERSION` and the server's minimum are now `0.3.0`, so a loaded `0.2.0` bridge is refused: rebuild it and load the new `.tox`.
- A change whose reply is lost is not retried; the error says it may or may not have been applied.

### Security
- A parameter expression is Python that TouchDesigner runs, so `td_set_parameters` can run code. It is annotated destructive and open-world. The protected paths guard against mistakes, not against a caller that means harm.

### Known gaps
- The bridge stops answering in some cases while TouchDesigner is in the background; seen again during the live run. The cause is not confirmed.
- `td_set_parameters` hides password values, but `td_get_parameters` and `td_get_operator` still return them.
- Not checked live: disabled undo, an unwritable audit log, a lost reply, a real hand edit between server changes, how `td_layout` output looks in the editor, and wiring through COMP In/Out operators or in the `POP` family.

## 2026-11-26: Type-check the bridge

### Changed
- Pyright now checks `bridge/` as well as `src/` and `tests/`, locally and in CI.
- `bridge/src/callbacks.py` declares the TouchDesigner-injected names it uses (`op`, `parent`, `app`, `project`, `constantTOP`) under `if TYPE_CHECKING:`, so editors and Pyright no longer report them as undefined. The block never runs in TouchDesigner.

### Fixed
- `bridge/build_spike.py` no longer crashes under `python -OO`, which strips the docstring it takes its help description from.

## 2026-11-26: Phase 1, core and perception (read-only)

### Added
- Seven read-only tools with annotations (`readOnlyHint`/`openWorldHint`): `td_project_info`, `td_capture_top`, `td_get_errors`, `td_snapshot_graph`, `td_list_children`, `td_get_operator`, `td_get_parameters`. The six that return JSON carry an `outputSchema` generated from `src/td_mcp/schemas.py`; `td_capture_top` returns an image block and has none.
- Server/`.tox` API-version gate: `MIN_API_VERSION` and `EXPECTED_API_VERSION` in `src/td_mcp/client.py`, independent of the package version. A bridge older than the minimum is refused with a rebuild message. The bridge `API_VERSION` is now `0.2.0` and is carried at the top level of every reply.
- Bridge routes `/project-info`, `/errors`, `/snapshot`, `/children`, `/operator`, `/parameters` in `bridge/src/callbacks.py`.
- `td_get_errors` reads the errors and warnings streams independently and reports `incomplete` instead of an all-clear when either is unread.
- Bridge rejects any non-loopback `Host` or `Origin` before it reads the token, and never sends a CORS header.
- `guides/INSTALL.md`: install and launch guide that pins the server to an exact version or commit rather than `@latest`.
- `.github/workflows/ci.yml`: ruff, pyright and unit tests on push to `main` and on pull requests.
- `tests/unit/test_build_spike.py`: guards that the bridge build always embeds the current `bridge/src/callbacks.py` text.

### Changed
- `td_project_info` no longer promises a project-wide resolution; TouchDesigner has none (see `specs/TOOL_CATALOG.md`).
- `bridge/build_spike.py` exposes `prepare_script()` so the build input can be tested without TouchDesigner.
- `pyproject.toml` declares `typing-extensions` directly, since the server imports it.

### Fixed (found by running against a live TouchDesigner)
- `td_snapshot_graph` on `/` silently omitted `/ui` and `/sys`. It and `td_get_errors` now leave both out of a `/` report on purpose and list them in a new `excluded` field. Before, the error report on `/` was filled with TouchDesigner's own warnings.
- `td_get_errors` returns one row per message; an operator's repeated warnings were merged into one.
- Parameter `min`/`max` are null unless TouchDesigner clamps to them.

### Known gaps
- The bridge has been seen to stop answering while TouchDesigner is in the background. The cause is not confirmed; see `research/PHASE1_LIVE_RESULTS.md`.
- A real error row and `status: "incomplete"` have not been seen live (unit-tested only).
