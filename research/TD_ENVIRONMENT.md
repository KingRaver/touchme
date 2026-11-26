# TouchDesigner Environment (verified)

Type: research
Status: complete
Last verified: 2026-11-26 at `369aabd`

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
- Nothing outstanding from the Phase 1 TouchDesigner API assumptions. Verified live 2026-11-26 (see `PHASE1_LIVE_RESULTS.md`, which also states what was not covered): `op("/").time` matches `/local/time` on a default project; `Host` and `Origin` are readable in the Web Server DAT callback; `OP.family` values include `CHOP`, `COMP`, `DAT`, `MAT`, `SOP` and `TOP`.

## Notes

- The MCP server runs in its own `uv` environment and never imports TouchDesigner's embedded Python, so the two Python versions do not have to match.
- Official reference documentation: https://docs.derivative.ca/ (operated by Derivative, the makers of TouchDesigner).
