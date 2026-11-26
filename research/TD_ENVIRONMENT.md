# TouchDesigner Environment (verified)

Type: research
Status: complete-with-open-items (Phase 1 TD API assumptions below are doc-sourced, not re-verified live)
Last verified: 2026-11-25 at `3df1a28` plus uncommitted Phase 1 changes (re-stamp with the Phase 1 commit)

Facts about the machine this server is built on. Re-run the commands if the TouchDesigner install changes.

| Item | Value | Command / source |
|---|---|---|
| TouchDesigner build | 2025.33230 (updated by the operator on 2026-11-25; previously 2025.32820) | `defaults read /Applications/TouchDesigner.app/Contents/Info.plist CFBundleShortVersionString` |
| Embedded Python | 3.11 | `ls /Applications/TouchDesigner.app/Contents/Frameworks/Python.framework/Versions` |
| Architecture | arm64 | `uname -m` |
| macOS | 26.7.3 | `sw_vers -productVersion` |
| Host Python | 3.11.2 | `python3 --version` |
| `uv` | `/opt/homebrew/bin/uv` | `which uv` |
| TD user data | `~/Library/Application Support/Derivative/TouchDesigner099` (Palette, TDLogs, ShaderCache, FontCache) | `ls` |

## Not yet verified

- Nothing outstanding from Phase 0 environment checks. Verified 2026-11-25 (see `PHASE0_SPIKE_RESULTS.md`): callbacks run on the main thread; the license is non-commercial with a 1280x1280 resolution cap; embedded Python is 3.11.15.
- **Phase 1, added 2026-11-25, not re-verified live (no TouchDesigner attached to that session):**
  - `td_project_info`'s timeline fields read `op("/").time` (the root's `OP.time`, "Time Component that defines the operator's time reference", per `OP_Class`) and its `.frame`/`.rate`/`.start`/`.end`/`.play` (per `TimeCOMP_Class`). The docs do not explicitly say `root.time` is *the* project timeline — that is an inference from the two pages, not a quoted fact. Confirm against a real project before trusting `td_project_info`'s `timeline` field.
  - The bridge's Host/Origin check (`bridge/src/callbacks.py`, `_handle`) assumes the Web Server DAT's `request` dict exposes `Host` and `Origin` as flat, case-insensitively-keyed entries, the same shape already confirmed for `Authorization` in Phase 0. Not independently re-confirmed for these two header names specifically.
  - `OP.family`'s exact string values (`"TOP"`, `"CHOP"`, `"DAT"`, `"SOP"`, `"COMP"`, `"MAT"`, ...) are not enumerated on `OP_Class`; `"TOP"` and `"CHOP"` are confirmed live (Phase 0), the rest are inferred from the docs' prose.

## Notes

- The MCP server runs in its own `uv` environment and never imports TouchDesigner's embedded Python, so the two Python versions do not have to match.
- Official reference documentation: https://docs.derivative.ca/ (operated by Derivative, the makers of TouchDesigner).
