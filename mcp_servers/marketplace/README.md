# Marketplace MCP prototype

One private Streamable HTTP endpoint (`POST /mcp`) wraps the existing marketplace engine. It exposes `search`, `product`, `quote`, `session_status`, and `authorize` when the noVNC viewer is enabled. No cart, purchases, orders, browser control, account mutation, arbitrary URLs or filesystem arguments are MCP tools. `session_status` reports saved metadata, not live verification. A catalog quote can be incomplete.

## Identity and trust boundary

Each enrolled **human + client installation** gets its own random slot and expiring opaque bearer credential. A trusted operator verifies the person and client before enrollment. The secret is shown once; SQLite retains only its digest. Every HTTP request is authenticated independently, before tool discovery, and the verified slot selects all persistent browser and worker state. Tool arguments, chat names, caller-supplied user IDs, MCP session IDs and forwarded identity headers do not select a slot. Separate grants always create separate profiles, even for the same human or label. The provider separates its own browser profile within that slot.

- A dedicated Hermes profile or private agent backend can set `Authorization: Bearer …` via its secret store. Never put it in a prompt, chat, or browser. Explicitly allow only the intended user to use that connector. This bearer is replayable if stolen: revoke it, purge the old slot, and enroll again. It is not an Ozon cookie.
- **Never configure a shared Telegram bot or Open WebUI instance with one global bearer for personal sessions.** Until a per-user gateway adapter exists, do not enable this connector for a shared audience. That adapter must resolve an authenticated `(client installation, trusted sender/account ID)`, inject only that user's credential server-side, fail closed when identity is unresolved, and not hand credentials to the model. A public bot needs its own explicit enrollment flow and user consent. Recognizing the same human across two frontends is not implemented and must not follow a display name or self-reported ID.
- Browser roots, HOME, XDG state/data/config/cache and temp files are per-slot; existing provider locks plus an outer slot lock keep operations from racing. One server process admits up to four in-flight workers. This is **application-level isolation**, not isolation from a compromised service operator, host or worker: they share an OS user. Keep the prototype private; a hostile multi-tenant deployment needs stronger worker isolation, egress controls, quotas, retention and independent security review.
- Revocation blocks subsequent requests and leaves the files in place. It does not revoke Ozon's session on its site or forcibly abort a running request or viewer. Cancel an active handoff separately and purge provider data with `session.forget` after workers have stopped. An expired or revoked slot is never reassigned.

## Start in Docker (private pilot)

Run from the repository root. On a dedicated Linux pilot host, host networking keeps the **existing loopback-only login viewer** reachable by an operator's SSH port forward. It also shares the host network namespace: do not use this shortcut on a shared host with sensitive loopback services. The service itself binds to `127.0.0.1`; there are no public ports in this example.

```sh
docker build -f mcp_servers/marketplace/Dockerfile -t marketplace-mcp:prototype .
# Review the upstream profile before applying it to this service.
curl -fsSLo playwright-seccomp.json https://raw.githubusercontent.com/microsoft/playwright/v1.58.0/utils/docker/seccomp_profile.json
docker volume create marketplace-mcp-data
docker run -d --name marketplace-mcp --init --network host --shm-size=512m \
  --security-opt seccomp=./playwright-seccomp.json \
  --mount source=marketplace-mcp-data,target=/data marketplace-mcp:prototype
```

Chromium runs non-root with sandboxing enabled. If the host's Docker/kernel policy blocks sandboxed Chromium user namespaces, use a supported kernel and a reviewed seccomp profile (see Playwright's official Docker guidance); do not run privileged or disable Chromium's sandbox to make the prototype work. Connect to `http://127.0.0.1:8000/mcp` over a private SSH forward from the agent backend, or put the endpoint behind authenticated private HTTPS with explicit allowed Host values (`MARKET_MCP_HOSTS`). Keep authorization headers out of proxy logs. No browser-origin requests are accepted by `/mcp`; terminate client connections server-side. This prototype uses pre-provisioned bearer credentials, not OAuth discovery.

## Connect Hermes through a private tunnel

Run Marketplace MCP and Chromium on a remote Linux host that can reach the actual storefronts. For example, with the container above listening on `127.0.0.1:8000`, run this from the remote marketplace host after the receiving SSH administrator has restricted the forward to loopback for a dedicated account:

```sh
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -R 127.0.0.1:28080:127.0.0.1:8000 mmcp-tunnel@hermes-host.example
```

Use `GatewayPorts no` and restrict `PermitListen` to `127.0.0.1:28080` for that account; verify the actual listener does not bind to a wildcard. Add `127.0.0.1:28080` to the server's `MARKET_MCP_HOSTS` allowlist. On the Hermes host, a request to `http://127.0.0.1:28080/mcp` without credentials should return HTTP 401. Register that URL in the enrolled Hermes profile as a Streamable HTTP MCP, with that profile's bearer in the Authorization header via its secret store. Run `hermes mcp test marketplace` and then a live catalog lookup. For phone sign-in use the separate HTTPS viewer below, not a public MCP port.

## Enroll and authorize a browser profile

Commands below are **operator-only**. Command output from `issue` and `session.login.start` contains credentials: run in a private terminal, never through a model tool, shared chat, or a retained CI log.

```sh
docker exec marketplace-mcp python /app/mcp/market_mcp.py issue alice-hermes --days 7
docker exec marketplace-mcp python /app/mcp/market_mcp.py account SLOT session.login.start --provider ozon
```

Install the bearer only in the enrolled client's private backend. For manual Ozon sign-in, open the existing viewer through a private SSH forward of the **same loopback port** on the authorized human's workstation (`ssh -N -L PORT:127.0.0.1:PORT operator@vps`, using `PORT` from the handoff). Open the exact returned URL there. If the result is still `starting`, poll `session.login.status` with `--handoff-id`. The person enters credentials only in the displayed official site. After login, finish and check persistence:

```sh
docker exec marketplace-mcp python /app/mcp/market_mcp.py account SLOT session.login.finish --handoff-id HANDOFF_ID
docker exec marketplace-mcp python /app/mcp/market_mcp.py account SLOT session.validate --provider ozon
```

For disconnect, revoke the grant, wait for in-flight work to stop, cancel any active viewer (CLI cancellation is allowed even after revocation), then forget each linked provider. The viewer remains private; no Telegram Mini App, phone-first enrollment, shared Hermes/Open WebUI adapter or public login endpoint is shipped. A future phone path must validate the Telegram/WebUI user outside the model and bind an expiring single-use handoff to both the user and the enrolled client before relaying any frames or input. Do not forward the existing loopback viewer to the public web unchanged.

## Tests

```sh
python -m pip install -r mcp_servers/marketplace/requirements.txt httpx==0.28.1
PYTHONPATH=mcp_servers/marketplace python -m unittest discover -s mcp_servers/marketplace/tests -v
```

The tests exercise the actual MCP HTTP handler using synthetic per-slot state and a fixture worker; they do not sign into real accounts. A VPS/browser pilot must verify sandbox launch, provider access, sign-in and profile persistence before accepting real users.

## Optional multi-user Ozon authorization via noVNC

Set `MARKET_MCP_VIEWER_ORIGIN=https://demo.example` and add `demo.example` to `MARKET_MCP_HOSTS`. Serve one instance behind an HTTPS reverse proxy that preserves `Host` and passes WebSocket upgrades to the MCP service. Keep the upstream private and disable request-path logging for `/view/` (links are sensitive). There is no RFB TCP listener. The container needs the same sandbox-compatible kernel/security settings as the base service. Do not add a desktop, socket mounts, `--privileged`, or host files. `MARKET_MCP_VIEWER_LIMIT=4` sets the simultaneous browser limit (1–10); measure capacity before increasing it. Each viewer has a ten-minute deadline.

For each enrolled slot, issue a **separate** viewer key: `python mcp_servers/marketplace/market_mcp.py viewer-key SLOT`. Give it privately to that human; do not give it to the agent or post it alongside a link. `authorize()` takes no user/profile argument. Call it at the start of every Ozon workflow. It checks the caller's saved Ozon profile and verifies it live when present. It returns `{"ok":true,"status":"success"}` if verified, or `authorization_required` with `authorization_url` and `expires_in` for an interactive attempt. `authorization_in_progress` means a verification/cleanup is still running. In viewer mode the Ozon catalog rejects requests without a successful check in the last ten minutes. Restarting the service invalidates all viewer links; the saved browser profiles remain.

The human opens the link, enters their viewer key, and completes the official Ozon checks in Chromium on the VPS. The phone renders noVNC only; Ozon credentials go to the remote Chromium, not into MCP tool data or chat. One-time claim plus an origin-checked WebSocket cookie restrict access to that viewport. On signed-in evidence, the service seals the link, disconnects the active WebSocket, writes the profile and stops the screen. Call `authorize()` again to verify the saved profile in the actual MCP browser runtime. A CAPTCHA that clears without login is not success; a site refusal is not a recoverable puzzle by itself. If the profile is challenged after reopening, the transition must be investigated rather than looped automatically.

This pilot is for enrolled, trusted participants. Profiles/Xvnc sockets are separate, but browser workers still share an OS user and container. For untrusted tenants use separate worker containers/VMs, restrict egress, and review the browser policies, HTTPS and proxy logs before exposure. The new interactive flow is Ozon-only; leave the legacy operator SSH handoff private if you still need it. No tunnel to a participant's computer is required.

For an operator-approved, isolated pilot slot with no existing marketplace account, `viewer-invite SLOT TICKET` issues a one-use alternative to the separate viewer key. Append `#invite=VALUE` to the exact HTTPS claim page and deliver it only in the recipient's private channel. This is a bearer invitation, not an identity check; forwarding it gives the recipient a chance to claim it first. It is bound to the slot and ticket, expires within 10 minutes, and never grants an MCP bearer. The browser removes the fragment and submits the invite only when the user clicks the confirmation button; do not put it in a query string or logs.
