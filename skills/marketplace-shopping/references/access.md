# Provider access

## Sessions

Use the same API for both providers:

```json
{"method":"session.ensure","params":{}}
{"method":"session.refresh","params":{}}
{"method":"session.status","params":{}}
```

Reads use the owned headless Chromium profile for each provider. Prefer `marketplace-accounts` for setup/renewal: call `session.validate`, then `session.login.start` for affected providers, give its private local link to the user, and call `session.login.finish` after they complete login. Both sites can be connected on one page without launching desktop windows. Do not ask for cookies/passwords/SMS codes in chat. Optional `providers` scopes diagnostics; explicit `profile` retains the legacy authorized desktop-cookie import path, not desktop browser control.

The clients retain their existing private snapshot paths: `${XDG_STATE_HOME:-~/.local/state}/ozon-bom/session.json` and `aliexpress-bom/session.json`. The shared manager stores private metadata under `marketplace-accounts/`, adopts existing Ozon profiles where possible and seeds existing credentials on initial migration. Profiles retain origin storage; snapshots preserve session cookies across Chromium shutdown. `session.status` is saved metadata; `session.validate` is a current account-page check. Actual order/cart reads separately verify their own schemas/access. Local session/profile data may also live in a skill's private `.runtime/` or `.secrets/` directory; exclude it from Git, installation payloads and reports.

## Owned browser and network

Both providers use the same owned persistent **native headless Chromium** runtime with Patchright. DISPLAY/Wayland are removed; desktop Chromium is read only during an explicit legacy import. AliExpress API requests execute inside Chromium with the current profile; parsers and cart reconciliation retain their existing contracts. Browser and NetworkService group membership is checked. The engine serializes calls per provider while allowing independent sites to run concurrently.

Retain the configured group route. A timeout is not proof of DNS failure; session_busy is not proof that login expired. Do not automatically change the router/proxy rules, use the user’s desktop browser, or switch to a visible browser.

The existing runtime is `${XDG_DATA_HOME:-~/.local/share}/ozon-bom/venv/bin/python`. For a missing driver, run `python3 assets/marketplace/vendors/ozon/scripts/setup_browser.py` from the shopping skill directory. It installs the pinned tested dependencies; use the system Chromium where present.

## Errors

- `login_required`/unverified login: use the shared local login handoff and resume the same task after verification.
- `session_busy`: another process owns the session; wait for it or serialize requests. Do not delete its lock/profile.
- transport/navigation errors: read the stage and actual error code. Only explicit DNS error codes justify a DNS diagnosis.
- challenge/rate/access block: do not repeat identical requests indefinitely. For an actual visible challenge in an authorized task, use captcha-assist and verify access afterward; otherwise return the structured blocker.
- schema/unsupported/partial: retain the good source data and state what is missing. Neither HTTP 200 nor an absent order array proves authenticated emptiness.
- uncertain cart write: read the current cart; reuse the operation ID for reconciliation and preserve other rows.

## Implementations

The factory is `assets/marketplace/providers/factory.py`; the common interface is `providers/base.py`. Provider-specific request mapping is in `providers/ozon.py` and `providers/aliexpress.py`. Tested low-level clients are bundled under `vendors/<provider>/scripts/`. Use these internals only for a concrete access/adapter repair; ordinary shopping and orders use the public entrypoints.

Ozon delivery cost/account address can be absent from the current widget. AliExpress validates a quote’s city using observed platform geo codes; it never guesses them from an IP. A requested city that cannot be confirmed stays unverified. The API keeps those gaps visible.
