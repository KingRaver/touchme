# Tool Catalog

Type: spec
Status: open
Last verified: 2026-11-26 at `a569928`

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

## Inspection

| Tool | Purpose |
|---|---|
| `td_list_children` | Children of a COMP, paginated, filterable by family and type. |
| `td_get_operator` | Type, flags, inputs and outputs, non-default parameters. |
| `td_get_parameters` | Full parameter detail for one operator (value, expression, mode, clamp limits, menu items). |
| `td_read_chop` / `td_read_dat` / `td_read_sop_info` | Sample data, with row and channel limits to protect context. |

A parameter's `min` and `max` are the limits TouchDesigner clamps to. Each is null when the value
is not clamped on that side.

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
| `td_set_parameters` | Each change is exactly one of `value`, `expr` or `bind`. Wrong types and unknown menu entries are refused rather than coerced. `value` on a bound parameter changes only that parameter. A dead expression or bind fails the whole batch. Exported and read-only parameters and pulse buttons are refused. A clamped number is marked `adjusted`. Password values are not logged or returned by this tool. CHOP and DAT exports cannot be created: the Python parameter API exposes them read-only. |
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

## Knowledge

| Tool | Purpose |
|---|---|
| `td_search_docs` | Search an offline index of the operator reference and Python class docs (from docs.derivative.ca). |
| `td_describe_operator_type` | Parameter list for an operator type read from the **installed** build, so it cannot drift from the docs. |

## Escape hatch (gated)

| Tool | Purpose |
|---|---|
| `td_exec_python` | Run Python in TouchDesigner. Disabled by default, audited, time-limited. Whether it ships at all is an open question in `plans/TD_MCP_BUILD.md`. |
