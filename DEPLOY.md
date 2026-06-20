# Деплой FinBot на облачную виртуалку (Linux VPS)

Инструкция для Ubuntu 22.04 / 24.04 (Debian аналогично). Бот будет работать
24/7 и автоматически перезапускаться через systemd.

## 1. Создай VPS
Любой провайдер (Hetzner, DigitalOcean, Timeweb, Selectel, Aeza и т.д.).
Минималки хватит: 1 vCPU, 1 GB RAM, Ubuntu 24.04. Зайди по SSH:
```bash
ssh root@ТВОЙ_IP
```

## 2. Установи Python
```bash
apt update && apt install -y python3 python3-venv python3-pip
```

## 3. Залей файлы бота в /opt/finbot
Вариант А — через scp с своего компьютера (выполнять локально, не на сервере):
```bash
scp -r ./finbot root@ТВОЙ_IP:/opt/finbot
```
Вариант Б — через git (если залил репозиторий):
```bash
git clone ТВОЙ_REPO /opt/finbot
```

## 4. Виртуальное окружение и зависимости
```bash
cd /opt/finbot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Проверь токен
Создай `.env` из шаблона (`cp .env.example .env`) и впиши токен. В файле `/opt/finbot/.env` должна быть строка:
```
BOT_TOKEN=123456:ваш_токен
```
Проверь ручной запуск (Ctrl+C чтобы остановить):
```bash
python bot.py
```
Если бот отвечает в Telegram — всё ок, идём дальше.

## 6. Автозапуск через systemd
```bash
cp /opt/finbot/finbot.service /etc/systemd/system/finbot.service
systemctl daemon-reload
systemctl enable --now finbot
```

## 7. Проверка и логи
```bash
systemctl status finbot
journalctl -u finbot -f      # живые логи
```

## Обновление кода
```bash
cd /opt/finbot
# залил новые файлы →
git pull            # или снова scp
systemctl restart finbot
```

## Частые проблемы
- **Бот не стартует / Conflict 409** — где-то ещё запущен тот же токен
  (например локально на Windows). Останови вторую копию — polling работает
  только в одном экземпляре.
- **Нет премиум-эмодзи** — это нормально, если владелец бота без Premium.
  Сервер ни при чём. Отключить: `PREMIUM_EMOJI=0` в .env.
- **База finance.db** — лежит рядом с bot.py в /opt/finbot. Делай бэкапы
  этого файла, чтобы не потерять данные.

## SSL / certifi
На Linux проблемы с certifi (как на Windows) обычно нет. Если всё же
вылезет SSL-ошибка:
```bash
apt install -y ca-certificates && update-ca-certificates
pip install --upgrade --force-reinstall certifi
```
