# Dream MCP (sandbox only)

Local stdio service, one trusted Hermes profile per participant. Do not expose a single instance to a shared multitenant gateway: the owner is fixed at launch, not derived from a chat identity. The official sandbox REST backend is fixed; there is no production route or cash withdrawal. The token never enters elicitation or MCP tool arguments. Protect the database, CA and token file with OS permissions and keep them outside the repository.

Requires Python 3.11+ and `mcp==2.3.0` in a dedicated virtual environment. From the repo root run `python -m unittest dream.test_dream dream.test_stdio`.

Example Hermes configuration (replace paths and owner; use an absolute Python path, and launch from the repository via a trusted wrapper that changes to the checkout and runs `exec …/python -m dream.server`):

```yaml
mcp_servers:
  dream:
    command: /absolute/path/to/dream-launcher
    env:
      DREAM_DB: /private/path/dream.db
      DREAM_OWNER: judge-1
      DREAM_CA_FILE: /private/path/tinvest-ca.pem
      DREAM_TOKEN_FILE: /private/path/sandbox-token
```

`hermes mcp test dream` tests discovery; call `connection_status` separately to test bank authentication. Forms use legacy MCP elicitation: operations fail closed if no consent arrives. Test with the actual target Hermes/Telegram before inviting participants. `DREAM_READ_ONLY=1` omits all mutation tools; run a separate instance for scheduled digests. Provisioning, delivery schedules and multi-user gateway identity routing are not automated here.

Amounts are integer kopecks. An ambiguous bank outcome leaves the journal pending; the operator must reconcile before repeating the operation on that goal. Sandbox account life cycle, price data and order methods are documented at the T-Bank developer portal under SandboxService and MarketDataService. Unit tests exercise a fake bank and a real local MCP transport, not bank execution.
