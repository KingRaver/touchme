# Phase 0 Spike Results

Type: research
Status: complete-with-open-items (TLS and Verify Client instability not explained; concurrency, other platforms and licensed builds untested; see "Not tested")
Last verified: 2026-11-25 at `f980863`

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

## Round 2 results

Same build (2025.33230, macOS arm64, non-commercial). Measured on a **second** TouchDesigner instance launched for the purpose, so the operator's own session was not touched. Probes were again throwaway and kept outside the repository: fixed routes only, no exec endpoint, mutations confined to the probe's own `sandbox`. The round trip used the repository's own bridge source (`bridge/src/callbacks.py`) and server (`src/td_mcp/`).

### Round trip: stdio MCP server to live TouchDesigner to an image

| Check | Result |
|---|---|
| Real stdio server process (`python -m td_mcp.server`) calls `td_capture_top` against the live bridge | **Works.** `tests/integration/test_roundtrip.py` passes: PNG, 640x360, centre pixel within 3 levels of the commanded colour (0.9, 0.2, 0.1). |
| Can Claude see the result | **Yes.** The returned PNG (1,830 bytes) was saved and opened as an image: flat orange, as commanded. |
| Glob and missing paths | Both refused with "no operator at exactly ...". The exact-path rule from round 1 holds end to end. |

Observed along the way, not explained: with the demo-TOP creation inside the `GET /ping` handler, `/ping` never answered while 401 and 404 responses on the same server still did. Moving the creation to its own route and wrapping the whole `onHTTPRequest` in a try/except made it go away. The original arrangement was not retested, so the cause is unknown. The wrapper stays because an exception escaping `onHTTPRequest` leaves the client with no reply at all.

### Web Server DAT authentication options

Docs checked against https://docs.derivative.ca/Web_Server_DAT (edited 2026-06-04) and the live DAT.

| Option | Finding |
|---|---|
| **Basic** (`authenticateBasic(token, userPasswords) -> bool`) | **Works as documented.** With a route that checks it: no credentials, wrong password, wrong user and empty password all returned 401; the right pair returned 200. The method is code-only (there is no parameter), takes a plaintext `userPasswords` dict that must live in the callbacks DAT, and the 401 and `WWW-Authenticate` header are the callback's job. Nothing is documented about how the comparison is done. It adds nothing over a bearer token in the same DAT. |
| **Secure (TLS)** (`secure`, `certificate`, `privatekey`, `minprotocol`) | **Worked once, then unreliable.** With a single self-signed certificate: HTTPS answered with the pinned certificate, an unpinned client refused it, plain HTTP to the same port was refused. With CA-signed server certificates (a chain file, then a single leaf), 3 of 3 attempts ended with the instance not listening or the process gone. No crash report or log was written, and the cause was not isolated. Enabling Secure also drops the connection that carried the request. |
| **Verify Client** (`verifyclient`, mutual TLS) | **Not validated.** The live DAT has the parameter, but the docs name no CA or trust-store parameter, so what a client certificate is checked against is undocumented. The one attempt to enable it took the instance down (same caveat: cause not isolated). |

Live parameter names on the DAT: `active callbacks certificate customext extension language localaddress minprotocol pageindex password port privatekey restart secure verifyclient wordwrap`. `localaddress` is a menu offering `127.0.0.1` and the machine's LAN address.

### Prior-art behaviors, re-verified on 2025.33230

| Behavior the prior art recorded | Result |
|---|---|
| Name collision renames silently | **Holds** (round 1, `/names`: `dup` became `dup1`). |
| Error and warning stream formats | **Holds** (round 1, `/errors`). Round 2 added `addError`: `errors()` returned `  Error: boom-caught` (two spaces), and the parent's `errors(recurse=True)` prefixed the operator path. |
| Script OP exceptions reach no stream | **Holds.** A Script CHOP whose `onCook` raises left `errors()`, `warnings()`, `scriptErrors()` and the parent's recursive `errors()` all empty. The traceback did appear on `sys.stderr` (captured by swapping `sys.stderr` around the cook). Catching and calling `scriptOp.addError(msg)` does reach `errors()`. An Error DAT check was inconclusive: no `op` or `ops` parameter was found to point it at the sandbox, and it listed nothing even for the `addError` case. |
| `op(<dict>)` hangs TouchDesigner | **Did not reproduce.** `op({"a": 1})` returned `None` in 5 ms, and the bridge answered afterwards. Other payloads and call sites were not tried, so the guard stays: the bridge type-checks `path` before calling `op()`. |

### Capture options

Source: a 1280x720 Noise TOP (non-flat, so compression is realistic), target 512x288, median of 7 calls.

| Option | Median time | Bytes | Temporary operator |
|---|---|---|---|
| A. `saveByteArray('.jpg')` full size | 16 ms | 217,593 | no |
| A. `saveByteArray('.png')` full size | 138 ms | 293,174 | no |
| B. Temporary Resolution TOP, then `saveByteArray('.jpg')` (the prior art's way) | 15 ms | 51,206 | **yes** (created and destroyed per call) |
| B. Temporary Resolution TOP, then `saveByteArray('.png')` | 35 ms | 72,708 | **yes** |
| C. `numpyArray()`, flip, `cv2.resize(INTER_AREA)`, `cv2.imencode('.jpg')` | 16 ms | 29,452 | **no** |
| C. same, `.png` | 18 ms | 138,146 | **no** |

- **C needs no temporary operator and costs the same as B.** The sandbox's child count stayed at 2 throughout. The decoded B and C JPEGs differ by 0.35 of 255 on average.
- **`numpyArray()` is bottom-up and must be flipped.** Unflipped, it differs from `saveByteArray` by 72.3 levels on average. Flipped (`arr[::-1]`), the full-size result is pixel-identical (difference 0.0). It returns float32 RGBA, shape `(h, w, 4)`.
- **PNG of noise is slow at full size (138 ms) but fine after downscaling (18 ms).** Round 1's 12 ms PNG was a flat colour.
- **`saveByteArray(filetype, quality=1.0, metadata=[])`** takes a `quality` from 0 to 1. The JPEG sizes above use the default of 1.0. Quality was not swept.
- **TouchDesigner's embedded Python has `cv2` 4.11.0 and `numpy` 2.1.2, and no `PIL`.** The bridge can use C without adding a dependency; Pillow stays on the server side.
- `numpyArray(delayed=True)` can return `None`; the default call does not.

## Not tested

- **Concurrency and long handlers.** All handlers ran on the main thread, so a slow handler presumably stalls TouchDesigner's UI and cooking. Nothing here measures that or how the DAT queues simultaneous requests.
- **Why TLS with CA-signed certificates, and Verify Client, took the instance down or stopped it listening.** Observed, not explained.
- **Other `op()` payloads** that might still hang TouchDesigner (only `{"a": 1}` was tried).
- **An Error DAT** watching a Script OP.
- **Other platforms**, other builds, and a licensed (commercial or Pro) instance.

## Consequences

- `specs/ARCHITECTURE.md`'s threading assumption is verified for this build. The queue-and-drain fallback is not needed, but long operations still block TouchDesigner and need time budgets.
- The prior art's recorded behaviors (name-collision rename, warning stream format, JPEG capture) hold on this build. Their `td.op()` glob caveat holds and is, if anything, worse than they documented.
- Capture resolution on this license tops out at 1280x1280, so default capture sizes should not exceed that and tools should report when a request is capped.
- Both JPEG and PNG are cheap at small sizes. The choice can be made on image content; see `plans/TD_MCP_BUILD.md`.
- The stdio-to-TouchDesigner round trip works, so decision 0002's revisit condition (cannot run handlers on the main thread, or unacceptable capture latency) is not met.
- Downscale in the bridge with `numpyArray()` and `cv2` rather than a temporary operator. Flip the array first.
- Do not build on TLS or Verify Client. See `decisions/0003-bridge-auth-bearer-over-loopback.md`.
- Script OP exceptions are invisible to `td_get_errors`. Tools must not report an all-clear on that basis alone.
