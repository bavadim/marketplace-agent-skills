# Dream MCP (sandbox only)

Local stdio service, one trusted Hermes profile per participant. Do not expose a single instance to a shared multitenant gateway: the owner is fixed at launch, not derived from a chat identity. The official sandbox REST backend is fixed; there is no production route or cash withdrawal. If no sandbox token is available or the bank rejects it with HTTP 401, `begin_connection` requests it using MCP form elicitation, not as a tool argument or ordinary assistant input. The form passes through the MCP host and may be retained by the client; use only sandbox credentials and test with a synthetic marker on the actual surface first. This is a sandbox-only exception to MCP guidance, not a production secret-entry channel. Protect the database, CA and token file with OS permissions and keep them outside the repository.

Requires Python 3.11+ and `mcp==2.3.0` in a dedicated virtual environment. From the repo root run `python -m unittest dream.test_dream dream.test_stdio dream.test_connection`.

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

`hermes mcp test dream` tests discovery; call `connection_status` separately to test bank authentication. Forms use legacy MCP elicitation: operations fail closed if no consent arrives. For user provisioning configure `DREAM_TOKEN_FILE` in a private directory (0700). The file may initially be absent; Dream creates it (0600) only after validating the submitted token and checking that it sees the existing open accounts. Test with the actual target Hermes/Telegram before inviting participants. `DREAM_READ_ONLY=1` omits all mutation tools; run a separate instance for scheduled digests. Provisioning, delivery schedules and multi-user gateway identity routing are not automated here.

## Offer and daily delivery

`offer_savings_goal` is the model-facing entrypoint. It only returns a sandbox offer and an estimate, without opening an account. It can be used after a concrete, priced purchase recommendation or on an explicit savings request. `daily_proposals` produces the same report as `python -m dream.daily`; there is no separate `plan` tool. Both use deterministic arithmetic. The report uses the saved price (not a live catalog quote), available virtual rubles with conservative deductions for blocked money, and portfolio valuation separately. It is not a trade, withdrawal or checkout. If enough virtual cash is available, the report invites the user to recheck the real price and optionally open the product card; it never claims that the sandbox can pay for it. A missing or unusable balance is unknown, not zero.

After `fund_goal` reports `funding_verified`, offer a daily digest. Only on a separate user confirmation, use Hermes's native cron tool in that verified private conversation. Check for an existing job before creating or updating one daily job per owner/dialogue. Set `deliver: origin`, `attach_to_session: true`, and the agreed time/timezone. Use a self-contained prompt that calls `daily_proposals` through a separate read-only Dream MCP instance sharing the owner's DB and token file. The scheduled model relays the deterministic report; it cannot trade. If scheduler setup fails, recover with `list_goals` and the cron tool; don't reopen or redeposit. No precreated job or Hermes patch is required. Verify Telegram delivery and reply context on the real client.

`hermes-daily.sh` remains an optional `no_agent` runner for installs that choose to provision a local script. It is not a prerequisite of the interactive scheduling path.

Amounts are integer kopecks. An ambiguous bank outcome leaves the journal pending; the operator must reconcile before repeating the operation on that goal. Sandbox account life cycle, price data and order methods are documented at the T-Bank developer portal under SandboxService and MarketDataService. Unit tests exercise a fake bank and a real local MCP transport, not bank execution.
