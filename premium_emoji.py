"""
Premium (custom) emoji support for the finance bot.

HOW IT WORKS
------------
Telegram custom (premium) emoji can only be rendered with the HTML or
MarkdownV2 parse modes, by attaching a <tg-emoji emoji-id="..."> tag around a
normal "fallback" emoji. They can be SENT only by bots whose owner has a
Telegram Premium subscription (yours does). Viewers do NOT need Premium to
SEE them — if a viewer's client can't show the custom emoji, it falls back to
the normal emoji inside the tag.

This module exposes `render()`, which the bot runs on every outgoing message:
  1. HTML-escapes the text (so user descriptions with < > & can't break it).
  2. Converts the bot's lightweight Markdown (*bold*, `code`, _italic_) to HTML.
  3. Replaces known emoji with <tg-emoji> tags, using the IDs in EMOJI_IDS.

Until you fill in EMOJI_IDS, step 3 is a no-op and the bot works exactly as
before (just rendered via HTML instead of Markdown). Add IDs gradually — only
the emoji you map get "upgraded", the rest stay normal.

HOW TO GET A custom_emoji_id
----------------------------
1. Create an emoji pack via @stickers in Telegram (Premium required).
2. From your Premium account, send one of those emoji to your bot (or any chat
   it can read). The incoming message's entities contain a `custom_emoji_id`
   for each custom emoji — log `update.message.entities` to read it. (You can
   also resolve details via the Bot API method getCustomEmojiStickers.)
3. Paste the numeric id below next to the matching base emoji.
"""

import html
import os
import re

# Master switch. Set PREMIUM_EMOJI=0 in fin.env to disable without editing IDs.
ENABLED = os.getenv("PREMIUM_EMOJI", "1").strip().lower() not in ("0", "false", "no", "")

# base emoji -> custom_emoji_id (a string of digits from YOUR pack).
# Leave "" to keep the normal emoji. Fill in the ones you want to upgrade.
EMOJI_IDS: dict[str, str] = {
    "💰": "5287231198098117669",   # денежный мешок — доход/деньги
    "💸": "5264713049637409446",   # рука с монетой — расход
    "💵": "5197434882321567830",   # зелёный доллар — сумма
    "📥": "5443127283898405358",   # стрелка вниз — итого доход
    "📤": "5445355530111437729",   # стрелка вверх — итого расход
    "➕": "5397916757333654639",   # зелёный плюс — кнопка Доход
    "➖": "5229113891081956317",   # минус — кнопка Расход
    "💼": "5449438768404662011",                      # balance (обычный)
    "📅": "5276398496008663230",                      # today / date
    "📆": "5276398496008663230",                      # calendar
    "📊": "5451882707875276247",   # график — месяц
    "📈": "5244837092042750681",   # график — статистика
    "📝": "5395444784611480792",   # планшет с ручкой — описание/записи
    "🔝": "5310278924616356636",   # мишень — топ расходов
    "📌": "5397782960512444700",   # пушпин — заголовок меню
    "⚙️": "5303214794336125778",   # калькулятор — настройки
    "💱": "5361993818373655559",   # currency / rates -> reuse 🔄 (обмен валют)
    "✅": "5213406375341731253",                      # ok / saved
    "❌": "5199800168056116715",                      # error / cancelled
    "⏳": "4911241630633165627",                      # loading
    "⚠️": "5276240711795107620",                      # warning
    "📋": "5197269100878907942",   # планшет с ручкой — отчёт
    "💚": "5449380056201697322",                      # income marker (green)
    "🔴": "5471954395719539651",                      # expense marker (red)
    "🏠": "5416041192905265756",
    "🔙": "4956425790393680550",                      # home / main menu
    "👋": "5472055112702629499",                      # welcome wave
    "🌍": "5397753673130463064",                      # language / globe
    "🔄": "5361993818373655559",   # анимированная стрелка-обмен — баланс в отчёте

    # ── Переиспользованные premium-id (дубли допустимы) — чтобы весь текст был premium ──
    "🏷️": "5240228673738527951",   # ярлык -> reuse 📌
    "👤": "5275979556308674886",   # профиль -> reuse 💼
    "🗂": "5298853345241358103",   # категории -> reuse 📋
    "✏️": "5334673106202010226",   # карандаш -> reuse 📝
    "🙂": "5341350410252723241",   # смайл -> reuse 👋
    "↩️": "5352759161945867747",   # возврат (свой premium id)
    # иконки категорий -> reuse ближайших premium id
    # ─── Флаги валют (premium) ───
    "🇬🇪": "5440371950708864925",   # GEL
    "🇪🇺": "5228784522924930237",   # EUR
    "🇺🇦": "5445118241758257251",   # UAH
    "🇷🇺": "5449408995691341691",   # RUB
    "🇰🇿": "5228718354658769982",   # KZT
    "🇺🇸": "5202021044105257611",   # USD
    "🇬🇧": "5202196682497859879",   # GBP
    "🇹🇷": "5226948110873278599",   # TRY
    "🇵🇱": "5291847690940852675",   # PLN
    "🇨🇿": "5429496861587156146",   # CZK
    "🇧🇾": "5382219601054544127",   # BYN
    "🇦🇿": "5224254431939275524",   # AZN
    "🇦🇲": "5411455658186778270",   # AMD
    "🇨🇳": "5431782733376399004",   # CNY
    "🇯🇵": "5456261908069885892",   # JPY
    "🇦🇪": "5449495646656537594",   # AED
    "🇨🇭": "5442703336266543270",   # CHF
    "🇸🇪": "5384542551296455687",   # SEK
    "🍔": "5372998546788194447",   # еда -> reuse 💵
    "🛒": "5312361253610475399",   # продукты -> reuse 📥
    "🚗": "5445085952194124000",   # транспорт -> reuse 🔝
    "💡": "5422439311196834318",   # счета -> reuse ⚙️
    "🎮": "5467583879948803288",   # развлечения -> reuse 📈
    "👕": "5364089929917813058",   # одежда -> reuse 📋
    "💊": "5134469678214677470",   # здоровье -> reuse 💚
    "📚": "5373098009640836781",   # образование -> reuse 📝
    "🎁": "5203996991054432397",   # подарки -> reuse 💰
    "✈️": "5386632774440476853",   # путешествия -> reuse 🌍
    "🐾": "5258028182149286660",   # другое -> reuse 📌
}

# ── The Markdown subset the bot uses -> HTML ─────────────────────────────────
# Applied AFTER html-escaping, so the only "<" present are tags we insert.
_CODE_RE = re.compile(r"`([^`\n]+?)`")
_BOLD_RE = re.compile(r"\*(.+?)\*")
_ITALIC_RE = re.compile(r"_([^_\n]+?)_")


def _md_to_html(text: str) -> str:
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


def _premiumize(text: str) -> str:
    if not ENABLED:
        return text
    for emoji, emoji_id in EMOJI_IDS.items():
        if emoji_id and emoji in text:
            text = text.replace(
                emoji, f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>'
            )
    return text


def render(text: str) -> str:
    """Escape + convert the bot's Markdown to HTML + apply premium emoji.

    The result MUST be sent with parse_mode="HTML".
    """
    if not isinstance(text, str):
        return text
    text = html.escape(text, quote=False)
    text = _md_to_html(text)
    text = _premiumize(text)
    return text


def custom_id_for(emoji: str) -> str:
    """Return the custom (premium) emoji id mapped to a base emoji, for use as
    an InlineKeyboardButton / KeyboardButton ``icon_custom_emoji_id`` (Bot API 9.4).

    Returns "" when premium emoji are disabled (PREMIUM_EMOJI=0) or the emoji has
    no mapping in EMOJI_IDS, so callers can fall back to a plain text emoji.
    """
    if not ENABLED:
        return ""
    return (EMOJI_IDS.get(emoji) or "").strip()


def button_parts(label: str) -> tuple[str, str]:
    """Split a leading premium emoji out of a button label.

    Returns (icon_custom_emoji_id, text):
    - If the label starts with an emoji that has a premium id (and premium is
      enabled), returns that id plus the remaining text (emoji stripped), so the
      caller can pass it as InlineKeyboardButton/KeyboardButton
      ``icon_custom_emoji_id`` (Bot API 9.4).
    - Otherwise returns ("", label) unchanged, keeping the normal emoji inline.
    """
    if not label:
        return "", label
    parts = label.split(" ", 1)
    if len(parts) == 2:
        emoji, rest = parts[0], parts[1]
        cem = custom_id_for(emoji)
        if cem:
            return cem, rest
    return "", label
