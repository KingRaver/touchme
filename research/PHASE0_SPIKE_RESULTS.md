# Phase 0 Spike Results

Type: research
Status: complete-with-open-items (round trip through an MCP server, TLS/auth options, Script OP and `op(<dict>)` behavior, and no-temp-operator downscaling are not done; see `plans/TD_MCP_BUILD.md`)
Last verified: 2026-11-25 at `c6586a9`

Measured on a live TouchDesigner **2025.33230** (macOS 26.7.3, arm64, embedded Python **3.11.15**), non-commercial license, using a throwaway probe component. The probe was a Web Server DAT with Local Address `127.0.0.1`, port 9990, a bearer token on every request, and mutations confined to its own `sandbox` container. It had no endpoint that executes arbitrary code. The probe and its token were kept outside this repository and the sandbox was destroyed afterward.

## Results

| Question | Result | Evidence |
|---|---|---|
| Which thread runs `onHTTPRequest`? | **`MainThread`.** Every probe request ran with `threading.current_thread() is threading.main_thread()` true, across 9 routes. Handlers can create, destroy and cook operators directly, with no queue or hand-off. | `/ping` and every route reported `is_main_thread: true` |
| Does the DAT bind to loopback only? | **Yes**, when Local Address is `127.0.0.1`. `lsof` showed `127.0.0.1:9990 (LISTEN)` over IPv4 only, and a request to the machine's LAN address (192.168.1.106) could not connect. | `lsof -nP -iTCP:9990`; `curl` to the LAN IP |
| Are request headers readable in the callback? | **Yes.** The request dict carries `Authorization`, `Host`, `User-Agent`, `Accept`, `clientAddress`, `serverAddress`, `method`, `uri` (a string), `pars`, `data`. Wrong or missing tokens were rejected with 401. | `/request-shape`, auth probes |
| Can we read a TOP as image bytes? | **Yes.** `saveByteArray('.jpg')` and `('.png')` both work. A flat 640x360 TOP: JPEG 4,312 bytes in about 4 ms; PNG 1,830 bytes in about 12 ms. Both begin with the correct magic bytes. | `/capture` |
| Resolution cap on this license | **1280x1280.** Requests for 1920, 4096 and 8192 square TOPs all cooked at 1280x1280. | `/resolution` |
| What does a name collision do? | Silently renames: the second `dup` became `dup1`. | `/names` |
| Do globs reach `td.op()`? | **Yes, and the match is arbitrary.** `td.op('<parent>/glob_*')` returned `glob_beta`, though `glob_alpha` was created first, so it is not "first by name" or "first created". A glob path returns a different operator than the one named. Exact-path checks are required on every mutating call. | `/glob` |
| Error and warning stream format | `errors()` was empty; `warnings(recurse=True)` returned `/<path>:Warning: Failed to open file. (/<path>)` for a missing movie file. A healthy operator returned `""` on both. Matches the prior art's recorded format. | `/errors` |
| Cook-time attributes | `cookTime`, `cpuCookTime`, `gpuCookTime`, `cookFrame` exist. `cookCount` does not. `scriptErrors`, `addError`, `numpyArray` exist. `app.licenseType` does not exist (no license-named attribute on `app`). | `/attrs`, `/ping` |
| Handler latency | 0.01 to 31 ms per request including cooks, light load. | `handler_ms` in each response |

## Not tested

- **Concurrency and long handlers.** All handlers ran on the main thread, so a slow handler presumably stalls TouchDesigner's UI and cooking. Nothing here measures that or how the DAT queues simultaneous requests.
- **Script OP callback exceptions** reaching no error stream, and the `op(<dict>)` hang. Both were left untested on purpose: the second is reported to freeze TouchDesigner.
- **Basic auth (`authenticateBasic`), TLS and client certificates** on the Web Server DAT.
- **Downscaling a capture without a temporary operator.**
- **Other platforms**, other builds, and a licensed (commercial or Pro) instance.
- The **MCP server leg**: nothing in this spike used the Python MCP server yet.

## Consequences

- `specs/ARCHITECTURE.md`'s threading assumption is verified for this build. The queue-and-drain fallback is not needed, but long operations still block TouchDesigner and need time budgets.
- The prior art's recorded behaviors (name-collision rename, warning stream format, JPEG capture) hold on this build. Their `td.op()` glob caveat holds and is, if anything, worse than they documented.
- Capture resolution on this license tops out at 1280x1280, so default capture sizes should not exceed that and tools should report when a request is capped.
- Both JPEG and PNG are cheap at small sizes. The choice can be made on image content; see `plans/TD_MCP_BUILD.md`.
