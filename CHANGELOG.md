# Changelog

Newest first. Progress and evidence for planned work live in [plans/TD_MCP_BUILD.md](plans/TD_MCP_BUILD.md); this file records what changed and why.

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
