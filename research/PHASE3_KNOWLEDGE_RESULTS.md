# Phase 3 Knowledge Results

Type: research
Status: complete-with-open-items (see "Not tested")
Last verified: 2026-11-26 at `e257c98` (measurements taken with an uncommitted working tree on top of it)

What the knowledge tools rest on: how TouchDesigner **2025.33230** (macOS, arm64, non-commercial license) behaves when an operator is created only to read its parameters, and what the documentation shipped with that build contains. The operator measurements come from a throwaway probe component (fixed routes only, no exec endpoint, kept outside this repository) in its own TouchDesigner instance.

## Reading a type's parameters

- A type object such as `families["TOP"][i]` exposes no parameter list: its attributes are those of the operator class. Reading a type needs an instance.
- On an instance, `label`, `style`, `page`, `default`, `defaultMode`, `defaultExpr`, `menuNames`, `menuLabels`, `help`, `normMin`, `normMax`, `readOnly`, `isCustom` and `sequence` are readable on built-in parameters.
- `min` and `max` read 0 and 1 on unclamped parameters, as in [PHASE1_LIVE_RESULTS.md](PHASE1_LIVE_RESULTS.md).
- Number parameters can carry preset `menuNames` (`resolutionw`), and toggles report `off`/`on`.
- `pageindex` has no page. Filler help reads `Help Not Available.`
- A new operator's value can differ from `default`: a Web Server DAT's `callbacks` defaults to empty and starts as the name of its docked DAT.

## Creating every type

All 653 types in `families` were each created inside a new COMP with `allowCooking = False`, read, and destroyed with that COMP, outside any undo block.

| Check | Result |
|---|---|
| Create or destroy errors | None |
| Operators left behind | None |
| Undo and redo stacks | Unchanged on every type. A separate single-type check started with one entry on each stack, and both survived. |
| Time to create | Under 15 ms for all but a few; slowest `gltfinCOMP` at 124 ms |
| Parameters per type | Median 23, most 240 (`phongMAT`). Only `annotateCOMP` had custom parameters. |
| Reading a value | `eval()` raised on some parameters of 10 types, such as `pointSOP` `tx`; the other attributes read normally |

### Side effects, with cooking off

**Turning cooking off does not make creation free of side effects.**

| Types | Observed |
|---|---|
| Batch holding `videodeviceinTOP`, `videodeviceoutTOP` and `videodevicesDAT` | macOS asked for camera permission for TouchDesigner. After it was allowed, the process connected to the camera service, listed the cameras and set a framing property. No stream start was logged, and the service reported no pending stream starts when the process exited. Attributed to these types by timing; not isolated to one. |
| `laserdeviceCHOP` | Opened a UDP socket |
| `orbbecTOP` | Opened a UDP socket on all interfaces, still open after the operator was destroyed |
| First batch, which includes the audio device types | A microphone permission status check, with no prompt |

Network operators such as `webserverDAT` (inactive by default) opened no socket. Connections to the local license service appeared throughout and are not tied to a type.

**Consequence:** the bridge never creates a type that talks to hardware (`DEVICE_TYPE_PREFIXES` in `bridge/src/callbacks.py`). The list is wider than the observed cases, because a permission already granted, or a device that is absent, hides a side effect.

## Parameter help file

`Contents/Resources/tfs/Config/TDParameterHelp.json` (662 types) gives each type a summary and each parameter a label, summary and type. It has no defaults, pages or menu entries; it names a vector once (`t` for `tx`, `ty`, `tz`); and it has no entry for 12 of the 653 live types, including `nuitrackCHOP`. It is a fallback, not a substitute for reading an instance.

## Offline documentation

`Contents/Resources/tfs/Samples/Learn/OfflineHelp/https.docs.derivative.ca` holds a copy of the wiki: 2,087 `.htm` files, 184 MB with images.

- Each page embeds its MediaWiki settings (`wgPageName`, `wgTitle`, `wgNamespaceNumber`, `wgCategories`, `wgArticleId`). The `<title>` element is unreliable: 126 pages are titled `COMP - TouchDesigner Documentation`, many of them Palette pages.
- The body runs from `id="mw-content-text"` to `class="printfooter"`. It also holds glossary pop-ups (`mw-lingo-tooltip`) and lists of every operator in a family (`catList`).
- Operator pages are in the categories `TOPs`, `CHOPs` and so on. Parameter names appear beside their labels, often as a group name (`resolution`).
- Class pages repeat inherited members under a level-1 heading such as `TOP Class`.
- Some articles are saved under two file names with one article id.

Indexed with the standard library's SQLite FTS5 (SQLite 3.39.4): 658 operator, 660 class and 664 article pages; the build took about 8.5 s and 19 MB, a cached search about 35 ms. `tests/integration/test_knowledge.py -k search_docs` passed (9 tests) through the real stdio server against this folder.

## The tool, live

All 37 tests in `tests/integration/` passed in one run (about 200 s) through the real stdio server against a `--test-fixtures` build of bridge API `0.4.0`, with TouchDesigner in front.

| Check | Result |
|---|---|
| Names | The parameter names described for `constantCHOP` matched those of a created one |
| Project | Nothing left in the bridge component or at the root; undo and redo still worked across a describe |
| Help file | Found through `app.configFolder`; `videodeviceinTOP` was described from it, 54 parameters |
| Camera | The system log showed no camera permission request and no camera service lines for the process over the whole run |
| Sockets | After the run: the bridge's loopback listener and the outbound connections TouchDesigner also made in the probe instance; no UDP socket |
| Inputs (`min_inputs`, `max_inputs`) | `noiseTOP` 0 and 2, `constantTOP` 0 and 1, `glslTOP` 0 and 3, `compositeTOP` 0 and 9999 |
| Size and time | 8,000 to 14,000 characters of JSON per call at the default page size; 15 to 39 ms |

The one permission check logged was a status check for input monitoring one second after launch, with no prompt; the probe instance logged the same service at its launch.

## Not tested

- Whether any type outside `DEVICE_TYPE_PREFIXES` asks for a permission or opens a device on a machine with that hardware attached.
- The documentation folder's location on Windows, and other builds.
