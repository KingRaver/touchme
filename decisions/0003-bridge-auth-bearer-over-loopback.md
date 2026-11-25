# 0003: Bridge authentication is a bearer token over loopback

Type: decision
Status: complete
Last verified: 2026-11-25 at `e2db2cd`

Proposed from the Phase 0 evaluation and approved by the operator on 2026-11-25.

## Threat model

Binding to `127.0.0.1` keeps other computers out. The token exists to stop callers on this machine that the operator did not intend.

- **Web pages in the operator's browser.** A page can send requests to `http://127.0.0.1:<port>`. A cross-origin GET or form POST is delivered even when the page cannot read the reply, and DNS rebinding can get past that read restriction. Without a token, any site visited could trigger whatever the bridge does. This is the main threat and the one the prior-art bridge was exposed to (`research/PRIOR_ART_TOUCHDESIGNER_MCP.md`, finding 1). Phase 1 adds Host and Origin checks and no wildcard CORS header as the stronger defense against it; the token is the second layer.
- **Other local users and processes** that discover the port.
- **Mutating tools are effectively code execution.** From Phase 2, `td_create_operator` plus `td_set_parameters` can create a Script or Execute DAT containing Python, so any caller able to mutate can run code as the operator even with `td_exec_python` off.

Out of scope: a process running as the same user that can read the `0600` token file. It could already do far more than call the bridge.

Today the bridge is read-only (`/ping` and a capture; a test-only route that creates one TOP exists only in builds made with `--test-fixtures`), so the token is mostly insurance until the Phase 2 mutating tools ship.

## Decision

Authenticate the bridge with our own bearer-token check, with the Web Server DAT bound to `127.0.0.1`. Do not build on the DAT's Basic authentication, Secure (TLS) or Verify Client options in Phase 1.

## Why

Evidence is in `research/PHASE0_SPIKE_RESULTS.md`, "Web Server DAT authentication options".

- **Basic** works, but it is code-only and needs a plaintext `userPasswords` dict inside the callbacks DAT. The secret ends up in the same place as a bearer token, with no extra protection and no documented comparison behavior.
- **TLS** worked once with a self-signed certificate, then 3 of 3 attempts with CA-signed certificates left the instance not listening or took the process down. The cause is unknown. A bridge that can take down the user's TouchDesigner session is worse than the exposure it would close on loopback.
- **Verify Client** has no documented trust-store parameter, and the one attempt to enable it took the instance down.
- Loopback binding is verified (`127.0.0.1` only, IPv4 only), and the token is checked on every request, including 401 for a missing or wrong token.

## Trade-offs accepted

- Traffic is plain HTTP on loopback. Any local process that can read the token file can call the bridge. The token file is `0600`, and the client refuses a token file readable by others.
- No mutual authentication of the bridge to the server beyond the port and token.

## Revisit if

- TLS or Verify Client is explained and made reliable on a later build.
- The bridge ever has to listen on a non-loopback interface. Then TLS stops being optional.
