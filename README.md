# touchme: a TouchDesigner MCP server

A local MCP server (Python, FastMCP, stdio) that lets Claude see, build and iterate on TouchDesigner projects, through a small bridge component running inside TouchDesigner.

Progress is tracked only in [plans/TD_MCP_BUILD.md](plans/TD_MCP_BUILD.md).

## Where things live

| Folder | Holds | Question it answers |
|---|---|---|
| [plans/](plans/README.md) | Build plans, open or closed | What is being built |
| [specs/](specs/README.md) | Architecture and tool catalog | How must it behave |
| [decisions/](decisions/README.md) | Decision records | Why is it this way |
| [research/](research/README.md) | Verified facts and reference notes | What did we look at |
| [guides/](guides/README.md) | How to install, run, test | How do I use this |

Folders for audits and incidents are created when the first one is written. Code lives in `src/td_mcp/` (the server), `bridge/` (the TouchDesigner side) and `tests/`; `evals/` is created with the first eval. The planned layout is in [specs/ARCHITECTURE.md](specs/ARCHITECTURE.md).

## Development

Needs `uv` and, for the live checks, TouchDesigner (the build in [research/TD_ENVIRONMENT.md](research/TD_ENVIRONMENT.md)).

```
uv sync
uv run ruff check . && uv run pyright && uv run pytest        # unit tests, no TouchDesigner needed

python3 bridge/build_spike.py --test-fixtures                 # builds bridge/build/td_mcp_bridge.tox (gitignored, private, embeds a fresh token)
# open that .tox in TouchDesigner, then:
export TD_MCP_TOKEN_FILE=$PWD/bridge/build/token.txt
uv run pytest tests/integration -m integration -p no:cacheprovider
```

`--test-fixtures` adds routes the integration tests need. With or without it, the bridge can change the open project; the integration tests create and delete `/td_mcp_test` and `/td_mcp_describe_check` and leave entries in the undo history. The documentation search tests need only an installed TouchDesigner, not a running one. The `.tox` is built from `bridge/src/callbacks.py`: rebuild and reopen it after any change to that file, or TouchDesigner keeps running the old copy.
