import logging
import os
import re
import calendar
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

# .env (стандарт), затем fin.env как legacy-фолбэк для старых установок
load_dotenv()
load_dotenv("fin.env")

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes, ExtBot
)

from database import Database
from i18n import TRANSLATIONS, CURRENCIES, LANG_NAMES, t
from premium_emoji import render, custom_id_for, button_parts


# ─── PREMIUM EMOJI BOT ───────────────────────────────────────────────────────

class PremiumBot(ExtBot):
    """ExtBot that rewrites every outgoing message to HTML + premium emoji.

    Every reply_text / edit_text / edit_message_text call ultimately funnels
    through Bot.send_message / Bot.edit_message_text, so overriding just these
    two methods upgrades the entire bot with no changes at the call sites.
    The incoming parse_mode ("Markdown") is intentionally overridden: render()
    converts the bot's lightweight Markdown to HTML and injects <tg-emoji> tags.
    """

    @staticmethod
    def _upgrade(kwargs: dict) -> None:
        text = kwargs.get("text")
        if isinstance(text, str):
            kwargs["text"] = render(text)
            kwargs["parse_mode"] = "HTML"

    async def send_message(self, *args, **kwargs):
        self._upgrade(kwargs)
        return await super().send_message(*args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        self._upgrade(kwargs)
        return await super().edit_message_text(*args, **kwargs)


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── STATES ──────────────────────────────────────────────────────────────────

(
    CHOOSING_LANG,
    CHOOSING_CURRENCY,
    CHOOSING_ACTION,
    ENTERING_AMOUNT,
    ENTERING_DESCRIPTION,
    SETTINGS_MENU,
    CHOOSING_CATEGORY,
    ENTERING_CATEGORY,
    PROFILE_MENU,
    ADDING_CATEGORY,
    CHOOSING_TX_CURRENCY,
) = range(11)

db = Database()

# ─── USER HELPERS ─────────────────────────────────────────────────────────────

def get_user_prefs(user_id: int) -> tuple[str, str, str]:
    """Returns (lang, currency_code, currency_symbol)."""
    user = db.get_user(user_id)
    lang     = (user or {}).get("lang", "ru")
    currency = (user or {}).get("currency", "RUB")
    symbol   = CURRENCIES.get(currency, ("?", "", ""))[0]
    return lang, currency, symbol


def cur_symbol(code: str) -> str:
    return CURRENCIES.get(code, ("?", "", ""))[0]


def totals_block(income_by_cur: dict, expense_by_cur: dict, lang: str,
                 keys: tuple, default_code: str, joiner: str = "\n",
                 block_sep: str | None = None) -> str:
    """Render income/expense/balance lines per currency.
    keys = (income_key, expense_key, balance_key).
    block_sep separates per-currency blocks (defaults to joiner)."""
    codes = list(dict.fromkeys(list(income_by_cur) + list(expense_by_cur))) or [default_code]
    blocks = []
    for code in codes:
        sym = cur_symbol(code)
        inc = income_by_cur.get(code, 0)
        exp = expense_by_cur.get(code, 0)
        bal = inc - exp
        sign = "+" if bal >= 0 else "-"
        blocks.append(joiner.join([
            t(lang, keys[0], amount=fmt(inc), symbol=sym),
            t(lang, keys[1], amount=fmt(exp), symbol=sym),
            t(lang, keys[2], sign=sign, amount=fmt(abs(bal)), symbol=sym),
        ]).rstrip("\n"))
    sep = block_sep if block_sep is not None else joiner
    return sep.join(blocks)


def tx_currency_keyboard(lang: str, default_currency: str) -> InlineKeyboardMarkup:
    """Picker shown when starting a record: keep-default + full currency grid."""
    info  = CURRENCIES.get(default_currency, ("", "", ""))
    label = f"{info[1]} {default_currency} {info[0]}".strip()
    rows  = [[InlineKeyboardButton(
        t(lang, "btn_keep_currency", currency=label), callback_data="txcur_keep")]]
    items = list(CURRENCIES.items())
    for i in range(0, len(items), 3):
        row = []
        for code, (symbol, flag, clabel) in items[i:i+3]:
            row.append(_btn(f"{flag} {code}", callback_data=f"txcur_{code}"))
        rows.append(row)
    rows.append([_btn(t(lang, "btn_cancel"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


# ─── CATEGORIES ─────────────────────────────────────────────────────────

# Predefined expense categories: (key, emoji, {lang: label}). The key is what
# gets stored in the DB so reports stay correct after a language switch.
CATEGORIES = [
    ("food",          "🍔", {"ru": "Еда",         "uk": "Їжа",         "en": "Food"}),
    ("groceries",     "🛒", {"ru": "Продукты",    "uk": "Продукти",    "en": "Groceries"}),
    ("transport",     "🚗", {"ru": "Транспорт",   "uk": "Транспорт",   "en": "Transport"}),
    ("housing",       "🏠", {"ru": "Жильё",       "uk": "Житло",       "en": "Housing"}),
    ("bills",         "💡", {"ru": "Счета",       "uk": "Рахунки",     "en": "Bills"}),
    ("entertainment", "🎮", {"ru": "Развлечения", "uk": "Розваги",     "en": "Entertainment"}),
    ("clothing",      "👕", {"ru": "Одежда",      "uk": "Одяг",        "en": "Clothing"}),
    ("health",        "💊", {"ru": "Здоровье",    "uk": "Здоров'я",    "en": "Health"}),
    ("education",     "📚", {"ru": "Образование", "uk": "Освіта",      "en": "Education"}),
    ("gifts",         "🎁", {"ru": "Подарки",     "uk": "Подарунки",   "en": "Gifts"}),
    ("travel",        "✈️", {"ru": "Путешествия", "uk": "Подорожі",    "en": "Travel"}),
    ("other",         "🐾", {"ru": "Другое",      "uk": "Інше",        "en": "Other"}),
]
CATEGORIES_BY_KEY = {c[0]: c for c in CATEGORIES}


def category_label(value: str, lang: str) -> str:
    """Display label: predefined keys get translated, custom names shown as-is."""
    c = CATEGORIES_BY_KEY.get(value)
    if c:
        return f"{c[1]} {c[2].get(lang, c[2]['en'])}"
    return value


# Обратный поиск: любое локализованное название (на любом языке) → канонический ключ.
# Исправляет старые записи, где хранилось переведённое название, чтобы одна и та же
# категория (напр. «Развлечения»/«Розваги») сливалась, а не дублировалась по языкам.
LABEL_TO_KEY = {}
for _k, _e, _names in CATEGORIES:
    LABEL_TO_KEY[_k.lower()] = _k
    for _lbl in _names.values():
        LABEL_TO_KEY[_lbl.lower()] = _k


def normalize_category(value: str) -> str:
    """Map a stored category to its canonical key when it matches a predefined
    category in any language; otherwise return it unchanged (true custom).

    Handles legacy rows that stored a localized label, optionally with the
    category's emoji prefix (e.g. "🎮 Розваги"), so the same category never
    splits into duplicates across languages or formats."""
    if not value:
        return ""
    v = value.strip().lower()
    if v in LABEL_TO_KEY:
        return LABEL_TO_KEY[v]
    # Legacy: strip a leading emoji / symbols / punctuation and retry on the text.
    stripped = re.sub(r"^[^0-9A-Za-z\u0400-\u04FF]+", "", v).strip()
    if stripped and stripped in LABEL_TO_KEY:
        return LABEL_TO_KEY[stripped]
    return value


# ─── KEYBOARDS ───────────────────────────────────────────────────────────────

LANG_OPTIONS = [("ru", "🇷🇺 Русский"), ("uk", "🇺🇦 Українська"), ("en", "🇬🇧 English")]


def detect_lang(code: str | None) -> str | None:
    """Map a Telegram language_code (e.g. 'ru', 'uk', 'en-US') to a supported lang."""
    if not code:
        return None
    code = code.lower()
    for supported in ("ru", "uk", "en"):
        if code.startswith(supported):
            return supported
    return None


def lang_keyboard(suggested: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for code, label in LANG_OPTIONS:
        text = f"✅ {label}" if code == suggested else label
        rows.append([InlineKeyboardButton(text, callback_data=f"lang_{code}")])
    return InlineKeyboardMarkup(rows)


def currency_keyboard() -> InlineKeyboardMarkup:
    """Build currency grid: 2 per row."""
    rows = []
    items = list(CURRENCIES.items())
    for i in range(0, len(items), 2):
        row = []
        for code, (symbol, flag, label) in items[i:i+2]:
            row.append(_btn(
                f"{flag} {code} {symbol}",
                callback_data=f"cur_{code}"
            ))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _btn(label: str, **kwargs) -> InlineKeyboardButton:
    """Inline button that shows a leading premium emoji as the button icon."""
    icon, text = button_parts(label)
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text, **kwargs)


def _kbtn(label: str, **kwargs) -> KeyboardButton:
    """Reply-keyboard button with a leading premium emoji as the button icon."""
    icon, text = button_parts(label)
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    return KeyboardButton(text, **kwargs)


def main_menu_keyboard(lang: str, currency: str = "") -> InlineKeyboardMarkup:
    rows = [
        [_btn(t(lang, "btn_income"),    callback_data="add_income",  style="success"),
         _btn(t(lang, "btn_expense"),   callback_data="add_expense", style="danger")],
        [_btn(t(lang, "btn_today"),     callback_data="view_today",  style="primary"),
         _btn(t(lang, "btn_pick_date"), callback_data="pick_date",   style="primary")],
        [_btn(t(lang, "btn_month"),     callback_data="view_month",  style="primary"),
         _btn(t(lang, "btn_stats"),     callback_data="stats",       style="primary")],
    ]
    last_row = []
    last_row.append(_btn(t(lang, "btn_rates"), callback_data="rates", style="primary"))
    last_row.append(_btn(t(lang, "btn_settings"), callback_data="settings"))
    rows.append(last_row)
    return InlineKeyboardMarkup(rows)


def back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _btn(t(lang, "btn_back"), callback_data="main_menu")
    ]])


def category_keyboard(lang: str, user_id: int) -> InlineKeyboardMarkup:
    """Expense category picker: predefined + user's custom + create-new + back."""
    hidden = db.get_hidden_categories(user_id)
    rows, row = [], []
    for key, emoji, names in CATEGORIES:
        if key in hidden:
            continue
        name = names.get(lang, names['en'])
        cem  = custom_id_for(emoji)
        if cem:
            # Bot API 9.4: show the premium custom emoji as the button icon.
            row.append(InlineKeyboardButton(
                name, callback_data=f"pcat_{key}", icon_custom_emoji_id=cem
            ))
        else:
            row.append(InlineKeyboardButton(
                f"{emoji} {name}", callback_data=f"pcat_{key}"
            ))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)

    crow = []
    for c in db.get_custom_categories(user_id):
        crow.append(InlineKeyboardButton(c["name"], callback_data=f"ccat_{c['id']}"))
        if len(crow) == 2:
            rows.append(crow); crow = []
    if crow:
        rows.append(crow)

    rows.append([_btn(t(lang, "btn_custom_category"), callback_data="newcat")])
    rows.append([_btn(t(lang, "btn_back"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def reply_keyboard(lang: str, currency: str = "") -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard — always visible under the text input."""
    rows = [
        [_kbtn(t(lang, "btn_income"),    style="success"),
         _kbtn(t(lang, "btn_expense"),   style="danger")],
        [_kbtn(t(lang, "btn_today"),     style="primary"),
         _kbtn(t(lang, "btn_pick_date"), style="primary")],
        [_kbtn(t(lang, "btn_month"),     style="primary"),
         _kbtn(t(lang, "btn_stats"),     style="primary")],
    ]
    last_row = []
    last_row.append(_kbtn(t(lang, "btn_rates"), style="primary"))
    last_row.append(_kbtn(t(lang, "btn_settings")))
    rows.append(last_row)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


# Map reply button text → callback_data equivalent (built dynamically per lang)
def _reply_button_map(lang: str) -> dict[str, str]:
    pairs = [
        ("btn_income", "add_income"), ("btn_expense", "add_expense"),
        ("btn_today", "view_today"), ("btn_pick_date", "pick_date"),
        ("btn_month", "view_month"), ("btn_stats", "stats"),
        ("btn_rates", "rates"), ("btn_settings", "settings"),
    ]
    out: dict[str, str] = {}
    for key, cb in pairs:
        label = t(lang, key)
        _, text = button_parts(label)
        out[label] = cb
        out[text] = cb
    return out




def make_calendar_keyboard(year: int, month: int, lang: str) -> InlineKeyboardMarkup:
    months   = t(lang, "months")
    weekdays = t(lang, "weekdays")
    month_name = months[month - 1]

    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1,  year + 1)

    buttons = [[
        InlineKeyboardButton("◀", callback_data=f"cal_nav_{prev_y}_{prev_m}"),
        InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore"),
        InlineKeyboardButton("▶", callback_data=f"cal_nav_{next_y}_{next_m}"),
    ]]
    buttons.append([InlineKeyboardButton(d, callback_data="ignore") for d in weekdays])

    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"day_{year}_{month}_{day}"))
        buttons.append(row)

    buttons.append([_btn(t(lang, "btn_back"), callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def make_month_nav_keyboard(year: int, month: int, lang: str) -> InlineKeyboardMarkup:
    months     = t(lang, "months")
    month_name = months[month - 1]
    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1,  year + 1)

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("◀", callback_data=f"month_{prev_y}_{prev_m}"),
            InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore"),
            InlineKeyboardButton("▶", callback_data=f"month_{next_y}_{next_m}"),
        ],
        [_btn(t(lang, "btn_back"), callback_data="main_menu")],
    ])


# ──��� REPORT FORMATTERS ───────────────────────────────────────────────────────

def fmt(amount: float) -> str:
    return f"{amount:,.0f}"


# Часовой пояс для отображения времени записей (created_at хранится в UTC).
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "4"))


def fmt_date(value: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY (best effort)."""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return value or ""


def fmt_time(value: str) -> str:
    """created_at (UTC 'YYYY-MM-DD HH:MM:SS') -> local HH:MM."""
    if not value:
        return ""
    try:
        dt = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S") + timedelta(hours=TZ_OFFSET_HOURS)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


# Разделитель: премиум-минус ➖ (маппится в premium_emoji.EMOJI_IDS)
DIVIDER = "➖➖➖➖➖➖➖➖"
SEP = f"\n{DIVIDER}\n"  # разделитель между секциями


def join_sections(parts) -> str:
    """Join non-empty sections with the divider, trimming trailing newlines so
    every section is separated by exactly one divider line."""
    return SEP.join(p.rstrip("\n") for p in parts if p)


def _format_category_block(order, percat, lang) -> str:
    """Render a 'spending by category' block from ordered keys + per-currency totals."""
    section = t(lang, "profile_spending_header")
    for c in order:
        label = category_label(c, lang) if c else t(lang, "profile_no_category")
        parts = [f"*{fmt(v[0])} {cur_symbol(cur)}*" for cur, v in percat[c].items()]
        cnt   = sum(v[1] for v in percat[c].values())
        section += f"  • {label}: {' · '.join(parts)} · {cnt}\n"
    return section


def expense_category_block(records, lang, default_currency) -> str:
    """Build a spending-by-category block from a list of records (expenses only)."""
    order, percat = [], {}
    for r in records:
        if r.get("type") != "expense":
            continue
        c   = normalize_category(r.get("category") or "")
        cur = r.get("currency") or default_currency
        if c not in percat:
            percat[c] = {}
            order.append(c)
        slot = percat[c].setdefault(cur, [0, 0])
        slot[0] += r["amount"]
        slot[1] += 1
    if not order:
        return ""
    return _format_category_block(order, percat, lang)


def format_day_report(records, target_date: date, lang: str, default_currency: str) -> str:
    weekdays  = t(lang, "weekdays")
    day_str   = target_date.strftime("%d.%m.%Y")
    weekday   = weekdays[target_date.weekday()]

    if not records:
        return f"📋 *{day_str} ({weekday})*\n\n{t(lang, 'no_records')}"

    income_lines, expense_lines = [], []
    income_by_cur, expense_by_cur = {}, {}

    for r in records:
        code  = r.get("currency") or default_currency
        sym   = cur_symbol(code)
        emoji = "💚" if r["type"] == "income" else "🔴"
        line  = f"{emoji} {fmt(r['amount'])} {sym}"
        if r["description"]:
            line += f" — {r['description']}"
        if r["type"] == "income":
            income_lines.append(line)
            income_by_cur[code] = income_by_cur.get(code, 0) + r["amount"]
        else:
            expense_lines.append(line)
            expense_by_cur[code] = expense_by_cur.get(code, 0) + r["amount"]

    sections = [f"📋 *{day_str} ({weekday})*"]
    if income_lines:
        sections.append(t(lang, "incomes_header") + "\n".join(income_lines))
    if expense_lines:
        sections.append(t(lang, "expenses_header") + "\n".join(expense_lines))
    sections.append(expense_category_block(records, lang, default_currency))
    sections.append(totals_block(income_by_cur, expense_by_cur, lang,
                                 ("total_income", "total_expense", "day_balance"),
                                 default_currency, block_sep=SEP))
    return join_sections(sections)


def format_month_report(records, year: int, month: int, lang: str, default_currency: str) -> str:
    months     = t(lang, "months")
    weekdays   = t(lang, "weekdays")
    month_name = months[month - 1]
    text       = f"📊 *{month_name} {year}*\n\n"

    if not records:
        return text + t(lang, "no_records")

    by_day: dict = {}
    for r in records:
        code = r.get("currency") or default_currency
        d = r["date"]
        by_day.setdefault(d, {}).setdefault(code, {"income": 0, "expense": 0})
        by_day[d][code][r["type"]] += r["amount"]

    income_by_cur, expense_by_cur = {}, {}
    day_lines = []
    for d in sorted(by_day):
        dt = datetime.strptime(d, "%Y-%m-%d")
        wd = weekdays[dt.weekday()]
        for code, vals in by_day[d].items():
            inc = vals["income"]; exp = vals["expense"]
            income_by_cur[code]  = income_by_cur.get(code, 0) + inc
            expense_by_cur[code] = expense_by_cur.get(code, 0) + exp
            bal  = inc - exp
            sign = "+" if bal >= 0 else "-"
            sym  = cur_symbol(code)
            row  = f"`{dt.strftime('%d.%m')}` ({wd})  "
            if inc: row += f"💚 {fmt(inc)}"
            if exp: row += f"  🔴 {fmt(exp)}"
            row += f"  ��{sign}{fmt(abs(bal))} {sym}"
            day_lines.append(row)

    sections = [
        f"📊 *{month_name} {year}*",
        "\n".join(day_lines),
        totals_block(income_by_cur, expense_by_cur, lang,
                     ("month_income", "month_expense", "month_balance"),
                     default_currency, block_sep=SEP),
    ]
    return join_sections(sections)


# ─── EXCHANGE RATES (fawazahmed0/exchange-api — бесплатно, без ключа, 200+ валют) ───

RATE_TARGETS = ["GEL","EUR","UAH","RUB","KZT","USD","GBP","TRY","PLN","CZK","BYN","AZN","AMD","CNY","AED","CHF","JPY","SEK"]
UNSUPPORTED_BASES: set = set()  # все валюты поддерживаются

async def fetch_rates(base: str) -> dict | None:
    """Fetch latest rates from fawazahmed0/exchange-api (CDN, no key, no limits).
    Returns dict with keys 'rates' and 'date', or None on error.
    Primary: cdn.jsdelivr.net, fallback: currency-api.pages.dev
    """
    code = base.lower()
    urls = [
        f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{code}.json",
        f"https://latest.currency-api.pages.dev/v1/currencies/{code}.json",
    ]
    targets = {c.lower() for c in RATE_TARGETS if c != base}
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            all_rates = data.get(code, {})
            date_str  = data.get("date", "?")
            rates = {
                k.upper(): v for k, v in all_rates.items()
                if k in targets and isinstance(v, (int, float))
            }
            return {"rates": rates, "date": date_str}
        except Exception as e:
            logger.warning(f"fetch_rates [{url}]: {e}")
    return None


# ─── ONBOARDING ──────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.full_name)

    # If user already has prefs — go straight to menu
    u = db.get_user(user.id)
    if u and u.get("lang") and u.get("currency"):
        lang, currency, symbol = get_user_prefs(user.id)
        await update.message.reply_text(
            t(lang, "welcome", name=user.first_name),
            parse_mode="Markdown",
            reply_markup=reply_keyboard(lang, currency)
        )
        await update.message.reply_text(
            t(lang, "main_menu_text"),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(lang, currency)
        )
        return CHOOSING_ACTION

    # First time — pick language (предлагаем определённый из Telegram)
    suggested = detect_lang(getattr(user, "language_code", None))
    await update.message.reply_text(
        TRANSLATIONS["ru"]["choose_lang"],
        reply_markup=lang_keyboard(suggested)
    )
    return CHOOSING_LANG


async def lang_chosen_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split("_")[1]          # lang_ru → ru
    user_id = update.effective_user.id

    db.ensure_user(user_id, update.effective_user.full_name)
    db.set_user_lang(user_id, lang)
    ctx.user_data["lang"] = lang

    # Смена языка из настроек — не переспрашиваем валюту
    if ctx.user_data.pop("changing", None) == "lang":
        currency = db.get_user(user_id).get("currency") or "RUB"
        await query.edit_message_text(t(lang, "changes_saved"), parse_mode="Markdown")
        await query.message.reply_text(
            t(lang, "main_menu_text"), parse_mode="Markdown",
            reply_markup=reply_keyboard(lang, currency))
        await query.message.reply_text(
            t(lang, "main_menu_text"), parse_mode="Markdown",
            reply_markup=main_menu_keyboard(lang, currency))
        return CHOOSING_ACTION

    await query.edit_message_text(
        t(lang, "choose_currency"),
        reply_markup=currency_keyboard()
    )
    return CHOOSING_CURRENCY


async def currency_chosen_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currency = query.data.split("_")[1]      # cur_GEL → GEL
    user_id  = update.effective_user.id
    lang     = ctx.user_data.get("lang") or db.get_user(user_id).get("lang", "ru")

    db.set_user_currency(user_id, currency)

    # Смена валюты из настроек — короткое подтверждение
    if ctx.user_data.pop("changing", None) == "currency":
        await query.edit_message_text(t(lang, "changes_saved"), parse_mode="Markdown")
        await query.message.reply_text(
            t(lang, "main_menu_text"), parse_mode="Markdown",
            reply_markup=reply_keyboard(lang, currency))
        await query.message.reply_text(
            t(lang, "main_menu_text"), parse_mode="Markdown",
            reply_markup=main_menu_keyboard(lang, currency))
        return CHOOSING_ACTION

    await query.edit_message_text(
        t(lang, "setup_done"),
        parse_mode="Markdown"
    )
    await query.message.reply_text(
        t(lang, "welcome", name=update.effective_user.first_name),
        parse_mode="Markdown",
        reply_markup=reply_keyboard(lang, currency)
    )
    await query.message.reply_text(
        t(lang, "main_menu_text"),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(lang, currency)
    )
    return CHOOSING_ACTION


# ─── MAIN MENU ─────���─────────────────────────────────────────────────────────

async def main_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, currency, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data.clear()
    await query.edit_message_text(
        t(lang, "main_menu_text"),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(lang, currency)
    )
    return CHOOSING_ACTION


# ─── ADD INCOME / EXPENSE ────────────────────────────────────────────────────

async def add_income_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, currency, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data["type"] = "income"
    ctx.user_data["date"] = date.today().isoformat()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.message.reply_text(
        t(lang, "tx_currency_q"),
        parse_mode="Markdown",
        reply_markup=tx_currency_keyboard(lang, currency)
    )
    return CHOOSING_TX_CURRENCY


async def add_expense_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, currency, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data["type"] = "expense"
    ctx.user_data["date"] = date.today().isoformat()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.message.reply_text(
        t(lang, "tx_currency_q"),
        parse_mode="Markdown",
        reply_markup=tx_currency_keyboard(lang, currency)
    )
    return CHOOSING_TX_CURRENCY


async def tx_currency_chosen_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, default_currency, _ = get_user_prefs(update.effective_user.id)
    data = query.data
    chosen = default_currency if data == "txcur_keep" else data.split("_", 1)[1]
    ctx.user_data["tx_currency"] = chosen
    ttype = ctx.user_data.get("type", "expense")
    key = "new_income" if ttype == "income" else "new_expense"
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.message.reply_text(
        t(lang, key), parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )
    return ENTERING_AMOUNT


async def enter_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang, default_currency, _ = get_user_prefs(update.effective_user.id)
    symbol = cur_symbol(ctx.user_data.get("tx_currency") or default_currency)
    text = update.message.text.replace(",", ".").replace(" ", "")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(t(lang, "bad_amount"), parse_mode="Markdown")
        return ENTERING_AMOUNT

    ctx.user_data["amount"] = amount
    if ctx.user_data.get("type") == "expense":
        await update.message.reply_text(
            t(lang, "pick_category", amount=fmt(amount), symbol=symbol),
            parse_mode="Markdown",
            reply_markup=category_keyboard(lang, update.effective_user.id)
        )
        return CHOOSING_CATEGORY
    await update.message.reply_text(
        t(lang, "enter_desc", amount=fmt(amount), symbol=symbol),
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


async def category_chosen_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, default_currency, _ = get_user_prefs(user_id)
    symbol = cur_symbol(ctx.user_data.get("tx_currency") or default_currency)
    data = query.data

    if data == "newcat":
        await query.edit_message_text(t(lang, "enter_category_name"), parse_mode="Markdown")
        return ENTERING_CATEGORY

    if data.startswith("pcat_"):
        ctx.user_data["category"] = data[len("pcat_"):]
    elif data.startswith("ccat_"):
        name = db.get_custom_category_name(user_id, int(data[len("ccat_"):]))
        ctx.user_data["category"] = name or ""

    amount = ctx.user_data.get("amount", 0)
    await query.edit_message_text(
        t(lang, "enter_desc", amount=fmt(amount), symbol=symbol),
        parse_mode="Markdown"
    )
    return ENTERING_DESCRIPTION


async def enter_category_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, default_currency, _ = get_user_prefs(user_id)
    symbol = cur_symbol(ctx.user_data.get("tx_currency") or default_currency)
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text(t(lang, "enter_category_name"), parse_mode="Markdown")
        return ENTERING_CATEGORY
    db.add_custom_category(user_id, name)
    ctx.user_data["category"] = name
    amount = ctx.user_data.get("amount", 0)
    await update.message.reply_text(
        t(lang, "enter_desc", amount=fmt(amount), symbol=symbol),
        parse_mode="Markdown"
    )
    return ENTERING_DESCRIPTION


async def _save_record(update: Update, ctx: ContextTypes.DEFAULT_TYPE, description: str):
    user_id  = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    rec_currency = ctx.user_data.get("tx_currency") or currency
    symbol   = cur_symbol(rec_currency)
    ttype    = ctx.user_data["type"]
    amount   = ctx.user_data["amount"]
    rec_date = ctx.user_data.get("date", date.today().isoformat())

    category = ctx.user_data.get("category", "")
    db.add_record(user_id, ttype, amount, description, rec_date, category, rec_currency)

    key      = "saved_income" if ttype == "income" else "saved_expense"
    desc_str = (t(lang, "desc_label") + description) if description else ""
    cat_str  = (t(lang, "cat_label") + category_label(category, lang)) if category else ""
    await update.message.reply_text(
        t(lang, key, amount=fmt(amount), symbol=symbol, desc=desc_str, cat=cat_str, date=rec_date),
        parse_mode="Markdown",
        reply_markup=reply_keyboard(lang, currency)
    )
    await update.message.reply_text(
        t(lang, "main_menu_text"),
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(lang, currency)
    )
    ctx.user_data.clear()


# ─── REPORTS ─────────────────────────────────────────────────────────────────

def build_profile_text(user_id: int, lang: str, default_currency: str) -> str:
    """Profile screen: expense totals grouped by category (per currency)."""
    totals = db.get_category_totals(user_id, default_currency)
    rows = [r for r in totals if r["total"]]
    title = t(lang, "profile_text")
    if not rows:
        return join_sections([title, t(lang, "profile_no_spending")])

    cats: dict = {}
    order = []
    grand: dict = {}
    for r in rows:
        c = normalize_category(r["category"])
        if c not in cats:
            cats[c] = {}
            order.append(c)
        slot = cats[c].setdefault(r["cur"], [0, 0])
        slot[0] += r["total"]
        slot[1] += r["cnt"]
        grand[r["cur"]] = grand.get(r["cur"], 0) + r["total"]

    cat_section = t(lang, "profile_spending_header")
    for c in order:
        label = category_label(c, lang) if c else t(lang, "profile_no_category")
        parts = [f"*{fmt(v[0])} {cur_symbol(cur)}*" for cur, v in cats[c].items()]
        cnt   = sum(v[1] for v in cats[c].values())
        cat_section += f"  • {label}: {' · '.join(parts)} · {cnt}\n"

    grand_parts = [f"*{fmt(v)} {cur_symbol(cur)}*" for cur, v in grand.items()]
    total_section = t(lang, "profile_total_label") + " · ".join(grand_parts)
    return join_sections([title, cat_section, total_section])


def profile_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_btn(t(lang, "btn_add_category"), callback_data="profile_addcat")],
        [_btn(t(lang, "btn_manage_categories"), callback_data="catmgr")],
        [_btn(t(lang, "btn_back"), callback_data="main_menu")],
    ])


def manage_categories_keyboard(user_id: int, lang: str) -> InlineKeyboardMarkup:
    """Management screen: hide system cats, delete custom cats, restore hidden, add."""
    hidden = db.get_hidden_categories(user_id)
    rows = []
    for key, emoji, names in CATEGORIES:
        if key in hidden:
            continue
        cem = custom_id_for(emoji)
        if cem:
            rows.append([InlineKeyboardButton(
                f"❌ {names.get(lang, names['en'])}", callback_data=f"cathide_{key}",
                icon_custom_emoji_id=cem)])
        else:
            rows.append([InlineKeyboardButton(
                f"❌ {emoji} {names.get(lang, names['en'])}", callback_data=f"cathide_{key}")])
    for c in db.get_custom_categories(user_id):
        rows.append([InlineKeyboardButton(
            f"❌ {c['name']}", callback_data=f"catdel_{c['id']}")])
    for key, emoji, names in CATEGORIES:
        if key in hidden:
            cem = custom_id_for(emoji)
            if cem:
                rows.append([InlineKeyboardButton(
                    f"↩️ {names.get(lang, names['en'])}", callback_data=f"catshow_{key}",
                    icon_custom_emoji_id=cem)])
            else:
                rows.append([InlineKeyboardButton(
                    f"↩️ {emoji} {names.get(lang, names['en'])}", callback_data=f"catshow_{key}")])
    rows.append([_btn(t(lang, "btn_add_category"), callback_data="profile_addcat")])
    rows.append([_btn(t(lang, "btn_back"), callback_data="settings")])
    return InlineKeyboardMarkup(rows)


async def profile_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    await query.edit_message_text(
        build_profile_text(user_id, lang, currency),
        parse_mode="Markdown", reply_markup=profile_keyboard(lang)
    )
    return PROFILE_MENU


async def _reply_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    await update.message.reply_text(
        build_profile_text(user_id, lang, currency),
        parse_mode="Markdown", reply_markup=profile_keyboard(lang)
    )
    return PROFILE_MENU


async def profile_add_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, _, _ = get_user_prefs(update.effective_user.id)
    await query.edit_message_text(t(lang, "enter_category_name"), parse_mode="Markdown")
    return ADDING_CATEGORY


async def add_category_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, _, symbol = get_user_prefs(user_id)
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text(t(lang, "enter_category_name"), parse_mode="Markdown")
        return ADDING_CATEGORY
    db.add_custom_category(user_id, name)
    await update.message.reply_text(t(lang, "category_added", name=name), parse_mode="Markdown")
    await update.message.reply_text(
        t(lang, "manage_cat_title"),
        parse_mode="Markdown", reply_markup=manage_categories_keyboard(user_id, lang)
    )
    return PROFILE_MENU


async def _show_manage_categories(query, user_id: int, lang: str):
    await query.edit_message_text(
        t(lang, "manage_cat_title"),
        parse_mode="Markdown",
        reply_markup=manage_categories_keyboard(user_id, lang),
    )


async def manage_categories_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, _, _ = get_user_prefs(user_id)
    await _show_manage_categories(query, user_id, lang)
    return PROFILE_MENU


async def category_hide_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    lang, _, _ = get_user_prefs(user_id)
    db.hide_category(user_id, query.data[len("cathide_"):])
    await query.answer(t(lang, "category_removed"))
    await _show_manage_categories(query, user_id, lang)
    return PROFILE_MENU


async def category_del_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    lang, _, _ = get_user_prefs(user_id)
    try:
        db.delete_custom_category(user_id, int(query.data[len("catdel_"):]))
    except (ValueError, TypeError):
        pass
    await query.answer(t(lang, "category_removed"))
    await _show_manage_categories(query, user_id, lang)
    return PROFILE_MENU


async def category_show_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    lang, _, _ = get_user_prefs(user_id)
    db.unhide_category(user_id, query.data[len("catshow_"):])
    await query.answer(t(lang, "category_restored"))
    await _show_manage_categories(query, user_id, lang)
    return PROFILE_MENU


async def view_today_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    today   = date.today()
    records = db.get_records_by_date(user_id, today.isoformat())
    text    = format_day_report(records, today, lang, currency)

    yest_cb = (f"day_{today.year}_{today.month}_{today.day - 1}"
               if today.day > 1 else "ignore")
    kb = InlineKeyboardMarkup([
        [_btn(t(lang, "btn_yesterday"), callback_data=yest_cb),
         _btn(t(lang, "btn_back"),      callback_data="main_menu")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


async def pick_date_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, _, _ = get_user_prefs(update.effective_user.id)
    today = date.today()
    await query.edit_message_text(
        t(lang, "pick_date"),
        reply_markup=make_calendar_keyboard(today.year, today.month, lang)
    )


async def calendar_nav_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, _, _ = get_user_prefs(update.effective_user.id)
    _, _, year, month = query.data.split("_")
    await query.edit_message_text(
        t(lang, "pick_date"),
        reply_markup=make_calendar_keyboard(int(year), int(month), lang)
    )


async def day_selected_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    _, year, month, day = query.data.split("_")
    selected = date(int(year), int(month), int(day))
    records  = db.get_records_by_date(user_id, selected.isoformat())
    text     = format_day_report(records, selected, lang, currency)

    cal_days = calendar.monthrange(int(year), int(month))[1]
    prev_day = selected.replace(day=selected.day - 1) if selected.day > 1   else None
    next_day = selected.replace(day=selected.day + 1) if selected.day < cal_days else None

    nav = []
    if prev_day:
        nav.append(InlineKeyboardButton("◀", callback_data=f"day_{prev_day.year}_{prev_day.month}_{prev_day.day}"))
    nav.append(_btn(t(lang, "btn_calendar"), callback_data=f"cal_nav_{year}_{month}"))
    if next_day:
        nav.append(InlineKeyboardButton("▶", callback_data=f"day_{next_day.year}_{next_day.month}_{next_day.day}"))

    kb = InlineKeyboardMarkup([nav, [_btn(t(lang, "btn_back"), callback_data="main_menu")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


async def view_month_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    today   = date.today()
    records = db.get_records_by_month(user_id, today.year, today.month)
    text    = format_month_report(records, today.year, today.month, lang, currency)
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=make_month_nav_keyboard(today.year, today.month, lang)
    )


async def month_nav_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    _, year, month  = query.data.split("_")
    year, month     = int(year), int(month)
    records = db.get_records_by_month(user_id, year, month)
    text    = format_month_report(records, year, month, lang, currency)
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=make_month_nav_keyboard(year, month, lang)
    )


# ─── STATS ───────────────────────────────────────────────────────────────────

def _stat_categories(user_id: int, default_currency: str):
    """Ordered unique expense categories + per-currency rows. Shared by the
    stats keyboard and the drilldown so button indexes stay consistent."""
    totals = db.get_category_totals(user_id, default_currency)
    order = []
    percat: dict = {}
    for r in totals:
        if not r["total"]:
            continue
        c = normalize_category(r["category"])
        if c not in percat:
            percat[c] = {}
            order.append(c)
        slot = percat[c].setdefault(r["cur"], [0, 0])
        slot[0] += r["total"]
        slot[1] += r["cnt"]
    return order, percat


def build_stats_text(user_id: int, lang: str, default_currency: str) -> str:
    """Top of the stats screen: ONLY overall income / expense / balance."""
    stats = db.get_stats(user_id, default_currency)
    meta = t(lang, "stats_days", n=stats["days"]) + t(lang, "stats_records", n=stats["total_records"])
    totals = totals_block(stats["income_by_cur"], stats["expense_by_cur"], lang,
                          ("stats_income", "stats_expense", "stats_balance"),
                          default_currency, joiner="", block_sep=SEP)
    order, percat = _stat_categories(user_id, default_currency)
    cat_block = _format_category_block(order, percat, lang) if order else ""
    return join_sections([
        t(lang, "stats_header"),
        meta,
        totals,
        cat_block,
        t(lang, "stats_tap_category"),
    ])


def stats_keyboard(user_id: int, lang: str, default_currency: str) -> InlineKeyboardMarkup:
    order, _ = _stat_categories(user_id, default_currency)
    rows = []
    for i, c in enumerate(order):
        ci  = CATEGORIES_BY_KEY.get(c)
        cem = custom_id_for(ci[1]) if ci else ""
        if ci and cem:
            # Bot API 9.4: premium custom emoji icon for predefined categories.
            name = ci[2].get(lang, ci[2]["en"])
            rows.append([InlineKeyboardButton(
                name, callback_data=f"statcat_{i}", icon_custom_emoji_id=cem
            )])
        else:
            label = category_label(c, lang) if c else t(lang, "profile_no_category")
            rows.append([InlineKeyboardButton(label, callback_data=f"statcat_{i}")])
    rows.append([_btn(t(lang, "btn_back"), callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


async def stats_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    await query.edit_message_text(
        build_stats_text(user_id, lang, currency),
        parse_mode="Markdown",
        reply_markup=stats_keyboard(user_id, lang, currency)
    )
    return CHOOSING_ACTION


async def stat_category_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    order, percat = _stat_categories(user_id, currency)
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        idx = -1
    if idx < 0 or idx >= len(order):
        await query.edit_message_text(
            build_stats_text(user_id, lang, currency),
            parse_mode="Markdown",
            reply_markup=stats_keyboard(user_id, lang, currency)
        )
        return CHOOSING_ACTION
    cat = order[idx]
    label = category_label(cat, lang) if cat else t(lang, "profile_no_category")
    # Summary: total per currency + records count
    summary = [t(lang, "stat_cat_title", label=label)]
    total_cnt = 0
    for cur, (total, cnt) in percat[cat].items():
        summary.append(t(lang, "stat_cat_spent", amount=fmt(total), symbol=cur_symbol(cur)))
        total_cnt += cnt
    summary.append(t(lang, "stat_cat_records", n=total_cnt))
    # Concrete records for this category: date, time, amount, description
    recs = [r for r in db.get_expense_records(user_id)
            if normalize_category(r.get("category", "")) == cat]
    LIMIT = 50
    detail = [t(lang, "stat_cat_list_header")]
    for r in recs[:LIMIT]:
        rcur = r.get("currency") or currency
        desc = f"  — {r['description']}" if r.get("description") else ""
        detail.append(t(lang, "stat_cat_record_line",
                        date=fmt_date(r.get("date", "")),
                        time=fmt_time(r.get("created_at", "")),
                        amount=fmt(r.get("amount", 0)),
                        symbol=cur_symbol(rcur),
                        desc=desc))
    extra = len(recs) - LIMIT
    if extra > 0:
        detail.append(t(lang, "stat_cat_more", n=extra))
    text = join_sections(["\n".join(summary), "\n".join(detail)])
    kb = InlineKeyboardMarkup([[_btn(t(lang, "btn_stats_back"), callback_data="stats")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return CHOOSING_ACTION


# ─── EXCHANGE RATES ──────────────────────────────────────────────────────────

async def rates_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, symbol = get_user_prefs(user_id)

    # Show "fetching" message
    await query.edit_message_text(t(lang, "rate_fetching"), parse_mode="Markdown")

    data = await fetch_rates(currency)
    if not data:
        await query.edit_message_text(
            t(lang, "rate_error", currency=currency),
            parse_mode="Markdown",
            reply_markup=back_keyboard(lang)
        )
        return

    rates_text = ""
    for code, rate in sorted(data["rates"].items()):
        info = CURRENCIES.get(code)
        flag = info[1] if info else "  "
        sym  = info[0] if info else code
        rates_text += f"{flag} *{code}* {sym}  —  `{rate:,.4f}`\n"

    text = t(lang, "rate_info", base=f"{currency} {symbol}", rates=rates_text, updated=data["date"])
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=back_keyboard(lang)
    )


# ─── SETTINGS ────────────────────────────────────────────────────────────────

async def settings_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    lang, currency, symbol = get_user_prefs(user_id)

    lang_name = LANG_NAMES.get(lang, lang)
    cur_info  = CURRENCIES.get(currency, (symbol, "", currency))
    cur_label = f"{cur_info[1]} {currency} {cur_info[0]}"

    kb = InlineKeyboardMarkup([
        [_btn(t(lang, "settings_lang"),     callback_data="set_lang")],
        [_btn(t(lang, "settings_currency"), callback_data="set_currency")],
        [_btn(t(lang, "btn_manage_categories"), callback_data="catmgr")],
        [_btn(t(lang, "btn_back"),          callback_data="main_menu")],
    ])
    await query.edit_message_text(
        t(lang, "settings_text", lang=lang_name, currency=cur_label),
        parse_mode="Markdown",
        reply_markup=kb
    )
    return SETTINGS_MENU


async def set_lang_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, _, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data["changing"] = "lang"
    await query.edit_message_text(
        t(lang, "choose_lang") if lang else TRANSLATIONS["ru"]["choose_lang"],
        reply_markup=lang_keyboard(lang)
    )
    return CHOOSING_LANG


async def set_currency_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang, _, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data["changing"] = "currency"
    await query.edit_message_text(
        t(lang, "choose_currency"),
        reply_markup=currency_keyboard()
    )
    return CHOOSING_CURRENCY


# ─── MISC ─────────────────────────────────────────────────────────────────────

async def reply_button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle taps on the persistent bottom keyboard by routing to the same
    logic as inline callbacks — no code duplication."""
    user_id = update.effective_user.id
    lang, _, _ = get_user_prefs(user_id)
    text = update.message.text

    action = _reply_button_map(lang).get(text)
    if not action:
        # Unknown text — just show the menu
        _, currency, _ = get_user_prefs(user_id)
        await update.message.reply_text(
            t(lang, "main_menu_text"),
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(lang, currency)
        )
        return CHOOSING_ACTION

    # Dispatch: create a fake-context message with the right action
    # by sending the inline menu and letting user tap it, OR handle directly
    dispatch = {
        "add_income":  _reply_add_income,
        "add_expense": _reply_add_expense,
        "view_today":  _reply_view_today,
        "pick_date":   _reply_pick_date,
        "view_month":  _reply_view_month,
        "stats":       _reply_stats,
        "rates":       _reply_rates,
        "settings":    _reply_settings,
    }
    handler = dispatch.get(action)
    if handler:
        return await handler(update, ctx)
    return CHOOSING_ACTION


# ── Reply-button direct handlers (no callback_query, use message.reply_text) ──

async def _reply_add_income(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang, currency, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data["type"] = "income"
    ctx.user_data["date"] = date.today().isoformat()
    await update.message.reply_text(
        t(lang, "tx_currency_q"), parse_mode="Markdown",
        reply_markup=tx_currency_keyboard(lang, currency)
    )
    return CHOOSING_TX_CURRENCY

async def _reply_add_expense(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang, currency, _ = get_user_prefs(update.effective_user.id)
    ctx.user_data["type"] = "expense"
    ctx.user_data["date"] = date.today().isoformat()
    await update.message.reply_text(
        t(lang, "tx_currency_q"), parse_mode="Markdown",
        reply_markup=tx_currency_keyboard(lang, currency)
    )
    return CHOOSING_TX_CURRENCY

async def _reply_view_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    today   = date.today()
    records = db.get_records_by_date(user_id, today.isoformat())
    text    = format_day_report(records, today, lang, currency)
    yest_cb = (f"day_{today.year}_{today.month}_{today.day - 1}"
               if today.day > 1 else "ignore")
    kb = InlineKeyboardMarkup([
        [_btn(t(lang, "btn_yesterday"), callback_data=yest_cb),
         _btn(t(lang, "btn_back"),      callback_data="main_menu")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    return CHOOSING_ACTION

async def _reply_pick_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang, _, _ = get_user_prefs(update.effective_user.id)
    today = date.today()
    await update.message.reply_text(
        t(lang, "pick_date"),
        reply_markup=make_calendar_keyboard(today.year, today.month, lang)
    )
    return CHOOSING_ACTION

async def _reply_view_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    today   = date.today()
    records = db.get_records_by_month(user_id, today.year, today.month)
    text    = format_month_report(records, today.year, today.month, lang, currency)
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=make_month_nav_keyboard(today.year, today.month, lang)
    )
    return CHOOSING_ACTION

async def _reply_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    await update.message.reply_text(
        build_stats_text(user_id, lang, currency),
        parse_mode="Markdown",
        reply_markup=stats_keyboard(user_id, lang, currency)
    )
    return CHOOSING_ACTION

async def _reply_rates(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, symbol = get_user_prefs(user_id)
    msg = await update.message.reply_text(t(lang, "rate_fetching"), parse_mode="Markdown")
    data = await fetch_rates(currency)
    if not data:
        await msg.edit_text(t(lang, "rate_error", currency=currency), parse_mode="Markdown",
                            reply_markup=back_keyboard(lang))
        return CHOOSING_ACTION
    rates_text = ""
    for code, rate in sorted(data["rates"].items()):
        info = CURRENCIES.get(code)
        flag = info[1] if info else "  "
        sym  = info[0] if info else code
        rates_text += f"{flag} *{code}* {sym}  —  `{rate:,.4f}`\n"
    text = t(lang, "rate_info", base=f"{currency} {symbol}", rates=rates_text, updated=data["date"])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard(lang))
    return CHOOSING_ACTION

async def _reply_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, symbol = get_user_prefs(user_id)
    lang_name = LANG_NAMES.get(lang, lang)
    cur_info  = CURRENCIES.get(currency, (symbol, "", currency))
    cur_label = f"{cur_info[1]} {currency} {cur_info[0]}"
    kb = InlineKeyboardMarkup([
        [_btn(t(lang, "settings_lang"),     callback_data="set_lang")],
        [_btn(t(lang, "settings_currency"), callback_data="set_currency")],
        [_btn(t(lang, "btn_manage_categories"), callback_data="catmgr")],
        [_btn(t(lang, "btn_back"),          callback_data="main_menu")],
    ])
    await update.message.reply_text(
        t(lang, "settings_text", lang=lang_name, currency=cur_label),
        parse_mode="Markdown", reply_markup=kb
    )
    return SETTINGS_MENU


async def ignore_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang, currency, _ = get_user_prefs(user_id)
    ctx.user_data.clear()
    await update.message.reply_text(
        t(lang, "cancelled"),
        reply_markup=main_menu_keyboard(lang, currency)
    )
    return CHOOSING_ACTION


# ─── MAIN ───────────────��────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    """Register bot commands shown when user types '/'."""
    commands = [
        BotCommand("start",   "🏠 Главное меню / Головне меню / Main menu"),
        BotCommand("income",  "➕ Добавить доход / Дохід / Add income"),
        BotCommand("expense", "➖ Добавить расход / Витрата / Add expense"),
        BotCommand("today",   "📅 Отчёт за сегодня / Сьогодні / Today report"),
        BotCommand("month",   "📊 Отчёт за месяц / Місяць / Month report"),
        BotCommand("stats",   "📈 Статистика / Статистика / Stats"),
        BotCommand("rates",   "💱 Курсы валют / Курси / Exchange rates"),
        BotCommand("settings","⚙️ Настройки / Налаштування / Settings"),
        BotCommand("skip",    "⏭ Пропустить описание / Пропустити / Skip description"),
        BotCommand("cancel",  "❌ Отменить действие / Скасувати / Cancel"),
    ]
    await app.bot.set_my_commands(commands)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set BOT_TOKEN in your .env file")

    app = (
        Application.builder()
        .bot(PremiumBot(token))
        .post_init(post_init)
        .build()
    )

    # ── Reply keyboard button filter (matches any of the 3 langs at once) ──
    ALL_REPLY_LABELS = set()
    for lang in ("ru", "uk", "en"):
        ALL_REPLY_LABELS.update(_reply_button_map(lang).keys())
    reply_btn_filter = filters.Regex("^(" + "|".join(map(lambda s: s.replace("+","\\+"), ALL_REPLY_LABELS)) + ")$")

    # ── Command shortcuts (/income, /expense, etc.) ────────────────────────
    async def cmd_income(u, c):   return await _reply_add_income(u, c)
    async def cmd_expense(u, c):  return await _reply_add_expense(u, c)
    async def cmd_today(u, c):    return await _reply_view_today(u, c)
    async def cmd_month(u, c):    return await _reply_view_month(u, c)
    async def cmd_stats(u, c):    return await _reply_stats(u, c)
    async def cmd_rates(u, c):    return await _reply_rates(u, c)
    async def cmd_settings(u, c): return await _reply_settings(u, c)

    # ── Handlers shared across CHOOSING_ACTION and SETTINGS_MENU ──────────
    common_action_handlers = [
        # Inline callbacks
        CallbackQueryHandler(main_menu_cb,    pattern="^main_menu$"),
        CallbackQueryHandler(add_income_cb,   pattern="^add_income$"),
        CallbackQueryHandler(add_expense_cb,  pattern="^add_expense$"),
        CallbackQueryHandler(view_today_cb,   pattern="^view_today$"),
        CallbackQueryHandler(pick_date_cb,    pattern="^pick_date$"),
        CallbackQueryHandler(calendar_nav_cb, pattern="^cal_nav_"),
        CallbackQueryHandler(day_selected_cb, pattern="^day_"),
        CallbackQueryHandler(view_month_cb,   pattern="^view_month$"),
        CallbackQueryHandler(month_nav_cb,    pattern="^month_"),
        CallbackQueryHandler(stats_cb,        pattern="^stats$"),
        CallbackQueryHandler(stat_category_cb, pattern="^statcat_"),
        CallbackQueryHandler(rates_cb,        pattern="^rates$"),
        CallbackQueryHandler(settings_cb,     pattern="^settings$"),
        CallbackQueryHandler(ignore_cb,       pattern="^ignore$"),
        # Reply keyboard taps
        MessageHandler(reply_btn_filter, reply_button_handler),
        # /command shortcuts
        CommandHandler("income",   cmd_income),
        CommandHandler("expense",  cmd_expense),
        CommandHandler("today",    cmd_today),
        CommandHandler("month",    cmd_month),
        CommandHandler("stats",    cmd_stats),
        CommandHandler("rates",    cmd_rates),
        CommandHandler("settings", cmd_settings),
    ]

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_LANG: [
                CallbackQueryHandler(lang_chosen_cb, pattern="^lang_"),
            ],
            CHOOSING_CURRENCY: [
                CallbackQueryHandler(currency_chosen_cb, pattern="^cur_"),
            ],
            CHOOSING_ACTION: common_action_handlers,
            CHOOSING_TX_CURRENCY: [
                CallbackQueryHandler(tx_currency_chosen_cb, pattern="^txcur_"),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
                MessageHandler(reply_btn_filter, reply_button_handler),
            ],
            ENTERING_AMOUNT: [
                # Reply-button taps while in amount state → restart action
                MessageHandler(reply_btn_filter, reply_button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
            ],
            ENTERING_DESCRIPTION: [
                CommandHandler("skip", skip_description),
                # Reply-button taps while in description state → save without desc, then action
                MessageHandler(reply_btn_filter, reply_button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_description),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
            ],
            CHOOSING_CATEGORY: [
                CallbackQueryHandler(category_chosen_cb, pattern="^(pcat_.+|ccat_.+|newcat)$"),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
                MessageHandler(reply_btn_filter, reply_button_handler),
            ],
            ENTERING_CATEGORY: [
                MessageHandler(reply_btn_filter, reply_button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_category_name),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
            ],
            PROFILE_MENU: [
                CallbackQueryHandler(profile_add_cat_cb, pattern="^profile_addcat$"),
                CallbackQueryHandler(manage_categories_cb, pattern="^catmgr$"),
                CallbackQueryHandler(category_hide_cb, pattern="^cathide_"),
                CallbackQueryHandler(category_del_cb, pattern="^catdel_"),
                CallbackQueryHandler(category_show_cb, pattern="^catshow_"),
                CallbackQueryHandler(settings_cb, pattern="^settings$"),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
                MessageHandler(reply_btn_filter, reply_button_handler),
            ],
            ADDING_CATEGORY: [
                MessageHandler(reply_btn_filter, reply_button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_profile),
                CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"),
            ],
            SETTINGS_MENU: [
                CallbackQueryHandler(set_lang_cb,        pattern="^set_lang$"),
                CallbackQueryHandler(set_currency_cb,    pattern="^set_currency$"),
                CallbackQueryHandler(manage_categories_cb, pattern="^catmgr$"),
                CallbackQueryHandler(category_hide_cb,   pattern="^cathide_"),
                CallbackQueryHandler(category_del_cb,    pattern="^catdel_"),
                CallbackQueryHandler(category_show_cb,   pattern="^catshow_"),
                CallbackQueryHandler(profile_add_cat_cb, pattern="^profile_addcat$"),
                CallbackQueryHandler(main_menu_cb,       pattern="^main_menu$"),
                CallbackQueryHandler(lang_chosen_cb,     pattern="^lang_"),
                CallbackQueryHandler(currency_chosen_cb, pattern="^cur_"),
                MessageHandler(reply_btn_filter, reply_button_handler),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
