# Phase 2 Undo and Construction Results

Type: research
Status: complete-with-open-items (see "Not tested")
Last verified: 2026-11-26 at `a569928`

How the TouchDesigner scripting APIs behind the construction tools behaved on a live **2025.33230** (macOS, arm64, non-commercial license). Measured with a throwaway probe component (fixed routes only, no exec endpoint, kept outside this repository) in a second TouchDesigner instance, so the operator's own session was not touched. API names were first checked in the offline help shipped with that build (`Undo_Class`, `Connector_Class`, `Par_Class`, `OP_Class`, `COMP_Class`, `Td_Module`). The construction code these results shaped is committed as `a569928`.

## Callbacks and requests

| Behavior | Observed |
|---|---|
| Module state | **The Web Server DAT callbacks module is re-executed on every request.** A module-level list had length 1 on six consecutive requests, with a new identity each time. A value written with `parent().store()` counted 1 to 6. |
| POST body | `request["method"]` is `"POST"` and `request["data"]` is `bytes`. `request["pars"]` still holds the query string. |

## Undo

| Behavior | Observed |
|---|---|
| Inside a callback | `startBlock`, `endBlock`, `undo` and `redo` work, including across requests and `undo` straight after `endBlock`. |
| Enabled flags | Outside a block, `ui.undo.state` was `False` and `ui.undo.globalState` `True`. |
| Stack order | `undoStack[0]` and `redoStack[0]` are the next entry to run. |
| Empty block | Pushes nothing. |
| Unblocked script change | Pushes nothing. |
| **A block that only sets `expr` or `bindExpr`** | The entry is named `Change Expression` or `Change Bind Expression`, not the block's name. |
| Marker callback | Calling `ui.undo.addCallback(noop, info)` first gave one entry under the block's name for an expression-only change, a bind-only change, a block that changed nothing, and an Annotate COMP creation. |
| **Annotate COMP creation** | Ends the surrounding block itself: the later `endBlock()` raised `Cannot end non existent undo operation`. With the marker, one undo removed it. |
| Operator sweep | All 653 types in `families` were each created in a marked block and undone. Every one left a single entry under the block's name and no leftover operators; only `annotateCOMP` raised on `endBlock`. |
| Rolled-back block | After `undo()` of a failed block, that block sits on the redo stack. A new block clears the redo stack. |
| Undo of a bind | Mode and value are restored; the bind text stays, unused. `isDefault` then read `False` for the other parameters of the same colour group too. |
| Undo of a destroy | Parameters, wires and docked operators come back. The operator's `id` is new. |

## Creating and wiring

| Behavior | Observed |
|---|---|
| `families` | Dict of family name (`COMP`, `TOP`, `CHOP`, `SOP`, `POP`, `MAT`, `DAT`) to a list of type objects; `__name__` is the type string, such as `noiseTOP`. |
| Bad type or name | `create` raises `tdError` (`Unknown operator type`, `Illegal node name specified`). A leading digit is accepted; an empty name gets the default. |
| Docked operators | Created with their host (GLSL TOP, Web Server DAT, Script and Timer CHOPs), undone with it, destroyed with it, and **not moved** when the host's `nodeX` changes. |
| Default position | New operators are all placed at (0, 0). |
| Refused wires | Mismatched families and wiring an operator to itself raise `tdError`. A loop between two operators is accepted. |
| **Wiring across networks** | Raises nothing, makes no connection, and **removes the wire that was on that input**. |
| Multi-input operators | A Composite TOP with n inputs has n+1 input connectors. Disconnecting one moves later inputs up. An index past the end raises `IndexError`. |
| **Destroying a wired operator** | Its source is wired straight into the operators it fed (single-input chain tested). |

## Parameters

| Behavior | Observed |
|---|---|
| **Silent coercion** | An unknown menu string or out-of-range index was accepted and stored as some menu value; `"yes"` set a toggle to true; `"0.7"` set a float; `2.7` set an int to 2; an out-of-range number was clamped. Non-numeric strings and `None` raised on numbers. |
| **Value on a bound parameter** | Writes through to the bind master and leaves the mode `BIND`. Setting `mode = ParMode.CONSTANT` first changed only that parameter. |
| Value on an expression parameter | Switches it to constant mode. |
| Dead expression | No error on assignment; `eval()` raises `tdError`. `errors()` on the operator stayed empty. |
| Dead bind | No error on assignment; `eval()` returns a value, and `bindMaster` is `None`. The operator's `errors()` text stayed after a later good bind, so it cannot be used to judge the new bind. |
| Exported parameter | Mode reads `EXPORT` and `exportOP` names the exporting CHOP. |
| Passwords | `par.password` is `True` on the Web Server DAT's built-in `password`. |
| Menu with free text | `StrMenu` parameters report both `isMenu` and `isString`. |

## The construction tools, live

All 21 tests in `tests/integration/` passed in one run (128 s) through the real stdio server against a `--test-fixtures` build on port 9996, with TouchDesigner in front. A hand edit was stood in for by a test-only route that adds a differently named undo entry.

## The bridge stops answering in the background (seen again)

- During a run, requests timed out after 18 tests had finished in three minutes. The operator reported that VS Code or Chrome was in front at the time, to read the session or GitHub. This supports the open item from [PHASE1_LIVE_RESULTS.md](PHASE1_LIVE_RESULTS.md). The cause is still not confirmed.
- Passing `-NSAppSleepDisabled YES` as a launch argument is not possible: TouchDesigner exits with `unknown argument`.
- No `NSAppSleepDisabled` preference was set, globally or for `ca.derivative.TouchDesigner`.

## Not tested

- Undo disabled in TouchDesigner, an unwritable audit log, and a lost reply to a change (unit tests only).
- A real edit made by hand in the editor between server changes.
- How readable `td_layout`'s result looks in the editor (positions were checked as numbers), and layout of a large project.
- Wiring through COMP In and Out operators, and wiring in the `POP` family.
- Other builds and platforms.
