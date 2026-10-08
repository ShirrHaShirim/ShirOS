"""Locale resources and formatting, kept independent of domain records.

Missing translations fall back from the requested locale to zh-CN, then en-US,
then the untranslated key. Formatting is deterministic and does not mutate the
process-wide ``locale`` setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Locale(StrEnum):
    ZH_CN = "zh-CN"
    EN_US = "en-US"


@dataclass(frozen=True, slots=True)
class UserLocalePreference:
    locale: Locale = Locale.ZH_CN
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown timezone: {self.timezone}") from error


class TranslationCatalog:
    """Translations grouped by namespace and locale; plugin IDs own ``plugin:<id>``."""

    def __init__(self, resources: Mapping[str, Mapping[Locale, Mapping[str, str]]] | None = None):
        self._resources: dict[str, dict[Locale, Mapping[str, str]]] = {}
        for namespace, translations in (resources or {}).items():
            self._add_namespace(namespace, translations)

    def _add_namespace(
        self, namespace: str, translations: Mapping[Locale, Mapping[str, str]]
    ) -> None:
        if not namespace or namespace.strip() != namespace:
            raise ValueError("Namespace must be a non-empty trimmed string")
        if namespace in self._resources:
            raise ValueError(f"Translation namespace already registered: {namespace}")
        normalized: dict[Locale, Mapping[str, str]] = {}
        for locale, messages in translations.items():
            locale = Locale(locale)
            checked: dict[str, str] = {}
            for key, value in messages.items():
                if not key or not isinstance(value, str):
                    raise ValueError(
                        "Translation keys must be non-empty and values must be strings"
                    )
                checked[key] = value
            normalized[locale] = MappingProxyType(checked)
        self._resources[namespace] = normalized

    def register_plugin(
        self, plugin_id: str, translations: Mapping[Locale, Mapping[str, str]]
    ) -> None:
        """Register a plugin's full locale bundle under its exclusive namespace."""
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if not plugin_id or any(char not in allowed for char in plugin_id):
            raise ValueError("Plugin ID must contain lowercase ASCII letters, digits, '-' or '_'")
        self._add_namespace(f"plugin:{plugin_id}", translations)

    def translate(self, namespace: str, key: str, locale: Locale) -> str:
        translations = self._resources.get(namespace, {})
        for candidate in (Locale(locale), Locale.ZH_CN, Locale.EN_US):
            message = translations.get(candidate, {}).get(key)
            if message is not None:
                return message
        return key


def format_number(value: int | float | Decimal, preference: UserLocalePreference) -> str:
    """Format with locale punctuation and grouping without changing global locale."""
    number = Decimal(str(value))
    if not number.is_finite():
        return str(number)
    rendered = f"{abs(number):,f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    sign = (
        ("−" if preference.locale == Locale.ZH_CN else "-")
        if number.is_signed() and not number.is_zero()
        else ""
    )
    return sign + rendered


def format_date(value: date, preference: UserLocalePreference) -> str:
    if preference.locale == Locale.ZH_CN:
        return f"{value.year}年{value.month}月{value.day}日"
    return f"{value.month}/{value.day}/{value.year}"


def format_time(value: datetime, preference: UserLocalePreference) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    local = value.astimezone(ZoneInfo(preference.timezone))
    if preference.locale == Locale.ZH_CN:
        return local.strftime("%H:%M")
    return local.strftime("%I:%M %p").lstrip("0")


def format_datetime(value: datetime, preference: UserLocalePreference) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    local_date = value.astimezone(ZoneInfo(preference.timezone)).date()
    return f"{format_date(local_date, preference)} {format_time(value, preference)}"


def default_catalog() -> TranslationCatalog:
    from shiros.i18n.resources import default_catalog as build_catalog

    return build_catalog()


__all__ = [
    "Locale",
    "TranslationCatalog",
    "UserLocalePreference",
    "default_catalog",
    "format_date",
    "format_datetime",
    "format_number",
    "format_time",
]
