import logging
import os
from dotenv import load_dotenv

load_dotenv("fin.env")
from datetime import datetime, date
import calendar
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from database import Database


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States
CHOOSING_ACTION, ENTERING_AMOUNT, ENTERING_DESCRIPTION = range(3)

db = Database()


# ─── HELPERS ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Доход",  callback_data="add_income"),
         InlineKeyboardButton("➖ Расход", callback_data="add_expense")],
        [InlineKeyboardButton("📅 Сегодня",  callback_data="view_today"),
         InlineKeyboardButton("📆 По дате",  callback_data="pick_date")],
        [InlineKeyboardButton("📊 Месяц",   callback_data="view_month"),
         InlineKeyboardButton("📈 Статистика", callback_data="stats")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]])


def format_day_report(records, target_date: date, user_id: int) -> str:
    day_str = target_date.strftime("%d.%m.%Y")
    weekday = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][target_date.weekday()]

    if not records:
        return f"📋 *{day_str} ({weekday})*\n\nЗаписей нет."

    income_lines, expense_lines = [], []
    total_income = total_expense = 0

    for r in records:
        emoji = "💚" if r["type"] == "income" else "🔴"
        line = f"{emoji} {r['amount']:,.0f} ₽"
        if r["description"]:
            line += f" — {r['description']}"
        if r["type"] == "income":
            income_lines.append(line)
            total_income += r["amount"]
        else:
            expense_lines.append(line)
            total_expense += r["amount"]

    text = f"📋 *{day_str} ({weekday})*\n\n"

    if income_lines:
        text += "💰 *Доходы:*\n" + "\n".join(income_lines) + "\n\n"
    if expense_lines:
        text += "💸 *Расходы:*\n" + "\n".join(expense_lines) + "\n\n"

    balance = total_income - total_expense
    sign = "+" if balance >= 0 else ""
    text += f"─────────────────\n"
    text += f"📥 Итого доход:  *{total_income:,.0f} ₽*\n"
    text += f"📤 Итого расход: *{total_expense:,.0f} ₽*\n"
    text += f"💼 Баланс дня:   *{sign}{balance:,.0f} ₽*"
    return text


def format_month_report(records, year: int, month: int) -> str:
    month_name = [
        "Январь","Февраль","Март","Апрель","Май","Июнь",
        "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"
    ][month - 1]

    text = f"📊 *{month_name} {year}*\n\n"

    if not records:
        return text + "Записей нет."

    # Group by day
    by_day = {}
    for r in records:
        d = r["date"]
        by_day.setdefault(d, {"income": 0, "expense": 0})
        by_day[d][r["type"]] += r["amount"]

    total_income = total_expense = 0
    for d in sorted(by_day):
        inc = by_day[d]["income"]
        exp = by_day[d]["expense"]
        total_income += inc
        total_expense += exp
        bal = inc - exp
        sign = "+" if bal >= 0 else ""
        dt = datetime.strptime(d, "%Y-%m-%d")
        weekday = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][dt.weekday()]
        text += f"`{dt.strftime('%d.%m')}` ({weekday})  "
        if inc:  text += f"💚{inc:,.0f}"
        if exp:  text += f"  🔴{exp:,.0f}"
        text += f"  →{sign}{bal:,.0f}\n"

    balance = total_income - total_expense
    sign = "+" if balance >= 0 else ""
    text += f"\n─────────────────\n"
    text += f"📥 Доходы:  *{total_income:,.0f} ₽*\n"
    text += f"📤 Расходы: *{total_expense:,.0f} ₽*\n"
    text += f"💼 Баланс:  *{sign}{balance:,.0f} ₽*"
    return text


def make_calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    month_name = [
        "Январь","Февраль","Март","Апрель","Май","Июнь",
        "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"
    ][month - 1]

    buttons = []
    # Header row with nav
    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1, year + 1)
    buttons.append([
        InlineKeyboardButton("◀", callback_data=f"cal_nav_{prev_y}_{prev_m}"),
        InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore"),
        InlineKeyboardButton("▶", callback_data=f"cal_nav_{next_y}_{next_m}"),
    ])
    # Weekday headers
    buttons.append([InlineKeyboardButton(d, callback_data="ignore") for d in ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]])

    # Day cells
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"day_{year}_{month}_{day}"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def make_month_nav_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    month_name = [
        "Январь","Февраль","Март","Апрель","Май","Июнь",
        "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"
    ][month - 1]

    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1, year + 1)

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("◀", callback_data=f"month_{prev_y}_{prev_m}"),
            InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore"),
            InlineKeyboardButton("▶", callback_data=f"month_{next_y}_{next_m}"),
        ],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
    ])


# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.full_name)
    await update.message.reply_text(
        f"👋 Привет, *{user.first_name}*!\n\n"
        "Я помогу вести учёт доходов и расходов.\n"
        "Выбери действие:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return CHOOSING_ACTION


async def main_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📌 Главное меню — выбери действие:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    ctx.user_data.clear()
    return CHOOSING_ACTION


async def add_income_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["type"] = "income"
    ctx.user_data["date"] = date.today().isoformat()
    await query.edit_message_text(
        "💰 *Новый доход*\n\nВведи сумму (например: `5000`):",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )
    return ENTERING_AMOUNT


async def add_expense_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["type"] = "expense"
    ctx.user_data["date"] = date.today().isoformat()
    await query.edit_message_text(
        "💸 *Новый расход*\n\nВведи сумму (например: `1500`):",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )
    return ENTERING_AMOUNT


async def enter_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(",", ".").replace(" ", "")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Некорректная сумма. Введи число, например `2500`:",
            parse_mode="Markdown"
        )
        return ENTERING_AMOUNT

    ctx.user_data["amount"] = amount
    ttype = "доход" if ctx.user_data["type"] == "income" else "расход"
    await update.message.reply_text(
        f"✅ Сумма: *{amount:,.0f} ₽*\n\n"
        f"Теперь введи описание (или отправь `/skip` чтобы пропустить):",
        parse_mode="Markdown"
    )
    return ENTERING_DESCRIPTION


async def enter_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    description = update.message.text
    if description.startswith("/skip"):
        description = ""
    await _save_record(update, ctx, description)
    return CHOOSING_ACTION


async def skip_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _save_record(update, ctx, "")
    return CHOOSING_ACTION


async def _save_record(update: Update, ctx: ContextTypes.DEFAULT_TYPE, description: str):
    user_id = update.effective_user.id
    ttype   = ctx.user_data["type"]
    amount  = ctx.user_data["amount"]
    rec_date = ctx.user_data.get("date", date.today().isoformat())

    db.add_record(user_id, ttype, amount, description, rec_date)

    emoji = "💰" if ttype == "income" else "💸"
    label = "Доход" if ttype == "income" else "Расход"
    desc_line = f"\n📝 {description}" if description else ""
    await update.message.reply_text(
        f"{emoji} *{label} записан!*\n"
        f"💵 Сумма: *{amount:,.0f} ₽*{desc_line}\n"
        f"📅 Дата: {rec_date}",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    ctx.user_data.clear()


async def view_today_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    today = date.today()
    records = db.get_records_by_date(update.effective_user.id, today.isoformat())
    text = format_day_report(records, today, update.effective_user.id)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Вчера", callback_data=f"day_{today.year}_{today.month}_{today.day - 1}" if today.day > 1 else "ignore"),
         InlineKeyboardButton("🔙 Меню",  callback_data="main_menu")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


async def pick_date_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    today = date.today()
    await query.edit_message_text(
        "📆 Выбери дату:",
        reply_markup=make_calendar_keyboard(today.year, today.month)
    )


async def calendar_nav_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, year, month = query.data.split("_")
    await query.edit_message_text(
        "📆 Выбери дату:",
        reply_markup=make_calendar_keyboard(int(year), int(month))
    )


async def day_selected_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month, day = query.data.split("_")
    selected = date(int(year), int(month), int(day))
    records = db.get_records_by_date(update.effective_user.id, selected.isoformat())
    text = format_day_report(records, selected, update.effective_user.id)

    # Prev / Next day
    cal_days = calendar.monthrange(int(year), int(month))[1]
    prev_day = selected.replace(day=selected.day - 1) if selected.day > 1 else None
    next_day = selected.replace(day=selected.day + 1) if selected.day < cal_days else None

    nav = []
    if prev_day:
        nav.append(InlineKeyboardButton("◀", callback_data=f"day_{prev_day.year}_{prev_day.month}_{prev_day.day}"))
    nav.append(InlineKeyboardButton("📆 Календарь", callback_data=f"cal_nav_{year}_{month}"))
    if next_day:
        nav.append(InlineKeyboardButton("▶", callback_data=f"day_{next_day.year}_{next_day.month}_{next_day.day}"))

    kb = InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 Меню", callback_data="main_menu")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


async def view_month_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    today = date.today()
    records = db.get_records_by_month(update.effective_user.id, today.year, today.month)
    text = format_month_report(records, today.year, today.month)
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=make_month_nav_keyboard(today.year, today.month)
    )


async def month_nav_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month = query.data.split("_")
    year, month = int(year), int(month)
    records = db.get_records_by_month(update.effective_user.id, year, month)
    text = format_month_report(records, year, month)
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=make_month_nav_keyboard(year, month)
    )


async def stats_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    stats = db.get_stats(user_id)

    text = "📈 *Общая статистика*\n\n"
    text += f"📅 Всего дней с записями: *{stats['days']}*\n"
    text += f"📝 Всего записей: *{stats['total_records']}*\n\n"
    text += f"💰 Все доходы:  *{stats['total_income']:,.0f} ₽*\n"
    text += f"💸 Все расходы: *{stats['total_expense']:,.0f} ₽*\n"
    balance = stats["total_income"] - stats["total_expense"]
    sign = "+" if balance >= 0 else ""
    text += f"💼 Общий баланс: *{sign}{balance:,.0f} ₽*\n\n"

    if stats["top_expenses"]:
        text += "🔝 *Топ расходов:*\n"
        for row in stats["top_expenses"]:
            desc = row["description"] or "без описания"
            text += f"  • {desc}: *{row['total']:,.0f} ₽*\n"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_keyboard())


async def ignore_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "❌ Отменено.", reply_markup=main_menu_keyboard()
    )
    return CHOOSING_ACTION


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Установи переменную окружения BOT_TOKEN")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ACTION: [
                CallbackQueryHandler(main_menu_cb,      pattern="^main_menu$"),
                CallbackQueryHandler(add_income_cb,     pattern="^add_income$"),
                CallbackQueryHandler(add_expense_cb,    pattern="^add_expense$"),
                CallbackQueryHandler(view_today_cb,     pattern="^view_today$"),
                CallbackQueryHandler(pick_date_cb,      pattern="^pick_date$"),
                CallbackQueryHandler(calendar_nav_cb,   pattern="^cal_nav_"),
                CallbackQueryHandler(day_selected_cb,   pattern="^day_"),
                CallbackQueryHandler(view_month_cb,     pattern="^view_month$"),
                CallbackQueryHandler(month_nav_cb,      pattern="^month_"),
                CallbackQueryHandler(stats_cb,          pattern="^stats$"),
                CallbackQueryHandler(ignore_cb,         pattern="^ignore$"),
            ],
            ENTERING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
            ],
            ENTERING_DESCRIPTION: [
                CommandHandler("skip", skip_description),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_description),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
