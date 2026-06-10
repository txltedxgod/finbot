# 💰 Finance Bot

Telegram-бот для личного учёта доходов и расходов. Записывай транзакции, смотри отчёты по дням и месяцам — прямо в Telegram.

![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)
![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-20.x-blue?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

## Возможности

- ➕ **Добавление дохода / расхода** — сумма + опциональное описание
- 📅 **Отчёт за сегодня** — все записи дня с балансом
- 📆 **Выбор даты** — встроенный календарь с навигацией по месяцам
- 📊 **Отчёт за месяц** — разбивка по дням с итогами
- 📈 **Статистика** — общий баланс, кол-во записей, топ расходов по категориям

## Стек

- [`python-telegram-bot`](https://python-telegram-bot.org/) v20 — асинхронный фреймворк
- `python-dotenv` — переменные окружения
- `Database` — собственный модуль (`database.py`) для хранения записей

## Установка

**1. Клонировать репозиторий**
```bash
git clone https://github.com/txltedxgod/finance-bot
cd finance-bot
```

**2. Установить зависимости**
```bash
pip install python-telegram-bot python-dotenv
```

**3. Создать `.env` файл**

Бот читает конфиг из файла `fin.env`:
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
├── bot.py          # Основная логика бота, хендлеры
├── database.py     # Модуль работы с БД
├── fin.env         # Переменные окружения (не коммитить!)
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

## Лицензия

MIT
