# Tool Catalog

Type: spec
Status: open
Last verified: 2026-11-26 at `64a3ad9`

The MCP tools the server exposes. All names use a `td_` prefix. Read-only tools carry `readOnlyHint`. Tools that delete or overwrite carry `destructiveHint`. Structure and constraints are in `specs/ARCHITECTURE.md`. Build order is in `plans/TD_MCP_BUILD.md`.

## Perception

| Tool | Purpose |
|---|---|
| `td_capture_top` | Render a TOP to PNG/JPEG at a chosen size, returned as an MCP image. The core feedback tool. |
| `td_capture_contact_sheet` | Several TOPs or time samples in one image, to compare variations cheaply. |
| `td_get_errors` | Errors and warnings across a subtree, one row per message with its operator path. |
| `td_get_performance` | FPS, cook time per operator (top N), GPU/CPU memory, dropped frames. |
| `td_snapshot_graph` | Compact text/JSON description of a network: ops, types, connections, flags. |

On the project root (`/`), `td_get_errors` and `td_snapshot_graph` leave out TouchDesigner's own
`/ui` and `/sys` networks and list them in `excluded`. Either path, asked for directly, is
reported in full (subject to the snapshot's limits), with `excluded` empty.

`td_get_performance` (evidence for this and the next three notes:
`research/PHASE4_LIVE_RESULTS.md`):

- Process-wide numbers (frame rate, frame time, dropped frames, memory, operator counts) come
  from a Perform CHOP the bridge creates in its own component, cooks, reads and destroys. It
  leaves nothing behind and no undo entry, so it is not written to the audit log. A value that
  could not be read is null and its channel is listed in `unread_channels`. Temperature and
  battery channels are not read.
- `top_cpu_cost` ranks the operators under `path` (default `/`, with the `/ui` and `/sys`
  exclusion above) by CPU cook time. The times are each operator's last measured cook, which
  may be long ago: see `cooked_this_frame`. The bridge's own operators are included. `top_n`
  defaults to 20 (at most 200); `max_depth` and the operator cap match `td_snapshot_graph`.

`td_capture_contact_sheet`:

- 1 to 16 TOP `paths`. With `frames` (at most 12), every path is captured at every frame, at
  most 48 cells, left to right then top to bottom.
- Frames are whole numbers inside the timeline's playback range; others are refused. Each is
  set on the root timeline and the target force-cooked, all within one request, and the
  timeline is put back, also after a failure.
- Cells follow what is computed from the timeline frame. `absTime`, LFO CHOPs and time-sliced
  Noise CHOPs do not.
- **With `frames`, the call changes stateful networks it cooks**, even if it is then refused,
  and `td_undo` does not reverse it. A feedback loop gives identical cells and advances one
  step. A Trail CHOP records the requested frames into its history. The tool is therefore
  annotated destructive, not read-only; it is not written to the audit log.
- A TOP over 480x480 pixels in area is scaled down to fit that area, and one over 2048x2048 is
  refused. A cell is at most 768 KiB and the cells of one sheet 10 MiB. The server tiles them,
  centred in equal slots without resizing, and returns an image only.

## Inspection

| Tool | Purpose |
|---|---|
| `td_list_children` | Children of a COMP, paginated, filterable by family and type. |
| `td_get_operator` | Type, flags, inputs and outputs, non-default parameters. |
| `td_get_parameters` | Full parameter detail for one operator (value, expression, mode, clamp limits, menu items). |
| `td_read_chop` / `td_read_dat` / `td_read_sop_info` | Sample data, with row and channel limits to protect context. |

A parameter's `min` and `max` are the limits TouchDesigner clamps to. Each is null when the value
is not clamped on that side. A parameter value that is not a finite number is the text `"nan"`,
`"inf"` or `"-inf"`. A `name` filter, here and in `td_describe_operator_type`, is one or more
names or patterns separated by spaces; a parameter matching any of them is listed once.

`td_read_chop` / `td_read_dat` / `td_read_sop_info`:

- `td_read_chop`: `channels` is one or more names or patterns separated by spaces. `count`
  defaults to 100, at most 2000, and at most 8000 values across the channels returned (at most
  64, else `channels_truncated`). `next_offset` pages on. A value that is not finite is the
  text `"nan"`, `"inf"` or `"-inf"`.
- `td_read_dat`: a text DAT returns `text` (at most 20,000 characters) and `is_table: false`. A
  table pages by `row_offset`/`row_limit` (at most 500 rows), at most 64 columns and 500
  characters a cell, and stops early past 40,000 characters, always returning one row if any
  remain. The bridge's token is replaced by `<redacted>`.
- `td_read_sop_info`: counts, bounds, group names and attribute names, never the geometry.
  Position (`P`) is not listed as an attribute. TouchDesigner's `__...` groups are left out.

## Construction

| Tool | Purpose |
|---|---|
| `td_create_operator` | Create by type name inside a parent. Validates the type first. |
| `td_connect` / `td_disconnect` | Wire inputs and outputs. |
| `td_set_parameters` | Batch-set values, expressions or bind references in one atomic undo step. |
| `td_delete_operator` | Destructive. Requires an explicit path. |
| `td_layout` | Auto-arrange a network so it stays readable. |
| `td_instantiate_palette` | Load a Palette component by name. |
| `td_apply_recipe` | Build a known-good network (feedback loop, GLSL pass, audio-reactive chain, and so on). |

Every construction tool, and `td_undo` and `td_redo`, follows these rules. Evidence for the
TouchDesigner behavior behind them is in `research/PHASE2_UNDO_RESULTS.md`.

- One successful call is one TouchDesigner undo entry, named `td-mcp <id>: ...` and returned as
  `undo_label`. A call that fails part way is rolled back, and its entry cannot be redone. A
  rolled-back call still discards whatever could have been redone before it.
- Each call is written to the audit log before and after it is applied; refused calls are logged
  too. If the log cannot be written, nothing is changed. If only the final line fails, the reply
  carries `audit_warning`. Nothing is changed while undo is disabled in TouchDesigner.
- Paths must be exact. The bridge's own component, `/ui` and `/sys` cannot be changed. `/`,
  `/local` and any COMP containing the bridge cannot be deleted.
- A lost reply is never retried. The error says the change may or may not have been applied.

| Tool | Behavior |
|---|---|
| `td_create_operator` | Type names include the family (`noiseTOP`); an unknown one suggests the closest. Names are letters, digits and underscores. A taken name is reported as `renamed`. Docked operators TouchDesigner adds are listed in `also_created`. Without a position, the node goes right of the existing ones. |
| `td_connect` | Both operators must be in the same network. Replaces what was on the input and lists it. Wires the left and right connectors only, not 3D or panel parenting. |
| `td_disconnect` | One input per call. Returns the inputs left, since later ones can move up. |
| `td_set_parameters` | Each change is exactly one of `value`, `expr` or `bind`. Wrong types and unknown menu entries are refused rather than coerced. `value` on a bound parameter changes only that parameter. A dead expression or bind fails the whole batch. Exported and read-only parameters and pulse buttons are refused. A clamped number is marked `adjusted`. An expression or bind that evaluates to NaN or infinity is applied, with a `warning`. Password values are not logged or returned by this tool. CHOP and DAT exports cannot be created: the Python parameter API exposes them read-only. |
| `td_delete_operator` | Reports former wires, operators deleted with it, and `downstream` wiring, which TouchDesigner may have spliced to the deleted operator's source. |
| `td_layout` | Direct children in columns by signal flow, keeping the network's top-left corner. Docked operators move with their host; annotations and protected operators stay. |
| `td_undo` / `td_redo` | 1 to 20 steps. Stops at the first entry the server did not make and leaves it alone. |

Undoing a bind restores the mode and value, but TouchDesigner keeps the unused bind text.

## Project and timeline

| Tool | Purpose |
|---|---|
| `td_project_info` | Build, project name/folder, cook rate (FPS), timeline state. |
| `td_timeline` | Play, pause, set frame, set range. |
| `td_save_project` | Save, or save-as inside the allowed root. |
| `td_undo` / `td_redo` | Step through the history of server-made changes. |

`td_project_info` does not report a project-level resolution: TouchDesigner has no single
project-wide resolution property (docs.derivative.ca's `App_Class`/`Project_Class`; checked
2026-11-25), only per-operator and per-window ones. Revisit once a specific output-resolution
need (e.g. the Window COMP backing the perform window) is identified.

`td_timeline` changes the root timeline (`op('/').time`), is annotated destructive, and follows
the construction rules above, except that it names no path:

- Any of `play`, `frame`, `start`, `end`; at least one. Frame numbers are whole.
- `start` and `end` also set the playback range, which TouchDesigner keeps separately and wraps
  the playhead into. A range ending before it starts, or a frame outside the playback range, is
  refused. A new range that leaves the playhead outside moves it to the range's first frame.
- `td_undo` reverses it, through an undo callback. The reply gives `previous` and `timeline`,
  each with `range_start` and `range_end`.

## Knowledge

| Tool | Purpose |
|---|---|
| `td_search_docs` | Search an offline index of the operator reference and Python class docs (from docs.derivative.ca). |
| `td_describe_operator_type` | Parameter list for an operator type read from the **installed** build, so it cannot drift from the docs. |

`td_describe_operator_type` (evidence: `research/PHASE3_KNOWLEDGE_RESULTS.md`):

- The bridge creates one operator of the type with cooking off inside its own component, reads it
  and destroys it before replying. Nothing stays in the project and no undo entry is made, so the
  tool is annotated read-only and is not written to the audit log.
- Types that talk to hardware (camera and other capture, audio devices, sensors, laser, DMX,
  serial, MIDI, mouse, keyboard and similar; the list is `DEVICE_TYPE_PREFIXES` in the bridge) are
  never created. Creating some of them asked for camera permission or opened network sockets.
  They are described from the build's parameter help file with `source: "help_file"` and a
  `note`: labels, types and summaries only.
- Each parameter reports name, label, style, page, default and, where they apply, default
  expression, clamp limits, slider range, menu entries, help, sequence block, `read_only` and
  `custom`. Fields that do not apply are left out. A sequence shows the blocks a new operator has.
- Filters: a name pattern, and a page. At most 250 parameters per call (default 60), with
  `next_offset`. Help is cut to 200 characters unless filtering by name, which also adds menu
  labels. Also reported: pages, input counts (`max_inputs` was 9999 on a Composite TOP, which
  takes any number) and the types TouchDesigner adds alongside.

`td_search_docs`:

- Searches the copy of docs.derivative.ca inside the installed TouchDesigner: operator reference
  and Python class pages, plus the other articles (`kind`: `operator`, `class` or `article`).
  Release notes are left out. TouchDesigner need not be running. Nothing is fetched online.
- The index is built on first use into `~/.td-mcp/docs-index.sqlite` and rebuilt when the help
  folder changes.
- Returns passages of at most 1200 characters with page, section and docs.derivative.ca URL,
  5 by default and at most 20, with `next_offset`. Order: reference pages whose whole title is in
  the query, then passages with every word, then with any word (`all_words: false`). Without a
  `page` filter, one page gives at most two passages, and identical text is returned once.
- A class page holds only that class's own members; inherited ones are on the parent's page.

## Escape hatch (gated)

| Tool | Purpose |
|---|---|
| `td_exec_python` | Run Python in TouchDesigner. Disabled by default, audited, time-limited. Whether it ships at all is an open question in `plans/TD_MCP_BUILD.md`. |
