# touchme: a TouchDesigner MCP server

A local MCP server (Python, FastMCP, stdio) that lets Claude see, build and iterate on TouchDesigner projects, through a small bridge component running inside TouchDesigner.

Status: planning. No code yet. Progress is tracked only in [plans/TD_MCP_BUILD.md](plans/TD_MCP_BUILD.md).

## Where things live

| Folder | Holds | Question it answers |
|---|---|---|
| [plans/](plans/README.md) | Build plans, open or closed | What is being built |
| [specs/](specs/README.md) | Architecture and tool catalog | How must it behave |
| [decisions/](decisions/README.md) | Decision records | Why is it this way |
| [research/](research/README.md) | Verified facts and reference notes | What did we look at |

Folders for guides, audits and incidents are created when the first one is written. Code folders (`src/`, `bridge/`, `tests/`, `evals/`) are created when the first code lands. The planned layout is in [specs/ARCHITECTURE.md](specs/ARCHITECTURE.md).
