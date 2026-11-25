# Prior Art: 8beeeaaat/touchdesigner-mcp

Type: research
Status: complete
Last verified: 2026-11-25 at `f980863` (the source review is of upstream commit `98119d2`; the re-verification section was checked on this project's build)

Full source-level review of the closest existing project, https://github.com/8beeeaaat/touchdesigner-mcp (MIT, TypeScript, npm `touchdesigner-mcp-server`). Cloned at `98119d2` into a scratch directory and read directly. Nothing from it was executed, except TouchDesigner's own `toeexpand` tool, run on a copy of the `.tox` to unpack it. Informs `plans/TD_MCP_BUILD.md` (decision A5).

## Read scope

All 225 tracked files were examined. How each group was examined:

| Group | Files | How |
|---|---|---|
| `src/` TypeScript, OpenAPI YAML, markdown templates | 68 | Every file opened, in full |
| `td/` Python, generator, template | 20 | Every file opened, in full |
| `td/mcp_webserver_base.tox` | 1 | Binary. Unpacked with `toeexpand` into an isolated directory; all 14 resulting text files read |
| `tests/` (unit, integration, e2e, Python) | 40 | Every file, in full |
| `plugin/` and `plugins/` (skills, hooks, references) | 31 | Every file, in full |
| `scripts/`, `docs/`, `.github/`, `.claude/`, `.codex/`, `.agents/`, `mcpb/`, `docker/`, root config | 53 | Every file, in full (the CHANGELOG, 1,155 lines, was read across three passes) |
| `assets/*.png` | 6 | Viewed |
| `package-lock.json` | 1 | **Audited by script, not read line by line** (7,237 lines of machine-generated JSON) |
| Symlinks `.agents/skills/*` | 4 | Point at `.claude/skills/*`, which were read |

**Gaps.** `src/gen/` (generated HTTP client and Zod schemas) and `td/modules/mcp/controllers/generated_handlers.py` are gitignored build output and are not in the repository, so they were never seen. Their behavior is inferred from the OpenAPI YAML and the Mustache template that produce them. Nothing was run, so runtime claims below are inferences from code and the authors' own recorded measurements, and are marked as such.

## What it is

Two processes joined by HTTP, the same shape as `decisions/0002-stdio-server-http-bridge.md`:

- **Node MCP server** (stdio or stateless Streamable HTTP) with 14 tools and 3 prompts, no resources. A single `TOOL_DEFINITIONS` table drives registration and the `describe_td_tools` manifest.
- **`.tox` with a Web Server DAT** on port 9981. Routes come from an OpenAPI file loaded at startup and dispatched to handler stubs generated into `api_service.py`.
- **Versioning:** the `.tox` reports an API version (`1.6.1`). The server gates on a minimum and an expected version, independent of the npm version.
- **Plugin for Claude Code and Codex:** skills and two hooks that bundle the server and teach conventions. It deliberately ships no operator or recipe knowledge, because that "rots against each TouchDesigner release".

## TouchDesigner facts, as recorded by the authors

These come from their tests and comments, "measured on 099.2025.33230" unless noted. We are now on 2025.33230 (the operator updated from 2025.32820 on 2026-11-25), the same build they measured on, so these are more likely to hold, but each still needs a live recheck in Phase 0. The embedded Python in their screenshots is 3.11, matching ours.

- **Threading (our Q1).** Every handler calls `td.op(...)`, `create`, `destroy` and parameter writes directly inside the Web Server DAT callback, with no queue or hand-off anywhere in `td/`. This works in practice. It is supporting evidence, not proof of which thread runs it, and the official Web Server DAT page is also silent on threading.
- **Bind address.** Official docs say a blank Local Address "will listen on all interfaces". The shipped `.tox` sets no Local Address. They also found the server binds **IPv4 only** (v0.4.4): `localhost` resolving to `::1` gave `ECONNREFUSED`, so they default to `127.0.0.1`.
- **Name collisions.** Creating a node with a name that exists does not fail. TouchDesigner silently renames it (`test_base_comp` becomes `test_base_comp4`). Always read the resolved path back.
- **Globs.** `td.op()` accepts globs and returns the first match: `td.op("/p/al*")` returned `/p/alpha`.
- **Operator names.** TouchDesigner refuses names containing any of 32 punctuation characters (listed in `tests/python/test_op_name_rule.py`). Leading digits are allowed. Non-ASCII names are refused.
- **Error streams.** `errors(recurse=True)` lines look like `path:  Error: msg` (two spaces); `warnings()` lines look like `path:Warning: msg` (none). Missing files, dangling references and shader failures land on `warnings()` with `errors()` empty. A healthy operator returns `""` from both. `recurse=True` only attributes messages to the owning operator.
- **Script OP exceptions** reach neither stream, nor `scriptErrors()`, nor an error DAT. They go to stderr only. Catching in the callback and calling `scriptOp.addError(msg)` does land on `errors()`.
- **`op(<dict>)` hangs TouchDesigner outright** (#244). Never pass non-string data to `td.op()`.
- **`OP.error()` is deprecated** in 2025 builds ("replaced by errors and addError").
- **Capture.** `TOP.saveByteArray('.jpg')` yields complete JPEGs with correct dimensions (verified by parsing the SOF marker, 512x256 and 128x64 after downscale). Downscaling creates and destroys a temporary `resolutionTOP`.
- **Globals.** `tdu`, `absTime`, `ui` and OP type names live in the `__main__` namespace and must be merged into an exec namespace.
- **A single non-ASCII character in the OpenAPI schema once took down every route** because the schema was read with TouchDesigner's default ASCII encoding.
- **A package named `mcp` or `utils` in TouchDesigner's own site-packages shadows the local packages** (for example the Anthropic MCP SDK installed into TouchDesigner's Python). They evict cached modules and put their paths first.

## What the official docs add (docs.derivative.ca/Web_Server_DAT, edited 2026-06-04)

The Web Server DAT has built-in **Local Address** (bind to one interface), **Secure (TLS)**, **Verify Client** (mutual TLS) and Basic authentication through the `authenticateBasic` Python method. The page says "security is the complete responsibility of the user". The prior art uses none of these. We can.

## Re-verification on 2025.33230 (2026-11-25)

The behaviors recorded above that we depend on were re-checked on a live 2025.33230. Detail and evidence are in `research/PHASE0_SPIKE_RESULTS.md`; this table is only the verdict.

| Prior-art claim | Verdict on our build |
|---|---|
| Name collision silently renames | Holds |
| `errors()` and `warnings()` line formats | Holds |
| Script OP exceptions reach no stream (`addError` does) | Holds |
| `td.op()` glob returns an unnamed match | Holds, and the match is arbitrary rather than first by name |
| JPEG capture via `saveByteArray` | Holds; PNG also works |
| `op(<dict>)` hangs TouchDesigner | **Did not reproduce** with `{"a": 1}` |

## Findings: gaps and risks

Read from code, docs and the authors' comments. None was run.

1. **Unauthenticated, all-interfaces, wildcard-CORS endpoint.** The `.tox` leaves Local Address blank (all IPv4 interfaces). `api_controller.py` sets `Access-Control-Allow-Origin: *`, answers `OPTIONS` with 200, and parses the body as JSON whatever the content type. There is no authentication. With the exec endpoint, a machine on the same network, or plausibly a web page in the user's browser, could run Python inside TouchDesigner. Their docs and `AGENTS.md` say services should stay on `127.0.0.1`, but that only describes where the client connects. This is the strongest reason for our design: bind 127.0.0.1, bearer token, no wildcard CORS, Host/Origin checks. Not reproduced, and not reported upstream.
2. **Glob paths reach destructive calls.** `delete_node`, `update_node`, `exec_node_method` and `create_node` resolve paths with bare `td.op(path)`. Only `get_node_errors` requires `owner.path == path`. By their own recorded behavior, a glob path would act on an unnamed node.
3. **The committed `.tox` is stale relative to its own source.** It embeds a copy of `import_modules` that differs from `td/import_modules.py`: the embedded copy swallows a missing or empty schema and prints, while the repo version raises, reads UTF-8 and checks for empty paths. Git history shows the `.tox` last changed 2026-07-01 and `import_modules.py` on 2026-09-19. The release zip ships both, and I cannot tell statically which copy wins at runtime. Their changelog says `td/modules/` is "baked into the .tox" while their self-debug skill says it is read from disk; the embedded DATs are the only parts that are baked in.
4. **No tool annotations or output schemas.** `registerTool` receives only `description` and `inputSchema`.
5. **Arbitrary execution is always on.** `execute_python_script` runs `eval`/`exec` with TouchDesigner's globals. `exec_node_method` calls any attribute name, including `destroy`. No gate, no audit log.
6. **No undo.** Nothing wraps mutations in an undo block.
7. **No request timeout.** `customInstance.ts` creates an axios instance with none.
8. **Debug logging hard-coded on** (`DEBUG = True`), and the generated handler template prints every request body.
9. **Errors come back as HTTP 200.** Failures are carried only in the JSON body.
10. **Supply chain.**
    - *The axios compromise.* In March 2026 a malicious `axios@1.14.1` was published (a remote-access trojan via `plain-crypto-js`). Anyone running `npx -y touchdesigner-mcp-server@latest` in that window pulled it. They then pinned axios exactly. The MCPB manifest still launches `@latest`, and the plugin uses `^2`, so every launch can resolve fresh dependency versions.
    - *Lockfile hygiene.* Their lockfile has 481 entries, every one resolved from `registry.npmjs.org` with an integrity hash. Two packages have install scripts (`esbuild`, `fsevents`). `plain-crypto-js` is absent. But the published package ships only `dist/` (`files` in `package.json`), so `npx` users never use that lockfile.
    - *Release pipeline.* The release job runs a third-party action pinned to a mutable branch (`gregoranders/nodejs-project-info@master`) with `contents: write`, and pushes tags. Other actions are pinned by version tag, not commit hash. npm publish does use `--provenance`.
    - *Registry integrity value.* `server.json` carries a `fileSha256` for the MCPB bundle that their own release docs say will not match the CI-rebuilt asset (non-deterministic zip).
11. **Compose publishes inspector ports on all interfaces.** `docker-compose.yml` maps `6274` and `6277` without a `127.0.0.1` prefix. Nothing starts an inspector in the container, so this is latent. The MCP HTTP port is bound to loopback.
12. **Doc and code drift.** `architecture.md` describes methods that do not exist (`getTransportInfo`, `getConnectionMetadata`), "connection pooling" that is not configured, and a `src/features/resources/` directory that does not exist. `CLAUDE.md` gives an HTTP default port of 3000 (the code has none), cites a `make clean` target (the Makefile has only `build`) and `.doc/` plans that are not in the repo. `touchDesignerServer.test.ts` mocks a module that does not exist. Two missing markdown templates (`moduleHelp`, `moduleHelpDetailed`) fall back to a default with a stderr note. One of their own skills still says the API version is 1.4.3 (it is 1.6.1).
13. **Known unfixed defect they documented:** `exec_node_method`'s "not callable" message interpolates the attribute's value rather than its name.
14. **Capability gaps against our catalog:** no wiring tool (a prompt suggests an exec script), no layout tool, no performance tool (their `perf` skill runs a script that reads CPU `cookTime` only, which it says does not cover GPU time), no docs search, no contact sheet, no resources.
15. **Parameter reads hide mode.** `get_td_node_parameters` returns `par.eval()` only, so an expression-driven parameter looks identical to a constant. Writing a constant then destroys the expression. Our `td_get_parameters` spec already includes mode and expression.

## What to borrow, and what not to

Borrow:
- The API-version gate and schema-first generation from one OpenAPI file.
- The errors-and-warnings model with explicit `incomplete` signalling and an all-clear that is withheld unless every caveat is ruled out.
- The token-cost controls (`detailLevel`, `limit`, a machine-readable `truncation` record).
- The fallback to `/` when a project has no `/project1`.
- Live lookup for operator facts. Their reference script creates a throwaway node, reads `par.name/label/default/menuNames/help`, then destroys it.
- Their test method: pure parsing logic tested against a stubbed `td`, with measured TouchDesigner output kept verbatim as fixtures, and one live-TouchDesigner suite that reconciles results against runtime values.
- Their lesson on versioning: package version and bridge-contract version are separate axes.

Do not copy:
- Unauthenticated, all-interfaces, wildcard-CORS endpoint.
- Bare `td.op(path)` for mutating calls.
- Always-on exec, no undo, no timeout, hard-coded debug logging.
- A committed `.tox` that can drift from its source. Build it from source in CI instead.
- `@latest` launch.
