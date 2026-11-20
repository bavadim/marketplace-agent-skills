"""Per-user stdio MCP. Bind owner at launch, never from a model argument."""
import os
from typing import Any
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel

from dream.bank import Sandbox
from dream.core import Dream, preview


class Consent(BaseModel):
    confirm: bool


def build(dream):
    server = MCPServer('dream', instructions='Official T-Invest sandbox only. Money amounts are integer kopecks.')

    async def approved(ctx, text):
        try:
            result = await ctx.elicit('Песочница, без реальных денег. ' + text, Consent)
        except Exception:
            return False
        return result.action == 'accept' and result.data.confirm is True

    @server.tool(structured_output=True)
    def connection_status() -> dict[str, Any]:
        """Check the server-side sandbox credential; never request a token in chat."""
        try:
            return dream.connection()
        except ValueError as exc:
            return {'mode': 'sandbox', 'connected': False, 'reason': str(exc)}

    @server.tool(structured_output=True)
    def plan(target_kopecks: int, initial_kopecks: int = 0, monthly_kopecks: int = 0) -> dict[str, Any]:
        """Compute convergence under fixed illustrative scenarios; no forecast of market returns."""
        return preview(target_kopecks, initial_kopecks, monthly_kopecks)

    @server.tool(structured_output=True)
    def list_goals() -> list[dict]:
        """List only this profile's goals."""
        return dream.list()

    @server.tool(structured_output=True)
    def goal_status(goal_id: str) -> dict[str, Any]:
        """Read the virtual portfolio and available cash of one goal."""
        return dream.status(goal_id)

    @server.tool(structured_output=True)
    def daily_digest() -> list[dict]:
        """Read all open goals; mark a bank error without inventing balances."""
        rows = []
        for goal in dream.list():
            if not goal['closed']:
                try:
                    rows.append(dream.status(goal['id']))
                except ValueError:
                    rows.append({**goal, 'live_status': 'unavailable'})
        return rows

    @server.tool(structured_output=True)
    def last_price(instrument_id: str) -> dict[str, Any]:
        """Get last market price by instrument UID or FIGI, not an execution promise."""
        return dream.bank.call('GetLastPrices', {'instrumentId': [instrument_id]})

    @server.tool(structured_output=True)
    def order_status(goal_id: str, order_id: str) -> dict[str, Any]:
        """Read the actual status of a sandbox order."""
        return dream.order_state(goal_id, order_id)

    if dream.readonly:
        return server

    @server.tool(structured_output=True)
    async def begin_connection(ctx: Context) -> dict[str, Any]:
        """Confirm sandbox access via a non-secret form; no real token entry."""
        if not await approved(ctx, 'Подключить тестовый портфель?'):
            return {'status': 'not_confirmed'}
        return connection_status()

    @server.tool(structured_output=True)
    async def create_goal(goal_id: str, title: str, target_kopecks: int, ctx: Context,
                          monthly_kopecks: int = 0) -> dict[str, Any]:
        """Open a separate virtual brokerage account for this goal after confirmation."""
        if not await approved(ctx, f'Открыть тестовый счёт для цели «{title[:80]}» ({target_kopecks} коп.)?'):
            return {'status': 'not_confirmed'}
        return dream.create(goal_id, title, target_kopecks, monthly_kopecks)

    @server.tool(structured_output=True)
    async def fund_goal(goal_id: str, operation_id: str, amount_kopecks: int, ctx: Context) -> dict[str, Any]:
        """Credit virtual rubles after confirmation; reuse operation_id after interruption."""
        if not await approved(ctx, f'Пополнить цель {goal_id[:128]} виртуальными деньгами: {amount_kopecks} коп.?'):
            return {'status': 'not_confirmed'}
        return dream.fund(goal_id, operation_id, amount_kopecks)

    @server.tool(structured_output=True)
    async def place_limit_order(goal_id: str, operation_id: str, instrument_id: str, side: str,
                                lots: int, limit_kopecks: int, ctx: Context) -> dict[str, Any]:
        """Submit a confirmed virtual limit order; read order_status to verify execution."""
        if not await approved(ctx, f'Заявка {side}: {instrument_id[:100]}, {lots} лот(ов), '
                                    f'лимит {limit_kopecks} коп. за бумагу; цель {goal_id[:128]}.'):
            return {'status': 'not_confirmed'}
        return dream.order(goal_id, operation_id, instrument_id, side, lots, limit_kopecks)

    @server.tool(structured_output=True)
    async def close_goal(goal_id: str, operation_id: str, ctx: Context) -> dict[str, Any]:
        """Close a virtual account. This is not withdrawal or a sale."""
        if not await approved(ctx, f'Закрыть виртуальный счёт цели {goal_id[:128]}?'):
            return {'status': 'not_confirmed'}
        return dream.close(goal_id, operation_id)

    return server


def main():
    os.umask(0o077)
    path = Path(os.environ['DREAM_DB'])
    if not path.is_absolute():
        raise ValueError('DREAM_DB must be absolute')
    owner = os.environ['DREAM_OWNER'].strip()
    if not owner:
        raise ValueError('DREAM_OWNER required')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = os.environ.get('DREAM_TOKEN_FILE')
    token = Path(token_file).read_text().strip() if token_file else os.environ.get('TINVEST_SANDBOX_TOKEN')
    dream = Dream(path, Sandbox(token, os.environ.get('DREAM_CA_FILE')), owner,
                  readonly=os.environ.get('DREAM_READ_ONLY') == '1')
    build(dream).run('stdio')


if __name__ == '__main__':
    main()
