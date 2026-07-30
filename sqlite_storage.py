"""Персистентное FSM-хранилище для aiogram на SQLite.

Штатный MemoryStorage теряет всё состояние при перезапуске: клиент, который
15 минут заполнял анкету и грузил фото, начинал бы заново после каждого деплоя.
Здесь состояние и данные переживают рестарт.
"""

import asyncio
import json
import sqlite3
from typing import Any, Dict, Mapping, Optional, Union

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey


def _key_to_str(key: StorageKey) -> str:
    return (
        f"{key.bot_id}:{key.chat_id}:{key.user_id}:"
        f"{key.thread_id or 0}:{key.business_connection_id or ''}:{key.destiny}"
    )


class SQLiteStorage(BaseStorage):
    """FSM-хранилище на SQLite. Операции синхронные, но быстрые (локальный файл),
    поэтому выполняются через to_thread, чтобы не блокировать event loop."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm (
                    key   TEXT PRIMARY KEY,
                    state TEXT,
                    data  TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    # --- вспомогательные синхронные операции ---

    def _sync_set_state(self, key: str, state: Optional[str]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO fsm (key, state) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state",
                (key, state),
            )
            conn.commit()
        finally:
            conn.close()

    def _sync_get_state(self, key: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT state FROM fsm WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _sync_set_data(self, key: str, data: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO fsm (key, data) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (key, json.dumps(data, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def _sync_get_data(self, key: str) -> Dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT data FROM fsm WHERE key = ?", (key,)).fetchone()
            if not row or not row[0]:
                return {}
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return {}
        finally:
            conn.close()

    # --- интерфейс BaseStorage ---

    async def set_state(self, key: StorageKey, state: Optional[Union[State, str]] = None) -> None:
        value = state.state if isinstance(state, State) else state
        async with self._lock:
            await asyncio.to_thread(self._sync_set_state, _key_to_str(key), value)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_state, _key_to_str(key))

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_set_data, _key_to_str(key), dict(data))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_data, _key_to_str(key))

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            skey = _key_to_str(key)
            current = await asyncio.to_thread(self._sync_get_data, skey)
            current.update(data)
            await asyncio.to_thread(self._sync_set_data, skey, current)
            return current

    async def get_value(
        self, storage_key: StorageKey, dict_key: str, default: Optional[Any] = None
    ) -> Optional[Any]:
        data = await self.get_data(storage_key)
        return data.get(dict_key, default)

    async def close(self) -> None:
        return None
