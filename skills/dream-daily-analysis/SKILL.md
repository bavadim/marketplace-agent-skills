---
name: dream-daily-analysis
license: MIT
description: "Report progress on open savings goals, on demand or on a schedule: check target, holdings, available cash, time to goal and next step. Read only; do not open accounts, fund, trade or schedule jobs."
---

# Daily Savings Analysis

- Read the current owner's stored goals. For each open goal, obtain portfolio value, positions, available cash and observation time. Exclude closed goals. If there are no open goals, do not send an empty scheduled digest.
- Check the exact variants' current price and stock if authorized catalog access is available. Otherwise label the price as saved, not current. Report unavailable data as unknown, not as zero.
- Show portfolio value and available cash separately. With verified amounts, report the gap and estimate the baseline assuming no returns. Use an installed calculator or the bundled [forecast helper](scripts/forecast.py) only through a trusted, restricted execution path. Do not enable a general shell for a scheduled job. Without reliable arithmetic, omit an exact estimate. If the goal is underfunded and there are no further contributions, there is no baseline completion date.
- Show upside and downside scenarios only with consent; include potential loss and delay. They are scenarios, not predictions. Do not increase risk because the goal is behind. Compare instruments using fresh liquidity, maturity, spread, lot size and costs. Do not recommend trading on missing or stale data.
- If only the portfolio valuation covers the target, suggest discussing a sale. If available cash covers a verified current price, give the product links. If only the saved price is known, ask to recheck it first. Virtual funds cannot be withdrawn or spent. Do not trade, withdraw, pay or request tokens.

Suggested scheduled prompt: “Report on this profile's open savings goals using the attached methodology. Read only. Return the digest for the originating conversation.”
