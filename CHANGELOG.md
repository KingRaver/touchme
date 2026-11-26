# Changelog

Newest first. Progress and evidence for planned work live in [plans/TD_MCP_BUILD.md](plans/TD_MCP_BUILD.md); this file records what changed and why.

## 2026-11-26: Phase 4, performance and data

### Added
- `td_get_performance`: frame rate, frame time, dropped frames, memory and operator counts, and the operators with the highest last CPU cook time.
- `td_read_chop`, `td_read_dat`, `td_read_sop_info`: channel samples, table rows or text, and geometry counts, bounds, groups and attribute names, each with reply limits.
- `td_timeline`: play, pause, seek and set the range of the root timeline. `td_undo` reverses it.
- `td_capture_contact_sheet`: several TOPs, timeline frames, or both, tiled into one image. Large TOPs are scaled down.
- `research/PHASE4_LIVE_RESULTS.md`: how the Perform CHOP, the Time COMP and frame sampling behave on the live build.

### Changed
- The bridge `API_VERSION` and the server's minimum are now `0.5.0`: rebuild the bridge and load the new `.tox`.
- Pillow is now a runtime dependency, because the server tiles contact sheets: run `uv sync`.
- `td_timeline` and `td_capture_contact_sheet` are annotated destructive: the first overwrites the playhead and range, the second can change stateful networks.

### Fixed
- A parameter value that is NaN or infinite no longer breaks `td_set_parameters`, `td_get_parameters` or `td_get_operator`. It reads as `"nan"`, `"inf"` or `"-inf"`, and an expression or bind that evaluates to one is applied with a `warning`.
- `td_get_parameters` and `td_describe_operator_type` accept several name patterns separated by spaces; before, they matched nothing.

### Security
- DAT reads replace the bridge token with `<redacted>`, since the bridge's own script holds it. A network that transforms the text could still show it.

### Known gaps
- Contact-sheet frames follow what is computed from the timeline frame. `absTime`, LFO and time-sliced Noise CHOPs do not.
- `td_capture_contact_sheet` with `frames` changes stateful networks it cooks: a feedback loop advances a step and a Trail CHOP records the frames. `td_undo` does not reverse this, and it is not in the audit log.
- Not checked live: time-dependent media or Cache operators in contact-sheet frames, a Trail CHOP with Grow Length on, a large user project, a timeline with its own Time COMP, and `POP` operators.

## 2026-11-26: Phase 3, knowledge

### Added
- `td_describe_operator_type`: an operator type's parameters (names, defaults, menus, limits, help) read from the running build, from a temporary operator inside the bridge that is removed at once.
- `td_search_docs`: search of the offline documentation shipped with the installed TouchDesigner, indexed on first use in `~/.td-mcp/docs-index.sqlite`. No bridge or network needed.
- `research/PHASE3_KNOWLEDGE_RESULTS.md`: what creating each operator type does, and what the offline help holds.

### Changed
- The bridge `API_VERSION` and the server's minimum are now `0.4.0`: rebuild the bridge and load the new `.tox`.

### Security
- Hardware operator types are never created to read their parameters. In a probe, creating all types (cooking off) raised a macOS camera permission request and opened UDP sockets. These types are described from the build's help file instead, with less detail.

### Known gaps
- The live run described only a few ordinary types and one hardware type.
- The hardware list is by type name. A type it misses would be created like any other.

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
