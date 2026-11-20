import sys
import tempfile
import unittest
from pathlib import Path
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import ElicitResult


class StdioTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_plan_and_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            for readonly in ('0', '1'):
                forms = []

                async def decline(context, params):
                    forms.append(params)
                    return ElicitResult(action='decline')

                params = StdioServerParameters(command=sys.executable, args=['-m', 'mcp_servers.dream.server'], env={
                    'DREAM_DB': str(Path(directory) / 'dream.db'), 'DREAM_OWNER': 'test',
                    'DREAM_READ_ONLY': readonly, 'TINVEST_SANDBOX_TOKEN': ''})
                async with Client(params, mode='legacy', elicitation_callback=decline) as client:
                    names = {t.name for t in (await client.list_tools()).tools}
                    expected = {'connection_status', 'list_goals', 'goal_status', 'last_price', 'order_status'}
                    if readonly == '0':
                        expected |= {'begin_connection', 'create_goal', 'fund_goal', 'place_limit_order', 'close_goal'}
                    self.assertEqual(names, expected)
                    goals = await client.call_tool('list_goals')
                    self.assertFalse(goals.is_error)
                    if readonly == '0':
                        result = await client.call_tool('create_goal', {'goal_id': 'g', 'title': 'Лук', 'target_kopecks': 50000})
                        self.assertFalse(result.is_error)
                        self.assertEqual(result.structured_content['status'], 'not_confirmed')
                        self.assertEqual(len(forms), 1)
                        self.assertIn('Открыть тестовый счёт', forms[0].message)
