"""Every translation carries exactly the keys strings.json defines.

A key missing from ca.json shows the English text in a Catalan Home Assistant; a key present only
in a translation is dead text. Both drift silently, so the shape is pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "phare"
SOURCE = json.loads((COMPONENT / "strings.json").read_text())
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def _keys(node, prefix=""):
    if isinstance(node, dict):
        return {k for key, value in node.items() for k in _keys(value, f"{prefix}{key}.")}
    return {prefix.rstrip(".")}


def test_english_is_strings_json() -> None:
    assert json.loads((COMPONENT / "translations" / "en.json").read_text()) == SOURCE


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_translation_has_the_same_keys(path: Path) -> None:
    assert _keys(json.loads(path.read_text())) == _keys(SOURCE)


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_no_translation_value_is_empty(path: Path) -> None:
    def leaves(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from leaves(value)
        else:
            yield node
    assert all(isinstance(v, str) and v.strip() for v in leaves(json.loads(path.read_text())))


def test_the_app_languages_are_covered() -> None:
    # The Phare app ships in these languages; the integration should not be English-only for them.
    assert {p.stem for p in TRANSLATIONS} >= {"en", "ca", "es", "fr"}
