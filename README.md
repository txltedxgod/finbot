# 💰 FinBot — Telegram-бот учёта финансов

Личный финансовый ассистент в Telegram: записывай доходы и расходы, раскладывай траты по своим категориям, смотри отчёты за день, месяц и по датам, следи за статистикой и курсами валют — всё прямо в чате.

![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white)
![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-22.7-26A5E4?style=flat-square&logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-embedded-003B57?style=flat-square&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## ✨ Возможности

- ➕ **Доходы и расходы** — быстрый ввод суммы с необязательным описанием
- 🏷️ **Свои категории** — создавай собственные категории расходов на лету
- 📊 **Статистика по категориям** — кликабельная разбивка трат по каждой категории
- 📅 **Отчёт за сегодня** — все записи дня и итоговый баланс
- 🗓️ **Выбор даты** — встроенный календарь с навигацией по месяцам
- 📆 **Отчёт за месяц** — разбивка по дням с итогами
- 📈 **Общая статистика** — баланс, количество записей, топ расходов
- 👤 **Личный кабинет** — язык интерфейса и валюта в пару кликов
- 🌍 **Мультиязычность** — 🇷🇺 русский · 🇺🇦 українська · 🇬🇧 English
- 💱 **Курсы валют** — актуальные курсы онлайн (источник: Frankfurter)
- ⌨️ **Удобное меню** — постоянная клавиатура + inline-кнопки

---

## 🧩 Стек

| Компонент | Назначение |
| --- | --- |
| [`python-telegram-bot`](https://python-telegram-bot.org/) 22.7 | Асинхронный фреймворк бота |
| [`python-dotenv`](https://pypi.org/project/python-dotenv/) | Загрузка переменных окружения |
| [`httpx`](https://www.python-httpx.org/) | Запросы курсов валют |
| `sqlite3` (стандартная библиотека) | Хранение данных |

---

## 📁 Структура проекта

```
finbot/
├── bot.py            # Логика бота: хендлеры, меню, диалоги
├── database.py       # Работа с SQLite (users, records, categories)
├── i18n.py           # Переводы интерфейса (ru / uk / en)
├── requirements.txt  # Зависимости
├── fin.env           # Переменные окружения (НЕ коммитить!)
├── finance.db        # База данных (создаётся автоматически)
└── README.md
```

---

## 🚀 Установка и запуск

**1. Клонировать репозиторий**

```bash
git clone https://github.com/txltedxgod/finbot
cd finbot
```

**2. Создать виртуальное окружение и поставить зависимости**

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**3. Настроить токен**

Создай файл `fin.env` в корне проекта:

```env
BOT_TOKEN="123456789:AA...ваш_токен_от_BotFather"
```

Токен можно получить у [@BotFather](https://t.me/BotFather) → `/newbot`.

**4. Запустить**

```bash
python3 bot.py
```

Открой бота в Telegram и отправь `/start`.

---

## 🖥️ Деплой на сервер (24/7 через systemd)

Чтобы бот работал постоянно и сам перезапускался после сбоев и перезагрузок, оформи его как systemd-сервис.

Создай файл `/etc/systemd/system/finbot.service`:

```ini
[Unit]
Description=Finance Telegram Bot
After=network.target

[Service]
User=youruser
WorkingDirectory=/home/youruser/finbot
ExecStart=/home/youruser/finbot/venv/bin/python /home/youruser/finbot/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Включи и запусти:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now finbot
sudo systemctl status finbot
```

Логи в реальном времени:

```bash
journalctl -u finbot -f
```

---

## 🤖 Команды бота

| Команда | Описание |
| --- | --- |
| `/start` | Запустить бота и открыть главное меню |
| `/profile` | Личный кабинет: язык и валюта |
| `/skip` | Пропустить ввод описания при добавлении записи |
| `/cancel` | Отменить текущее действие |

---

## 🗄️ Схема базы данных

```
users        (id, name, lang, currency, created)
records      (id, user_id, type, amount, description, date, created_at, category_id)
categories   (id, user_id, name, created)   -- UNIQUE(user_id, name)
```

Записи (`records`) связаны с категориями (`categories`) через `category_id`. Индексы по `user_id + date` и `category_id` ускоряют отчёты.

---

## ⚙️ Как это работает

1. Пользователь отправляет `/start` → выбирает язык и валюту (при первом запуске).
2. Открывается главное меню: доход / расход / отчёты / статистика / кабинет.
3. При добавлении записи: сумма → категория (для расходов) → необязательное описание.
4. Данные сохраняются в SQLite и доступны в отчётах за день, месяц и по дате.

Диалоги построены на `ConversationHandler` (с `allow_reentry=True`), состояния охватывают выбор языка/валюты, ввод суммы, выбор/создание категории и ввод описания.

---

## 📝 Лицензия

MIT — см. файл [LICENSE](LICENSE).
