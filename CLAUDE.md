# CLAUDE.md

Project: a local TouchDesigner MCP server (Python, FastMCP, stdio) plus an in-TouchDesigner bridge `.tox`. Start with `README.md` and `plans/README.md`.

## Documentation conventions

Docs are organized by purpose, using the taxonomy from the `docs-reorg` skill.

- **Folders:** `plans/`, `specs/`, `decisions/`, `research/` exist now. Add `guides/`, `audits/`, `incidents/` only when the first document for them exists. No empty folders. No `archive/` or `old/`.
- **Root files** stay at the root: `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `LICENSE`.
- **Label header** directly under the title of every doc in those folders:
  ```
  Type: plan | audit | guide | spec | decision | research | incident
  Status: open | complete | complete-with-open-items (<what>) | superseded-by <path>
  Last verified: YYYY-MM-DD at `<short sha>`
  ```
  Status lives in the header, not in the folder.
- **Plans own their progress.** Checkboxes and evidence are written only in the plan that owns the work. Other docs and every folder `README.md` link to it and do not restate status.
- **Audits are immutable** once written. Fix work goes in a plan that links to the audit.
- **Correct specs and guides in place.** Annotate plans and audits with a dated note instead of rewriting them.
- **Decisions are numbered** (`decisions/NNNN-slug.md`). A changed decision is a new record that supersedes the old one.
- **Never delete docs.** Label or supersede them instead.

## Ground truth

Claims about TouchDesigner come from the installed build (see `research/TD_ENVIRONMENT.md`) or https://docs.derivative.ca/, never from memory.
