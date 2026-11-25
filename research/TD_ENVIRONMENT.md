# TouchDesigner Environment (verified)

Type: research
Status: complete
Last verified: 2026-11-25 at `no git repo yet`

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

- Whether Web Server DAT callbacks run on TouchDesigner's main thread. Tracked in `plans/TD_MCP_BUILD.md` Phase 0.
- License tier (non-commercial, commercial, pro). Needed to know capture resolution limits.

## Notes

- The MCP server runs in its own `uv` environment and never imports TouchDesigner's embedded Python, so the two Python versions do not have to match.
- Official reference documentation: https://docs.derivative.ca/ (operated by Derivative, the makers of TouchDesigner).
