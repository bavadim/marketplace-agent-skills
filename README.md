# Marketplace Agent Skills

English agent skills for shopping, order tracking and personal styling. Ozon and AliExpress use one JSON API: scripts handle repeatable site actions; the agent selects products and interprets evidence.

| Skill | Purpose | Required companion |
|---|---|---|
| [marketplace-shopping](skills/marketplace-shopping/SKILL.md) | Compare products, exact variants, prices and delivery; fill an authorized cart | None |
| [marketplace-accounts](skills/marketplace-accounts/SKILL.md) | Connect and renew both accounts through one local login page | `marketplace-shopping` |
| [marketplace-orders](skills/marketplace-orders/SKILL.md) | Combine orders, delivery dates, pickup addresses, deadlines and pickup-code images | `marketplace-shopping` |
| [wardrobe-stylist](skills/wardrobe-stylist/SKILL.md) | Collect measurements, find clothes and render three coordinated outfits with shopping links | None; marketplace integration is optional |
| [captcha-assist](skills/captcha-assist/SKILL.md) | Complete a visible verification challenge | Supported browser tool |

## Installation

### From GitHub

Ask the agent to use `$skill-installer` to install skills from `bavadim/marketplace-agent-skills` with the required `skills/<name>` paths, or run its helper:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/scripts/install-skill-from-github.py" --repo bavadim/marketplace-agent-skills --path skills/marketplace-shopping skills/marketplace-accounts skills/marketplace-orders skills/wardrobe-stylist skills/captcha-assist
```

For accounts or orders alone, include `skills/marketplace-shopping` too. The upstream installer copies the requested paths without resolving companions. Each skill includes its own instructions and MIT license.

### From this checkout

```bash
python3 scripts/install.py
```

The default installs all skills. `--skills marketplace-orders` selects a subset and includes its required companions. `--dest /absolute/test/skills` selects another destination. Existing installations are refused unless `--replace` is supplied; replacements are backed up outside the skills directory and restored if installation fails. Local private state is excluded from copied files and retained in replacement backups.

Installed skills become available on the next agent turn. Installing files does not connect accounts or configure a proxy.

## Dependencies

Live marketplace automation targets Linux with Python 3.11+, curl and Chromium. Node.js 18+ is needed for JavaScript checks. There is one dependency file for the entire checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

For separately installed marketplace skills, prepare their private browser runtime:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/marketplace-shopping/assets/marketplace/vendors/ozon/scripts/setup_browser.py"
```

The helper uses the same tested runtime versions, installs them in `${XDG_DATA_HOME:-~/.local/share}/ozon-bom/venv`, and downloads Chromium only when no system executable is found. It works without this repository's dependency file. It does not start a desktop browser, log in or change network settings. The legacy runtime directory name preserves existing installations.

Standalone wardrobe scripts need `beautifulsoup4` for public pages and `Pillow` for images. Use an interpreter containing these libraries or the host's workspace runtime. Optional merchant browsing also needs Patchright/Playwright and Chromium. Outfit rendering uses the host agent's native image tool.

## Accounts

Ask the agent to connect marketplace accounts. The shared API starts and completes one login handoff for both sites:

```json
{"method":"session.login.start","params":{"providers":["ozon","aliexpress"]}}
```

```json
{"method":"session.login.finish","params":{"handoff_id":"RETURNED_UUID"}}
```

Send requests through `marketplace-accounts/scripts/accounts.py call --request REQUEST.json --output RESULT.json`; shopping and orders expose the same account methods. The returned private local page controls owned headless Chromium. The user signs in, verifies each account and completes the connection there. The link expires after 30 minutes by default. Passwords and SMS codes belong on the site's page, never in chat.

`session.status` reads saved metadata; `session.validate` checks live authentication. `session.ensure` and `session.refresh` reuse the owned profile. Desktop-profile import is optional and requires explicit authorization; Linux import may need an accessible Secret Service keyring plus distribution GObject bindings. Network errors and challenges do not erase saved credentials. See the [account API](skills/marketplace-accounts/references/api.md).

Private state defaults to `${XDG_STATE_HOME:-~/.local/state}/` under `marketplace-accounts`, `ozon-bom`, `aliexpress-bom` and `marketplace-shopping`. Snap Chromium profiles may live in `~/snap/chromium/common/marketplace-accounts/`. Set `MARKETPLACE_BROWSER_ROOT` or `MARKETPLACE_CHROMIUM_BINARY` to override profile storage or the browser executable. Sessions remain local to each machine.

Local state may also live in a skill's `.runtime/` or `.secrets/` directory. `.gitignore` and the installer exclude sessions, cookies, keys, profiles, personal photos and generated account artifacts from publication.

See the [access reference](skills/marketplace-shopping/references/access.md) for connection and challenge errors.

## Use

- “Find a MIDI-to-USB interface and compare price and delivery.”
- “Show orders from both marketplaces that I need to collect this week.”
- “Хочу приодеться: подбери три демисезонных образа под мои фото и мерки.”

Shopping stops before checkout/payment. Sending pickup-code images requires an explicit recipient and channel. Styling renders illustrate an outfit; measured fit still requires exact garment dimensions.

## Structure and checks

```text
README.md                 Setup, usage and maintenance
LICENSE                   MIT license
requirements.txt          All Python dependencies
skills/<name>/            SKILL.md, UI metadata, references, scripts, assets and tests
scripts/install.py        Install skills and required companions
scripts/validate.py       Check source files, metadata, links and syntax
scripts/check.py          Run isolated offline suites
.github/workflows/        CI using the same dependencies and checks
```

`skills/` is the source of truth; editing an installed copy does not update this checkout. The installer discovers folders containing `SKILL.md`. Accounts and orders reuse the provider factory and Chromium adapters in `marketplace-shopping/assets/marketplace/`. Its `vendors/` directories contain our provider implementations, not copies of external libraries. Per-skill references document agent operations; general repository documentation stays in this README.

```bash
.venv/bin/python scripts/validate.py
.venv/bin/python scripts/check.py
```

Validation checks skill metadata, companion dependencies, links, licenses, syntax and obvious secret patterns. Offline suites use synthetic data without account access. Native browser tests are opt-in and use temporary state with synthetic marketplace responses:

```bash
MARKETPLACE_BROWSER_SMOKE=1 .venv/bin/python -m unittest discover -s skills/marketplace-accounts/tests -p test_native.py -v
```

Publish this source repository directly. Generated distributions and archives are unnecessary. `.gitignore` permits only source roots and excludes private state at every depth; validation also checks eligible files before installation. These checks do not replace reviewing files before publication.

## Coverage and license

Ozon supports pickup groups and split shipments. AliExpress shipment details and pickup-code export remain partial/unsupported. Missing delivery costs, dates, stock or dimensions stay unknown. CAPTCHA handling depends on the visible challenge and available browser actions.

[MIT](LICENSE). External libraries keep their own licenses. Account state, personal photos and product media are runtime inputs, not repository assets.
