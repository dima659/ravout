# Использует временную БД и настройки, созданные test_app.
import unittest
from unittest.mock import AsyncMock
import test_app as support
from app.main import deliver
from app import bot as bot_module
from app.core import db

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    setUp=support.AppTests.setUp
    tearDown=support.AppTests.tearDown
    payload=support.AppTests.payload
    async def test_deliver_to_manager(self):
        rid=self.client.post('/api/requests',data=self.payload()).json()['id']
        fake=AsyncMock()
        await deliver(fake,rid)
        self.assertEqual(fake.send_message.await_count,1)
        args=fake.send_message.call_args
        self.assertIn(f'№{rid}',args.args[1])
        self.assertEqual(len(args.kwargs['reply_markup'].inline_keyboard),3)
    async def test_status_saved_and_notified(self):
        rid=self.client.post('/api/requests',data=self.payload()).json()['id']
        fake=AsyncMock()
        self.assertTrue(await bot_module.set_status(fake,rid,'working'))
        with db() as c: self.assertEqual(c.execute('SELECT status FROM requests WHERE id=?',(rid,)).fetchone()[0],'working')
        fake.send_message.assert_awaited_once()
    async def test_invalid_status_rejected(self):
        fake=AsyncMock()
        self.assertFalse(await bot_module.set_status(fake,1,'invalid'))
        fake.send_message.assert_not_awaited()
    async def test_non_admin_rejected(self):
        fake=AsyncMock();fake.from_user.id=987654321
        await bot_module.admin(fake)
        fake.answer.assert_not_awaited()

