"""Tests that every language has every text, and can show it."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "toshiba_ac_link"
LANGUAGES = sorted(path.name for path in (COMPONENT / "translations").glob("*.json"))


def _flat(data, prefix=""):
    """Return a translation file as {dotted key: text}."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result.update(_flat(value, f"{prefix}{key}."))
        else:
            result[f"{prefix}{key}"] = value

    return result


def _load(path):
    """Return a translation file, flattened."""
    return _flat(json.loads(path.read_text(encoding="utf-8")))


SOURCE = _load(COMPONENT / "strings.json")


def test_all_languages_are_checked():
    """A language that goes missing would pass the other tests."""
    assert LANGUAGES == ["de.json", "en.json", "it.json", "nl.json"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_language_has_the_same_keys_as_strings_json(language):
    """A missing key shows English or nothing, and an extra one is a leftover."""
    keys = set(_load(COMPONENT / "translations" / language))

    assert keys == set(SOURCE)


@pytest.mark.parametrize("language", LANGUAGES)
def test_placeholders_are_the_same_in_each_text(language):
    """A wrong placeholder breaks the text at runtime."""
    texts = _load(COMPONENT / "translations" / language)

    for key, text in SOURCE.items():
        assert set(re.findall(r"{(\w+)}", texts[key])) == set(
            re.findall(r"{(\w+)}", text)
        ), key


def test_english_file_is_the_same_as_strings_json():
    """They are two copies of one text."""
    assert _load(COMPONENT / "translations" / "en.json") == SOURCE
