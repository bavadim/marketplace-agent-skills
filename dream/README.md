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

## Offer and daily delivery

`offer_savings_goal` is the model-facing entrypoint. It only returns a sandbox offer and an estimate, without opening an account. It can be used after a concrete, priced purchase recommendation or on an explicit savings request. `daily_proposals` produces the same report as `python -m dream.daily`; there is no separate `plan` tool. Both use deterministic arithmetic. The report uses the saved price (not a live catalog quote), available virtual rubles with conservative deductions for blocked money, and portfolio valuation separately. It is not a trade, withdrawal or checkout. If enough virtual cash is available, the report invites the user to recheck the real price and optionally open the product card; it never claims that the sandbox can pay for it. A missing or unusable balance is unknown, not zero.

Install `hermes-daily.sh` under the participant profile's `scripts/` directory, and create a private sibling `dream-daily.env` with shell assignments for `DREAM_CHECKOUT`, `DREAM_PYTHON`, `DREAM_DB`, `DREAM_OWNER`, `DREAM_CA_FILE` (if required), and `DREAM_TOKEN_FILE`. Store only paths in the file, make it operator-owned and mode 0600. Match the DB and owner of that profile's MCP instance. The runner builds a read-only Dream instance and prints the user-facing report verbatim. No open goals means no output.

Create one job per authorized owner in their verified private conversation via the Hermes cron tool: `no_agent: true`, `script: dream-daily.sh`, `deliver: origin`, `attach_to_session: true`, and the agreed daily schedule/timezone. Do not create it from a CLI with an unknown origin; for operator provisioning bind the exact verified private destination and enable the per-job mirror. Keep one binding per owner (update the existing job instead of adding duplicates), pause on disconnect, and don't accept chat IDs from model or tool arguments. Separate profiles are required by this implementation; shared-bot routing remains out of scope. Test a run and a reply in the actual client: continuable delivery can mirror into a matching session or open a thread, depending on the target. It does not resume an LLM run. The scheduler, not Dream MCP, owns delivery. No exactly-once delivery across manually created duplicate jobs is guaranteed.

Amounts are integer kopecks. An ambiguous bank outcome leaves the journal pending; the operator must reconcile before repeating the operation on that goal. Sandbox account life cycle, price data and order methods are documented at the T-Bank developer portal under SandboxService and MarketDataService. Unit tests exercise a fake bank and a real local MCP transport, not bank execution.
