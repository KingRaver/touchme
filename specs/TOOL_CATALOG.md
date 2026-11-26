# Tool Catalog

Type: spec
Status: open
Last verified: 2026-11-26 at `8cbebbe`

The MCP tools the server exposes. All names use a `td_` prefix. Read-only tools carry `readOnlyHint`. Tools that delete or overwrite carry `destructiveHint`. Structure and constraints are in `specs/ARCHITECTURE.md`. Build order is in `plans/TD_MCP_BUILD.md`.

## Perception

| Tool | Purpose |
|---|---|
| `td_capture_top` | Render a TOP to PNG/JPEG at a chosen size, returned as an MCP image. The core feedback tool. |
| `td_capture_contact_sheet` | Several TOPs or time samples in one image, to compare variations cheaply. |
| `td_get_errors` | Errors and warnings across a subtree, with operator path and message. |
| `td_get_performance` | FPS, cook time per operator (top N), GPU/CPU memory, dropped frames. |
| `td_snapshot_graph` | Compact text/JSON description of a network: ops, types, connections, flags. |

## Inspection

| Tool | Purpose |
|---|---|
| `td_list_children` | Children of a COMP, paginated, filterable by family and type. |
| `td_get_operator` | Type, flags, inputs and outputs, non-default parameters. |
| `td_get_parameters` | Full parameter detail for one operator (value, expression, mode, range, menu items). |
| `td_read_chop` / `td_read_dat` / `td_read_sop_info` | Sample data, with row and channel limits to protect context. |

## Construction

| Tool | Purpose |
|---|---|
| `td_create_operator` | Create by type name inside a parent. Validates the type first. |
| `td_connect` / `td_disconnect` | Wire inputs and outputs. |
| `td_set_parameters` | Batch-set values, expressions or export bindings in one atomic undo step. |
| `td_delete_operator` | Destructive. Requires an explicit path. |
| `td_layout` | Auto-arrange a network so it stays readable. |
| `td_instantiate_palette` | Load a Palette component by name. |
| `td_apply_recipe` | Build a known-good network (feedback loop, GLSL pass, audio-reactive chain, and so on). |

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
