# Install and run td-mcp

Type: guide
Status: open
Last verified: 2026-11-26 at `a569928`

How to install and launch the server, and the bridge it talks to. See `specs/ARCHITECTURE.md`
for what the two pieces are, and `research/TD_ENVIRONMENT.md` for the TouchDesigner build this
was developed against.

## Pin the launch to an exact version. Never `@latest` / unpinned.

The closest prior art (`research/PRIOR_ART_TOUCHDESIGNER_MCP.md`) is launched with
`npx -y touchdesigner-mcp-server@latest`. That line re-resolves on every launch, so when a
dependency in that package's supply chain was compromised (the March 2026 axios incident),
every one of its users pulled the compromised code the next time they opened their MCP
client, with no version bump or review on their end to catch it. Nothing about `-y`/`@latest`
is specific to that one package: the same pattern would resolve to a compromised version of
`td-mcp` just as fast, the moment any dependency in *this* server's chain is compromised.

Pin to a version you chose on purpose, and bump it only when you choose to:

- **Not yet released to PyPI.** This project is pre-1.0 and `td-mcp` is not yet published, so
  there is no version to pin on PyPI yet. Clone the repo and pin to a commit, not to a moving
  branch:

  ```bash
  git clone https://github.com/KingRaver/touchme.git td-mcp
  cd td-mcp
  git checkout <commit-sha-or-tag>      # a sha you chose, never `git checkout main` and leave it tracking
  uv sync
  ```

  Re-point to a newer commit only when you deliberately choose to (`git checkout <new-sha>`,
  then `uv sync` again), the same discipline a version pin gives you once one exists.

- **Once released to PyPI** (tracked in `plans/TD_MCP_BUILD.md`, Phase 5 packaging), launch
  with `uvx` pinned to an exact version:

  ```bash
  uvx td-mcp==<exact-version>
  ```

  Not `uvx td-mcp` and not `uvx td-mcp@latest` — both resolve again on every launch, which is
  the exact exposure above. Bump the pin yourself after reading the new version's changelog.

## Claude Code configuration

Point Claude Code at the pinned install, not at a bare command that could resolve to something
else later. For the pre-release, git-clone install above:

```json
{
  "mcpServers": {
    "td-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/td-mcp", "td-mcp"],
      "env": {
        "TD_MCP_TOKEN_FILE": "/absolute/path/to/td-mcp/bridge/build/token.txt"
      }
    }
  }
}
```

Once `td-mcp` is on PyPI, replace `command`/`args` with `uvx` and the pinned `td-mcp==<version>`
form above; keep `TD_MCP_TOKEN_FILE` (or `TD_MCP_TOKEN`) either way.

## Build and load the bridge

The server needs a running TouchDesigner with the bridge component loaded. The bridge is
built from source, never committed (`bridge/build/` is gitignored: the built `.tox` embeds a
fresh per-build token):

```bash
python3 bridge/build_spike.py            # writes bridge/build/td_mcp_bridge.tox and token.txt
```

The bridge can change the open project (create, wire, set, delete, undo). Every change it is
asked to make is recorded in `~/.td-mcp/audit.jsonl`; add `--audit-log PATH` to the build to
put the log elsewhere. Each change is one undo entry in TouchDesigner, so it can also be
reversed there with Undo. A parameter expression is Python that TouchDesigner runs, so approve
`td_set_parameters` calls with that in mind.

Open `bridge/build/td_mcp_bridge.tox` in TouchDesigner, then export the token path for the
server to read:

```bash
export TD_MCP_TOKEN_FILE=/absolute/path/to/td-mcp/bridge/build/token.txt
```

Rebuild and reopen the `.tox` after any change to `bridge/src/callbacks.py` — TouchDesigner
keeps running whatever was last loaded. The bridge binds to `127.0.0.1` only and checks a
bearer token plus the request's Host and Origin on every call (`decisions/0003-bridge-auth-bearer-over-loopback.md`).

Delete the previous `td_mcp_bridge` component before loading a rebuilt one: both use the same
port. Do not save a project inside `bridge/build/`, because a rebuild replaces that folder.

## If the bridge does not answer

Bring TouchDesigner to the front and try again. It has been seen to stop answering while another
application was in front, and to resume once it was brought forward
(`research/PHASE1_LIVE_RESULTS.md`; the cause is not confirmed).

## Verify it's working

```bash
uv run ruff check . && uv run pyright && uv run pytest      # no TouchDesigner needed
uv run pytest tests/integration -m integration -p no:cacheprovider   # needs the bridge loaded, above
```
