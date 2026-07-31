# Bot Story Book — бот сбора анкет для персональной книги

Telegram-бот: собирает бриф на книгу (герои, стиль, возраст) и фотографии
родственников/питомцев/игрушек. Фото проверяются нейросетью Gemini, складываются
в папку заказа на Google Drive, строка заказа уходит в Google Sheets, админам
приходит уведомление с расчётом сложности и цены.

## Стек

Python 3.12 · aiogram 3 · Google Drive/Sheets API · Gemini (`google-genai`) · SQLite · APScheduler

Модель Gemini задаётся переменной `GEMINI_MODEL` (по умолчанию `gemini-2.5-flash`).

## Структура

| Файл | Назначение |
|---|---|
| `main.py` | Хендлеры, сценарий анкеты, интеграции |
| `core.py` | Чистая бизнес-логика: разбор данных, сложность, цена (покрыта тестами) |
| `sqlite_storage.py` | Персистентное FSM-хранилище (анкеты переживают перезапуск) |

## Запуск локально

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить токены
```

Первая авторизация в Google (один раз, нужен браузер):

```bash
ALLOW_INTERACTIVE_AUTH=true .venv/bin/python main.py
```

Появится `data/token.json` — его и нужно положить на сервер.

Дальше обычный запуск:

```bash
.venv/bin/python main.py
```

## Запуск в Docker

```bash
docker compose up -d --build
```

Положите `credentials.json` и `token.json` в том `bot_data` (`/app/data`) —
в образ они не входят и в репозиторий не коммитятся.

## Тесты

```bash
.venv/bin/python -m unittest discover -p "test_*.py" -v
```

## Важно про секреты

`credentials.json`, `token.json` и `.env` — в `.gitignore`. Не коммитьте их:
в `token.json` лежит refresh-токен с полным доступом к Диску и Таблицам.
Если такой файл попал в репозиторий — отзовите доступ на
https://myaccount.google.com/permissions и пересоздайте OAuth-клиент.

## Данные

Всё состояние — в `DATA_DIR` (в Docker это том `/app/data`):

- `stats.db` — статистика заказов по месяцам
- `fsm.db` — незаконченные анкеты клиентов
- `tmp/` — временные фото (чистятся автоматически раз в сутки)
- `credentials.json`, `token.json` — доступ к Google
