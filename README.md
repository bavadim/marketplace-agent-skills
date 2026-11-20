# Marketplace Agent Skills

One agent skill and two independent MCP services. The stylist defines what to find; the catalog service finds and verifies live offers. Dream provides a sandbox investment workflow, independently of the stylist.

## Skills

- [wardrobe-stylist](skills/wardrobe-stylist/SKILL.md) — style, fit, outfits and visualizations. Uses a configured catalog by capability, not by a hardcoded server name.

```bash
python3 scripts/install.py --skills wardrobe-stylist
```

Use `--dest /absolute/path/to/skills` for a test install, for isolation. The installer does not remove previously installed skills. `--replace` backs up existing installations before replacement.

## MCP services

- [Marketplace MCP](mcp/README.md): Streamable HTTP catalog, exact variants and price/delivery, with a separate private login handoff for Ozon. Browser runtime is under `mcp/runtime/`, not installed in Hermes as a skill. Build from this checkout with `docker build -f mcp/Dockerfile -t marketplace-mcp .`.
- [Dream MCP](dream/README.md): sandbox accounts, funding, quotes, orders, forecasts and portfolio. Start it independently using its own configuration. No production trading or real withdrawals.

## Offline verification

```bash
python -m pip install -r requirements.txt -r mcp/requirements.txt
python scripts/validate.py
python scripts/check.py
python -m unittest dream.test_dream dream.test_stdio
PYTHONPATH=mcp python -m unittest discover -s mcp/tests -v
```

Offline tests do not establish that Ozon login, a bank order, or Telegram confirmation works on the target host. Keep secrets and browser state outside the checkout.
