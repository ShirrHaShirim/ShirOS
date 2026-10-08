from datetime import UTC, date, datetime

import pytest

from shiros.i18n import (
    Locale,
    TranslationCatalog,
    UserLocalePreference,
    format_date,
    format_datetime,
    format_number,
    format_time,
)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [(Locale.ZH_CN, "保存"), (Locale.EN_US, "Save")],
)
def test_translation_for_both_locales(locale: Locale, expected: str) -> None:
    catalog = TranslationCatalog(
        {"common": {Locale.ZH_CN: {"save": "保存"}, Locale.EN_US: {"save": "Save"}}}
    )
    assert catalog.translate("common", "save", locale) == expected


def test_translation_fallback_order_and_key_fallback() -> None:
    catalog = TranslationCatalog({"core": {Locale.EN_US: {"hello": "Hello"}}})
    assert catalog.translate("core", "hello", Locale.ZH_CN) == "Hello"
    catalog = TranslationCatalog({"core": {Locale.ZH_CN: {"hello": "你好"}}})
    assert catalog.translate("core", "hello", Locale.EN_US) == "你好"
    assert catalog.translate("core", "missing", Locale.EN_US) == "missing"


def test_plugin_resources_are_namespaced_and_collisions_rejected() -> None:
    catalog = TranslationCatalog()
    catalog.register_plugin("calendar", {Locale.EN_US: {"today": "Today"}})
    assert catalog.translate("plugin:calendar", "today", Locale.EN_US) == "Today"
    assert catalog.translate("calendar", "today", Locale.EN_US) == "today"
    with pytest.raises(ValueError, match="already registered"):
        catalog.register_plugin("calendar", {Locale.ZH_CN: {"today": "今天"}})
    with pytest.raises(ValueError, match="Plugin ID"):
        catalog.register_plugin("Calendar", {Locale.EN_US: {"x": "x"}})
    with pytest.raises(ValueError, match="non-empty"):
        TranslationCatalog({"core": {Locale.EN_US: {"": "bad"}}})


def test_locale_formatting_and_timezone_conversion() -> None:
    china = UserLocalePreference(Locale.ZH_CN, "Asia/Shanghai")
    us = UserLocalePreference(Locale.EN_US, "America/Los_Angeles")
    instant = datetime(2026, 10, 6, 12, 5, tzinfo=UTC)
    assert format_number(-1234567.5, china) == "−1,234,567.5"
    assert format_number(-1234567.5, us) == "-1,234,567.5"
    assert format_number(1234.5, china) == "1,234.5"
    assert format_number(0, china) == "0"
    assert format_number(0, us) == "0"
    assert format_date(date(2026, 10, 6), china) == "2026年10月6日"
    assert format_date(date(2026, 10, 6), us) == "10/6/2026"
    assert format_time(instant, china) == "20:05"
    assert format_time(instant, us) == "5:05 AM"
    assert format_datetime(instant, china) == "2026年10月6日 20:05"
    assert format_datetime(instant, us) == "10/6/2026 5:05 AM"


def test_format_time_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="Timezone-aware"):
        format_time(datetime(2026, 10, 6, 12), UserLocalePreference())


def test_preference_validates_timezone() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        UserLocalePreference(timezone="Not/A_Zone")


def test_default_catalog_contains_cli_messages_for_both_locales() -> None:
    from shiros.i18n import default_catalog

    catalog = default_catalog()
    keys = (
        "memory.demo_complete",
        "memory.results",
        "memory.context_size",
        "error.operation_failed",
    )
    for key in keys:
        assert catalog.translate("core", key, Locale.ZH_CN) != key
        assert catalog.translate("core", key, Locale.EN_US) != key
