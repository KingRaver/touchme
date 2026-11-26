# Phase 4 Live Results

Type: research
Status: complete-with-open-items (see "Not tested")
Last verified: 2026-11-26 at `c1ee443`

How the performance, data, timeline and contact-sheet tools behaved on a live **2025.33230** (macOS, arm64, non-commercial license), through the real stdio server and a `--test-fixtures` bridge build with TouchDesigner in front. The code these results shaped is committed as `64a3ad9`; it was measured as an uncommitted working tree over `5e90090`. Probes were throwaway scripts kept outside this repository; they used the server's own tools, so no probe component was needed.

## First run: what the unit tests had missed

The first version passed 407 unit tests against fakes written from the offline help and assumptions. Live, it did the following. Each is fixed and has a test in `tests/integration/test_performance_data.py`.

| Problem | Evidence |
|---|---|
| 7 of 11 process-wide performance values were `null` | Perform CHOP toggle names differ from channel names (below) |
| `td_undo` reported a timeline change as undone and restored nothing | Frame stayed 300 after undo and redo |
| A narrowed range stayed in force after widening it | `rangestart`/`rangeend` read 80 and 20 with `start`/`end` 1 and 600; contact-sheet frames 10, 100, 200 showed 69, 41, 23 |
| Stop-and-seek landed one frame late | `play=False, frame=100` replied 100, then read 101, in 4 of 4 tries |
| A range excluding the paused playhead replied with the old frame | Replied 200 for range 300 to 400, then read 301 |
| `channels="c1 c2"` matched nothing | See `chans()` below |
| A DAT row offset past the end gave a negative `row_limit` | `-4599` |
| One NaN or infinite sample failed the whole CHOP read | "JSON cannot represent NaN or Infinity" |
| Replies had no overall size limit | Limits were per row, cell and channel only (from the limits, not measured) |
| `td_read_dat` on the bridge's script returned the bearer token | The token was in `text` |
| TouchDesigner's internal groups were listed | `__sopptgroup_pointclosure_` and similar |
| The contact sheet refused any TOP over 480x480 | A 1280x720 TOP was refused |
| Cells of different shapes were stretched | A 32x64 cell filled a 64x64 slot |

## Perform CHOP

| Toggle parameter | Channel |
|---|---|
| `fps`, `msec`, `cookrate`, `cookrealtime` | the same |
| `droppedframes` | `dropped_frames` |
| `gpumemused` / `totalgpumem` / `cpumemused` | `gpu_mem_used` / `total_gpu_mem` / `cpu_mem_used` |
| `activeops` / `deactivatedops` / `totalops` | `active_ops` / `deactivated_ops` / `total_ops` |

- Only `fps` is on by default. A new Perform CHOP gave all the values above on its first forced cook.
- Creating and destroying it left nothing behind and added no undo entry.
- `cpuCookTime` and `gpuCookTime` are the last measured cook, which can be long ago. On `/`, the bridge's own callbacks DAT ranked first (17.7 ms). `gpuCookTime` read above zero on cooking TOPs.

## Readers

- `CHOP.chans()` takes several patterns as separate arguments. `c[1-3]` matched `c1` and `c3` only; `c[123]` matched all three.
- Infinity came from multiplying 1e38 by 1e38, NaN from adding `inf` and `-inf`. Infinity multiplied by 0 read 0.0.
- `pointAttribs`, `primAttribs` and `vertexAttribs` can be iterated, and `Cd`, `N`, `uv` and a custom attribute appeared. `P` was not listed.
- Before the reply limits were added, 2000 samples of a 100,000-sample CHOP and a 500-row page of a 90,001-row table each took 0.84 s, including starting the server process.
- A Group SOP made no group until a criterion such as `usenumber` was on.
- Redacting the token covers its exact text, including a Select DAT copy. A network that transforms the text could still show it; a caller who can set expressions can reach it anyway (`decisions/0003-bridge-auth-bearer-over-loopback.md`).

## Timeline

- `op("/").time` is `/local/time`. `start`/`end` and `rangeStart`/`rangeEnd` are separate, and the playhead wraps into the second pair without an error (frame -5 in range 10 to 50 became 36).
- 20.5 became 21. A range ending before it starts was accepted.
- Setting these attributes from Python is not undone by TouchDesigner. A `ui.undo.addCallback` restored them, including from a later request.
- Setting `play = False` before `frame` lands on the frame.

## Contact sheet frames

Frames are set and cooked inside one callback.

| Network | Result |
|---|---|
| Expression on `me.time.frame`, a Timeline CHOP, and operators downstream of either | Exact for frames 10, 100, 200, 50 |
| Expression on `absTime.frame` | Identical cells |
| LFO CHOP, and a time-sliced Noise CHOP | Values differed from those read after stepping the timeline one request at a time |
| Feedback loop | Identical cells; each call advanced it one step. Plain captures of the paused loop did not advance it |
| Trail CHOP | Cells differed and the call wrote into its history; see below |

The timeline was restored after success and after a refusal part way through, paused or playing.

### Trail CHOP

Measured 2026-11-26 at `a4b218d`, same build and setup (the warning and annotation changes that followed are committed as `c1ee443`): a Timeline CHOP into Trail CHOPs of 8 and 600 samples, both capture modes (`timeslice`, `inputcooks`) at 8 samples and `timeslice` at 600.

| Check | Result |
|---|---|
| Paused: reading the trail, `td_capture_top`, a sheet without `frames` | History unchanged |
| Cells for frames 50, 100, 200 | The newest sample in each cell was that frame; older samples accumulated from cell to cell |
| Paused: history afterwards | Gained 50, 100, 200, then one more sample for the frame returned to |
| Playing, 600 samples | History read 509, 20, 40, 510: the requested frames sit inside the live recording |
| The same frame three times | One sample for the three, then the returned one |
| Refused part way through | Timeline restored; the cells already cooked stayed in the history |
| A sheet of a TOP not using the trail, paused | History unchanged |
| `td_undo` afterwards | The sheet had added no undo entry |

Stepping the timeline one request at a time also records one sample per step. Both capture modes gave the same results with this input, which cooks on every frame change.

Scaling with `numpyArray()` and OpenCV differed from a box-filtered `td_capture_top` image by a mean under 4 of 255 per channel for `rgba8fixed`, `rgba32float`, `mono8fixed`, `mono32float` and `rg16float`, as PNG and JPEG. A sheet of 24 scaled 1280x720 cells took 0.92 s, including starting the server process.

## Final run

All 73 tests in `tests/integration/` (36 new) passed in one run, about 320 s. After the two fixes to earlier tools below, all 78 passed in one run, about 348 s. With the two Trail CHOP tests and the corrected annotations, all 80 passed in one run, about 369 s.

## Found in earlier tools, then fixed

| Problem | Evidence | Fix |
|---|---|---|
| A NaN or infinite float parameter broke every reply that held it | `td_set_parameters` applied `float('nan')`, `float('inf')` or `-float('inf')`, directly or through a bind, then replied with an error, as did `td_get_parameters`, `td_get_operator`, and a later change that had it as `previous` | Sent as `"nan"`, `"inf"`, `"-inf"`; the change carries a `warning` |
| A name pattern holding a space matched nothing | `"tx ty"`, `"t? period"` and a blank pattern gave 0 parameters in `td_get_parameters` and `td_describe_operator_type` | Split and passed to `pars()` as separate arguments; blank means no filter |

TouchDesigner coerces the same expressions on other parameter types without an error, and the tools report what it stored: on a Constant TOP, `resolutionw` read 1 for NaN and 2147483647 for infinity, the `resmult` toggle read false for NaN, and the `outputresolution` menu kept `useinput`.

The new live tests failed (5 of 5) against the bridge from before the fix.

## Not tested

- Time-dependent media, such as a Movie File In TOP, and Cache operators in contact-sheet frames.
- A Trail CHOP with Grow Length on, or with an input that does not cook on every frame change.
- A large user project, timelines with their own Time COMP, and `POP` operators.
- Timeline changes with undo disabled (unit-tested for other changes only).
- Other builds and platforms.
