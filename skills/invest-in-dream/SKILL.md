---
name: invest-in-dream
license: MIT
description: "Offer a sandbox savings goal after recommending a specific purchase with a price, unless the user wants to buy now or has declined. Also use when asked to save for a goal, open, fund, list or close savings goals, or schedule a digest."
---

# Invest in Your Dream

1. Use the goal, verified price and product links from the conversation. Ask for the initial amount and monthly contribution plan. Do not treat unknown shipping as zero. Make clear that investments are simulated. A purchase recommendation is not consent to open an account.
2. Check sandbox access. If its token is missing or rejected, use the available MCP form for sandbox connection. Never request it in chat, as a tool argument, or in response to a transient network error. Stop if the form is unavailable. Do not accept production tokens.
3. With consent, create a savings goal with its name, target amount, contribution plan and links. The backend verifies the dedicated account. Request separate consent for a virtual deposit; report success only after readback. No external payment link is needed. Do not retry an account opening or deposit with an ambiguous result.
4. On request, compare eligible sandbox instruments with holding cash. Check the time horizon, lot size, liquidity, spread, currency, quote freshness and costs; do not promise returns. If data is unavailable or no instrument fits, keep the cash. Before a buy or sell, show the account, instrument, lots, limit price and estimated costs. Use a separately confirmed operation and then check execution. Do not suggest leverage or short selling. Closing a goal requires separate confirmation.
5. After a verified deposit, offer a daily digest. Only on explicit consent, agree on time and time zone; find and update or create one native Hermes job for all the owner's goals from their verified private conversation. Attach the installed daily analysis skill and a short, self-contained task prompt; deliver to the originating conversation with reply context. Allow only verified read-only tools: no trades, token entry or general shell. If the profile, private destination or tool scope cannot be verified, do not schedule. Verify that the job was saved.
6. For a request to see goals, read this owner's stored savings goals. For a request to resume reports, recover scheduling without opening a duplicate account. Never claim the job is active before verification; a missing job does not imply a bank error.
