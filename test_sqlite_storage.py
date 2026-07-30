import asyncio
import os
import tempfile
import unittest

from aiogram.fsm.storage.base import StorageKey

from sqlite_storage import SQLiteStorage


def key(user_id: int = 1) -> StorageKey:
    return StorageKey(bot_id=100, chat_id=user_id, user_id=user_id, destiny="default")


class TestSQLiteStorage(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                os.remove(p)

    async def test_state_roundtrip(self):
        s = SQLiteStorage(self.path)
        self.assertIsNone(await s.get_state(key()))
        await s.set_state(key(), "OrderFlow:waiting_for_name")
        self.assertEqual(await s.get_state(key()), "OrderFlow:waiting_for_name")
        await s.set_state(key(), None)
        self.assertIsNone(await s.get_state(key()))

    async def test_data_roundtrip_and_update(self):
        s = SQLiteStorage(self.path)
        self.assertEqual(await s.get_data(key()), {})
        await s.set_data(key(), {"full_name": "Иванов Иван"})
        self.assertEqual(await s.get_data(key()), {"full_name": "Иванов Иван"})
        merged = await s.update_data(key(), {"book_style": "акварель"})
        self.assertEqual(merged, {"full_name": "Иванов Иван", "book_style": "акварель"})
        self.assertEqual(await s.get_value(key(), "book_style"), "акварель")
        self.assertEqual(await s.get_value(key(), "missing", "def"), "def")

    async def test_survives_restart(self):
        """Главное: анкета клиента не теряется при перезапуске бота."""
        s1 = SQLiteStorage(self.path)
        await s1.set_state(key(7), "OrderFlow:waiting_for_relative_photo")
        await s1.set_data(key(7), {"current_folder_id": "abc123", "full_name": "Петров"})
        await s1.close()

        s2 = SQLiteStorage(self.path)  # как будто процесс перезапустили
        self.assertEqual(await s2.get_state(key(7)), "OrderFlow:waiting_for_relative_photo")
        self.assertEqual(
            await s2.get_data(key(7)), {"current_folder_id": "abc123", "full_name": "Петров"}
        )

    async def test_users_are_isolated(self):
        s = SQLiteStorage(self.path)
        await s.set_data(key(1), {"name": "первый"})
        await s.set_data(key(2), {"name": "второй"})
        self.assertEqual((await s.get_data(key(1)))["name"], "первый")
        self.assertEqual((await s.get_data(key(2)))["name"], "второй")

    async def test_concurrent_updates_do_not_lose_data(self):
        s = SQLiteStorage(self.path)
        await s.set_data(key(3), {})
        await asyncio.gather(*[s.update_data(key(3), {f"k{i}": i}) for i in range(20)])
        data = await s.get_data(key(3))
        self.assertEqual(len(data), 20)


if __name__ == "__main__":
    unittest.main()
