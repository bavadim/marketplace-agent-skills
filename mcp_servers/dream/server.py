"""Per-user stdio MCP. Bind owner at launch, never from a model argument."""
import os
from typing import Any
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from mcp_servers.dream.credentials import TokenFile

from mcp_servers.dream.bank import CredentialRequired, Sandbox
from mcp_servers.dream.core import Dream


class Consent(BaseModel):
    confirm: bool


class SandboxTokenInput(BaseModel):
    token: str = Field(min_length=1, max_length=4096, title="Sandbox-токен Т-Инвестиций", repr=False)


def build(dream, *, token_store=None, bank_factory=None):
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
        except CredentialRequired:
            return {'mode': 'sandbox', 'connected': False, 'status': 'credential_required',
                    'next_action': 'begin_connection'}
        except (OSError, ValueError):
            return {'mode': 'sandbox', 'connected': False, 'status': 'connection_unavailable'}

    @server.tool(structured_output=True)
    def list_goals() -> list[dict]:
        """Получить сохранённые копилки владельца профиля: цели, суммы, взносы и состояние счёта. Расписание не входит в результат."""
        return dream.list()

    @server.tool(structured_output=True)
    def goal_status(goal_id: str) -> dict[str, Any]:
        """Получить по копилке стоимость портфеля, позиции, доступные деньги и время наблюдения. Неизвестные значения — null."""
        return dream.status(goal_id)

    @server.tool(structured_output=True)
    def last_price(instrument_id: str) -> dict[str, Any]:
        """Получить последнюю котировку бумаги по UID или FIGI. Не подтверждает цену исполнения."""
        return dream.bank.call('GetLastPrices', {'instrumentId': [instrument_id]})

    @server.tool(structured_output=True)
    def order_status(goal_id: str, order_id: str) -> dict[str, Any]:
        """Read the actual status of a sandbox order."""
        return dream.order_state(goal_id, order_id)

    if dream.readonly:
        return server

    @server.tool(structured_output=True)
    async def begin_connection(ctx: Context) -> dict[str, Any]:
        """If sandbox access is missing or expired, collect a sandbox token via the MCP form. Never ask for it in chat or tool arguments. If already connected, do not show a form."""
        status = connection_status()
        if status.get('status') != 'credential_required':
            return status
        if token_store is None or bank_factory is None:
            return {**status, 'status': 'token_store_not_configured'}
        try:
            result = await ctx.elicit('Введи только тестовый токен Т-Инвестиций (Sandbox). '
                                      'Не вводи боевой токен. Ответ видит MCP-клиент.', SandboxTokenInput)
        except Exception:
            return {'status': 'form_unavailable'}
        if result.action != 'accept':
            return {'status': 'not_confirmed'}
        token = result.data.token
        if not token.isascii() or not all(c.isalnum() or c in '._~+/-=' for c in token):
            return {'status': 'invalid_token_format'}
        try:
            dream.check_replacement(bank_factory(token))
            token_store.write(token)
        except CredentialRequired:
            return {'status': 'token_rejected'}
        except (OSError, ValueError):
            return {'status': 'token_not_saved', 'message': 'Проверь доступ к счетам и хранилище профиля.'}
        return {'mode': 'sandbox', 'connected': True, 'status': 'connected'}

    @server.tool(structured_output=True)
    async def create_goal(goal_id: str, title: str, target_kopecks: int, ctx: Context,
                          monthly_kopecks: int = 0, purchase_urls: list[str] | None = None) -> dict[str, Any]:
        """Создать копилку по названию, сумме цели и плану взносов, открыть отдельный тестовый счёт после подтверждения. Суммы в копейках."""
        if not await approved(ctx, f'Открыть тестовый счёт для цели «{title[:80]}» ({target_kopecks} коп.)?'):
            return {'status': 'not_confirmed'}
        return dream.create(goal_id, title, target_kopecks, monthly_kopecks, purchase_urls=purchase_urls)

    @server.tool(structured_output=True)
    async def fund_goal(goal_id: str, operation_id: str, amount_kopecks: int, ctx: Context) -> dict[str, Any]:
        """Пополнить копилку виртуальными рублями после подтверждения и сверить зачисление. Для повтора используй тот же operation_id. Сумма в копейках."""
        if not await approved(ctx, f'Пополнить цель {goal_id[:128]} виртуальными деньгами: {amount_kopecks} коп.?'):
            return {'status': 'not_confirmed'}
        return dream.fund(goal_id, operation_id, amount_kopecks)

    @server.tool(structured_output=True)
    async def place_limit_order(goal_id: str, operation_id: str, instrument_id: str, side: str,
                                lots: int, limit_kopecks: int, ctx: Context) -> dict[str, Any]:
        """Выставить лимитную заявку на покупку или продажу в песочнице после подтверждения. Проверять исполнение отдельно."""
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


def from_env():
    os.umask(0o077)
    path = Path(os.environ['DREAM_DB'])
    if not path.is_absolute():
        raise ValueError('DREAM_DB must be absolute')
    owner = os.environ['DREAM_OWNER'].strip()
    if not owner:
        raise ValueError('DREAM_OWNER required')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = os.environ.get('DREAM_TOKEN_FILE')
    token = TokenFile(token_file).read if token_file else os.environ.get('TINVEST_SANDBOX_TOKEN')
    readonly = os.environ.get('DREAM_READ_ONLY') == '1'
    return Dream(path, Sandbox(token, os.environ.get('DREAM_CA_FILE'), readonly=readonly), owner,
                 readonly=readonly)


def main():
    token_file = os.environ.get('DREAM_TOKEN_FILE')
    build(from_env(), token_store=TokenFile(token_file) if token_file else None,
          bank_factory=lambda token: Sandbox(token, os.environ.get('DREAM_CA_FILE'), readonly=True)).run('stdio')


if __name__ == '__main__':
    main()
