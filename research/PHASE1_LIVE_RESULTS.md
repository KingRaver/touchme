# Phase 1 Live Results

Type: research
Status: complete-with-open-items (bridge stops answering with TouchDesigner in the background; see "Not tested")
Last verified: 2026-11-26 at `369aabd`

The Phase 1 tools run against a live TouchDesigner **2025.33230** (macOS, arm64, non-commercial license) through the real stdio server (`python -m td_mcp.server`) and a `--test-fixtures` bridge build. The checks beyond `tests/integration/` were a throwaway script kept outside this repository.

## Run 1: bridge API 0.1.0 (code at `15d10c9`)

Project: an unsaved default project with the bridge at `/project1/td_mcp_bridge`. The 2 integration tests and 44 live checks passed. Further read-only probes of the same results found four problems the checks did not catch.

| Problem | Evidence |
|---|---|
| A snapshot of `/` silently omitted `/ui` and `/sys` | At depth 1 it returned the root plus `/local`, `/perform` and `/project1`, while `/children` listed 5 children. At depth 8: 73 operators, none under `/ui` or `/sys`, `truncated: false`. Asked for directly, `/ui` and `/sys` each hit the 1000-operator cap. |
| Repeated warnings from one operator were merged into one row | 32 rows held 88 messages. |
| `/errors` on `/` was filled with TouchDesigner's own warnings | All 32 rows were under `/ui` or `/sys`; `/project1` was clean. |
| Parameter `min`/`max` did not describe a range | `resolutionw` was 640 with `min` 1 and `max` 1. [Par_Class](https://docs.derivative.ca/Par_Class) says a value is clamped to `min` or `max` only when `clampMin` or `clampMax` is true. |

Warning stream format for repeated warnings, which [PHASE0_SPIKE_RESULTS.md](PHASE0_SPIKE_RESULTS.md) had not seen: the first line is `<owner>:Warning: <msg> (<path>)`, and each further one is `Warning: <msg> (<path>)` with no owner prefix. The trailing path differed from the owner in 4 of the 88 messages.

## Run 2: bridge API 0.2.0 (code committed as `369aabd`)

Project: the one TouchDesigner created when the `.tox` was opened with `open -a TouchDesigner <path to .tox>`. The bridge was at `/td_mcp_bridge` and there was no `/project1`. The 2 integration tests passed, and with TouchDesigner brought to the front, 52 of 52 live checks passed.

| Fix | Evidence |
|---|---|
| Snapshot of `/` | `excluded: ["/ui", "/sys"]` at depths 1 and 8, with nothing under either (61 operators at depth 8). `/ui` asked for directly still returned its operators with `excluded: []`. |
| One row per warning | 87 rows from `/ui` and `/sys`, none containing a second `Warning:` line. |
| `/errors` on `/` | One warning, on `/perform` ("Window Operator is missing or points to a blank Base component"), with `excluded: ["/ui", "/sys"]`. |
| Clamp limits | 299 numeric values across the `/` snapshot, 90 with a limit, none outside it. `resolutionw`: 640, `min` 1, `max` null. Web Server DAT `port`: 9991, `min` 1, `max` 65535. |

## Assumptions confirmed

- **Timeline.** `op("/").time` as reported by `td_project_info` matched `/local/time` on rate, start, end and play state, and the frame advanced during playback (117 to 179 over about 1 s in run 1). Checked on default projects only; a component with its own Time COMP was not tried.
- **Request headers.** `Host` and `Origin` are readable in the callback: foreign values got 403, valid requests 200, and no tested reply carried a CORS header, including a foreign-origin `OPTIONS` request.
- **Operator families.** `CHOP`, `COMP`, `DAT`, `MAT`, `SOP` and `TOP` appeared with those spellings.

## The bridge stops answering in the background

- In run 2 the bridge stopped replying partway through the checks. Every request then timed out for several minutes. The process was sleeping at about 1.5% CPU, the port was still listening, and Google Chrome was the front application when checked.
- Bringing TouchDesigner to the front (`open -a TouchDesigner`) got a reply to the next request in 0.49 s, then 0.02 s. The reruns started that way passed.
- An earlier instance the same day also held port 9991 without replying. Which application was in front was not checked, and TouchDesigner was closed before it was diagnosed.
- **Cause not confirmed.** macOS throttling a background application is a likely explanation. It was not reproduced under control.
- This matters in normal use, where the MCP client is in front. The client's timeout message ("It may be busy cooking") pointed the wrong way here.

## Not tested

- A real row on the errors stream (both runs had warnings only), and `status: "incomplete"`, live.
- A large user project, `POP` or other families, and the over-size capture limits.
- Why the bridge stops answering in the background, and whether any TouchDesigner or macOS setting prevents it.
