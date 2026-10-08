# Account API

All methods use the shared `{method, params}` JSON boundary. Results contain provider statuses, never site credentials. Both marketplaces are selected by default; `providers:["ozon"]` scopes an operation.

| Method | Required parameters | Optional parameters | Meaning |
|---|---|---|---|
| `session.status` | None | `providers` | Saved metadata, no network verification |
| `session.validate` | None | `providers` | Current signed-in state in owned Chromium |
| `session.ensure` | None | `providers`, `profile` | Check/reuse owned login; explicit profile opts into legacy import |
| `session.refresh` | None | `providers`, `profile` | Recheck owned login; explicit profile requests legacy reimport |
| `session.login.start` | None | `providers`, `ttl_seconds` | Start/reuse a local human handoff; TTL 60–3600 seconds, default 1800 |
| `session.login.status` | `handoff_id` | None | Handoff progress, no screenshot or credential values |
| `session.login.finish` | `handoff_id` | None | Verify selected accounts and close after complete success |
| `session.login.cancel` | `handoff_id` | None | Close the handoff, retaining saved state |
| `session.forget` | `providers` | None | Forget explicitly selected local access; no server revocation |

```json
{"method":"session.login.start","params":{"providers":["ozon","aliexpress"]}}
```

The result includes a private `handoff_id`, `handoff_url` and expiry. The user signs in on that page. Then:

```json
{"method":"session.login.finish","params":{"handoff_id":"RETURNED_UUID"}}
```

If the user is already signed into Chromium and authorized session reuse, import the explicit source profile for the affected provider before asking them to log in again:

```json
{"method":"session.refresh","params":{"providers":["aliexpress"],"profile":"/absolute/chromium/profile"}}
{"method":"session.validate","params":{"providers":["aliexpress"]}}
```

These are separate requests. The source profile is read, not controlled as a desktop browser. AliExpress checks a private candidate before replacing its snapshot. A successful import is followed by a fresh owned-profile check. If a login handoff is holding the profile, cancel that handoff first. The local handoff's profile is separate from the user's ordinary Chromium profile; its link must be opened exactly as returned, rather than opening the marketplace website directly.

`session.status` is always metadata-only. `authenticated:null` in a live check means unverified, not anonymous. Preserve network/challenge/unknown-schema failures separately from `login_required`. A partial handoff remains available until cancelled, completed or expired. A cancelled/expired handoff may leave some providers connected; validate them before resuming.

Private metadata and handoffs live under `${XDG_STATE_HOME:-~/.local/state}/marketplace-accounts/`. Provider snapshots retain legacy `ozon-bom/session.json` and `aliexpress-bom/session.json` paths for adapter compatibility. Chromium profiles use the private state root, or `~/snap/chromium/common/marketplace-accounts/` for system Snap Chromium. Existing Ozon profiles are adopted on first migration where possible. `MARKETPLACE_BROWSER_ROOT` overrides the profile root; `MARKETPLACE_CHROMIUM_BINARY` selects an executable. Directories must belong to this user with mode 0700; JSON state uses 0600.

The profile retains persistent cookies and origin storage. Legacy cookies are seeded during initial migration or an explicit new import. Absent session cookies are restored from the last synchronized snapshot because Chromium can discard them on shutdown. Updated site-scoped cookies/localStorage/IndexedDB are saved automatically. The existing private state paths remain the default. Local state inside a skill is also permitted when convenient; use a private `.runtime/` or `.secrets/` directory, excluded from Git and installation payloads. Local credentials are never included in reports. The login page retains frames and input events in memory; it does not write them to logs or screenshots.

For a missing driver, run the [shared setup helper](../../marketplace-shopping/assets/marketplace/vendors/ozon/scripts/setup_browser.py). No system network rules or browser windows are changed by this helper.
