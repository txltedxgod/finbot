import sqlite3

import pytest

from database import Database
from i18n import TRANSLATIONS


def test_user_onboarding_and_preferences(tmp_path) -> None:
    db = Database(str(tmp_path / "fin_test.db"))
    assert db.get_user(1001) is None
    assert db.is_setup_done(1001) is False

    db.ensure_user(1001, "Alex")
    user = db.get_user(1001)
    assert user is not None
    assert user["name"] == "Alex"
    assert db.is_setup_done(1001) is True

    db.set_user_lang(1001, "en")
    db.set_user_currency(1001, "EUR")
    updated = db.get_user(1001)
    assert updated["lang"] == "en"
    assert updated["currency"] == "EUR"


def test_records_categories_and_multicurrency_stats(tmp_path) -> None:
    db = Database(str(tmp_path / "records.db"))
    db.ensure_user(1, "Alice")
    db.ensure_user(2, "Bob")

    rec_inc = db.add_record(1, "income", 5000.0, "Salary", "2026-11-01", category="salary", currency="EUR")
    rec_exp1 = db.add_record(1, "expense", 120.5, "Groceries", "2026-11-01", category="food", currency="EUR")
    rec_exp2 = db.add_record(1, "expense", 450.0, "Metro pass", "2026-11-02", category="transport", currency="CZK")
    assert rec_inc > 0 and rec_exp1 > 0 and rec_exp2 > 0

    with pytest.raises(sqlite3.IntegrityError):
        db.add_record(1, "expense", -10.0, "Invalid negative", "2026-11-02")

    day_rows = db.get_records_by_date(1, "2026-11-01")
    assert len(day_rows) == 2

    month_rows = db.get_records_by_month(1, 2026, 11)
    assert len(month_rows) == 3

    totals = db.get_category_totals(1, default_currency="RUB")
    assert {(row["category"], row["cur"]): row["total"] for row in totals} == {
        ("food", "EUR"): 120.5,
        ("transport", "CZK"): 450.0,
    }

    stats = db.get_stats(1, default_currency="RUB")
    assert stats["total_records"] == 3
    assert stats["days"] == 2
    assert stats["income_by_cur"] == {"EUR": 5000.0}
    assert stats["expense_by_cur"] == {"EUR": 120.5, "CZK": 450.0}

    # Isolation: user 2 cannot delete user 1's record
    assert db.delete_record(rec_exp1, user_id=2) is False
    assert db.delete_record(rec_exp1, user_id=1) is True


def test_custom_and_hidden_categories_and_i18n_keys(tmp_path) -> None:
    db = Database(str(tmp_path / "cats.db"))
    db.ensure_user(7, "Carol")

    cat_id = db.add_custom_category(7, "Coffee")
    assert db.get_custom_category_name(7, cat_id) == "Coffee"
    assert len(db.get_custom_categories(7)) == 1
    assert db.delete_custom_category(7, cat_id) is True
    assert db.get_custom_categories(7) == []

    db.hide_category(7, "games")
    assert "games" in db.get_hidden_categories(7)
    db.unhide_category(7, "games")
    assert "games" not in db.get_hidden_categories(7)

    assert {"ru", "uk", "en"}.issubset(TRANSLATIONS.keys()) or len(TRANSLATIONS) >= 2
    base_keys = {"choose_lang", "welcome", "btn_income", "btn_expense", "stats_header"}
    for lang_map in TRANSLATIONS.values():
        assert base_keys.issubset(lang_map.keys())
