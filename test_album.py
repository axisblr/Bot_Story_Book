"""Проверяем поведение с альбомами: берём первое фото, остальные игнорируем."""

import os
import tempfile
import unittest

# main.py требует переменные окружения и создаёт каталоги при импорте
_tmp = tempfile.mkdtemp()
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("MAIN_FOLDER_ID", "test")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATA_DIR", _tmp)

from main import claim_album_photo  # noqa: E402


class FakeState:
    """Минимальная замена FSMContext: хранит данные в словаре."""

    def __init__(self):
        self.data = {}

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        return dict(self.data)


class TestAlbumHandling(unittest.IsolatedAsyncioTestCase):
    async def test_first_photo_of_album_is_taken(self):
        state = FakeState()
        self.assertTrue(await claim_album_photo(state, "album-1"))

    async def test_rest_of_album_is_ignored(self):
        state = FakeState()
        await claim_album_photo(state, "album-1")
        self.assertFalse(await claim_album_photo(state, "album-1"))
        self.assertFalse(await claim_album_photo(state, "album-1"))

    async def test_next_album_is_taken_again(self):
        state = FakeState()
        await claim_album_photo(state, "album-1")
        # новый альбом — снова берём первое фото
        self.assertTrue(await claim_album_photo(state, "album-2"))
        self.assertFalse(await claim_album_photo(state, "album-2"))


if __name__ == "__main__":
    unittest.main()
