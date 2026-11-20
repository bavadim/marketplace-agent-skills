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
                    self.assertIn('daily_proposals', names)
                    self.assertNotIn('plan', names)
                    report = await client.call_tool('daily_proposals')
                    self.assertEqual(report.structured_content['goals'], [])
                    self.assertEqual('create_goal' in names, readonly == '0')
                    result = await client.call_tool('offer_savings_goal', {'title': 'Лук', 'target_kopecks': 50000})
                    self.assertFalse(result.is_error)
                    self.assertIsNone(result.structured_content['forecast']['baseline_months'])
                    if readonly == '0':
                        result = await client.call_tool('create_goal', {'goal_id': 'g', 'title': 'Лук', 'target_kopecks': 50000})
                        self.assertFalse(result.is_error)
                        self.assertEqual(result.structured_content['status'], 'not_confirmed')
                        self.assertEqual(len(forms), 1)
                        self.assertIn('Открыть тестовый счёт', forms[0].message)
