---
name: marketplace-accounts
description: Connect or renew the user's marketplace accounts before shopping or reading orders. Set up Ozon and AliExpress together through one private local login page backed by owned headless Chromium, check each login, and reuse saved sessions. Use for sign-in, expired sessions, account connection and marketplace authentication setup; network configuration is a separate task.
---

# Marketplace accounts

## Interface

Run `python3 scripts/accounts.py capabilities` or `python3 scripts/accounts.py call --request REQUEST.json --output RESULT.json` from this directory. Stdin is supported with `--request -`. Install the sibling `marketplace-shopping` skill for the shared runtime. Keep request/results outside installed skills.

## Connect accounts

1. Call `session.status` for the requested providers; the default is all registered marketplaces. This is saved metadata, not proof of current login. Call `session.validate` for a current check. Keep each provider's result; a challenge or network failure is not an expired login.
2. For missing/unverified login, reuse an already signed-in desktop session when the user has authorized that reuse: call `session.refresh` with its explicit `profile` and only the affected `providers`, then `session.validate`. Do not reimport working accounts. If import cannot establish login or there is no authorized source, call `session.login.start`. Explain that its local page controls a separate headless profile: being signed in on the ordinary marketplace tab does not sign in this profile automatically. Return the exact `handoff_url` as a clickable link, not the marketplace's cart/login URL. No desktop browser window is launched or attached.
3. Ask the user to complete login on that page and report completion. Phone numbers, passwords, SMS codes, cookies and tokens stay out of chat. Do not take screenshots of login forms or inspect entered credentials through agent tools.
4. After the user's completion, call `session.login.finish` with the returned `handoff_id`. Success requires observed signed-in state on each account page, not cookie count or HTTP 200. Partial results leave the handoff open; report the affected provider and actual blocker. Resume the original shopping/orders task after a verified login.

Normal reads reuse the same owned provider profile. `session.ensure` and `session.refresh` without `profile` check this profile; they do not import desktop cookies automatically. An explicit `profile` retains the legacy authorized import path when the user wants it. A user may cancel with `session.login.cancel`; `session.forget` clears local saved access only when requested and does not revoke the server's session.

## Access failures

The shared runtime is native headless Chromium with Patchright for both providers. The existing group route is retained; do not repair proxy/firewall/DNS settings as part of account connection. `session_busy` means a login/operation already holds that profile. Close the handoff or wait; never remove its lock.

The handoff is loopback-only, authenticated and expires after 30 minutes by default. Give the link only to this user; it is private and works on the machine running the scripts. For a remote executor the user needs an explicitly configured local tunnel or another supported handoff; do not publish the login server.

Read [the API and state reference](references/api.md) for method parameters and privacy boundaries. A visible challenge can be completed by the user in the same handoff. Never report a real login as verified based solely on offline helper tests.
