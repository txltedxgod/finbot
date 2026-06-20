# 💰 Finance Bot

Telegram-бот для личного учёта доходов и расходов. Записывай транзакции, смотри отчёты по дням и месяцам — прямо в Telegram.

![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)
![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-22.7-blue?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

## Возможности

- ➕ **Добавление дохода / расхода** — сумма + опциональное описание
- 📅 **Отчёт за сегодня** — все записи дня с балансом
- 📆 **Выбор даты** — встроенный календарь с навигацией по месяцам
- 📊 **Отчёт за месяц** — разбивка по дням с итогами
- 📈 **Статистика** — общий баланс, кол-во записей, топ расходов по категориям

## Стек

- [`python-telegram-bot`](https://python-telegram-bot.org/) v22.7 — асинхронный фреймворк
- `python-dotenv` — переменные окружения
- `Database` — собственный модуль (`database.py`) для хранения записей

## Установка

**1. Клонировать репозиторий**
```bash
git clone https://github.com/txltedxgod/finbot
cd finance-bot
```

**2. Установить зависимости**
```bash
pip install -r requirements.txt
```

**3. Создать `.env` файл**

Скопируй `.env.example` → `.env` и впиши токен (`cp .env.example .env`):
```env
BOT_TOKEN=your_telegram_bot_token_here
```

Получить токен можно у [@BotFather](https://t.me/BotFather) в Telegram.

**4. Запустить**
```bash
python bot.py
```

## Структура проекта

```
finance-bot/
├── bot.py           # Основная логика бота, хендлеры, клавиатуры
├── database.py      # Модуль работы с SQLite
├── i18n.py          # Переводы (ru/uk/en) и список валют
├── premium_emoji.py # Premium custom emoji (текст + иконки кнопок)
├── requirements.txt # Зависимости
├── finbot.service   # systemd unit для автозапуска
├── .env.example     # Шаблон переменных окружения (копируй в .env)
└── README.md
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Запустить бота, открыть главное меню |
| `/skip` | Пропустить ввод описания при добавлении записи |
| `/cancel` | Отменить текущее действие |

## Как это работает

1. Пользователь отправляет `/start`
2. Бот показывает inline-меню с действиями
3. При добавлении транзакции — последовательный ввод: сумма → описание
4. Все записи сохраняются в БД и доступны в отчётах

Логика построена на `ConversationHandler` с тремя состояниями:
- `CHOOSING_ACTION` — главное меню, выбор действия
- `ENTERING_AMOUNT` — ввод суммы
- `ENTERING_DESCRIPTION` — ввод описания (или `/skip`)

## .gitignore

Не забудь добавить в `.gitignore`:
```
fin.env
*.db
__pycache__/
*.pyc
```

## Премиум-эмодзи (custom emoji)

Бот умеет показывать премиум-эмодзи из любого набора. Это работает, потому что
владелец бота (ты) имеет Telegram Premium — отправлять премиум эмодзи может
бот, чей владелец премиум; зрителям Premium для просмотра не нужен.

**Как включить:**
1. Найди желаемый набор эмодзи.
2. Узнай `custom_emoji_id`: отправь своё кастомное эмодзи боту https://t.me/FIND_STICKER_ID_BOT и посмотри
   `update.message.entities` — там будет id для каждого эмодзи.
3. Впиши id рядом с нужным базовым эмодзи в `premium_emoji.py` → `EMOJI_IDS`.
4. Перезапусти бота. Эмодзи без id остаются обычными.

Отключить полностью: `PREMIUM_EMOJI=0` в `.env`.

> Технически: всё исходящее проходит через `PremiumBot` (подкласс `ExtBot`),
> который переводит лёгкий Markdown в HTML и оборачивает эмодзи в теги
> `<tg-emoji>`. Менять вызовы `reply_text` / `edit_message_text` не нужно.

## Лицензия

MIT
