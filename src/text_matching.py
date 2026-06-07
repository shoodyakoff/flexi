from __future__ import annotations

import re

_NON_WORD_RE = re.compile(r"[^\w]", re.UNICODE)

# Разрешаем только простые именные формы бренда/продукта.
# Это отсеивает прилагательные и производные вроде "сопроводительные".
_RUSSIAN_NOUN_SUFFIXES = {
    "",
    "а",
    "у",
    "е",
    "ом",
    "ы",
    "ов",
    "ам",
    "ами",
    "ах",
}


def normalize_word_token(word: str) -> str:
    return _NON_WORD_RE.sub("", word).lower()


def matches_anchor_root(word: str, anchor_root: str) -> bool:
    token = normalize_word_token(word)
    root = normalize_word_token(anchor_root)
    if not token or not root:
        return False
    if token == root:
        return True
    if not token.startswith(root):
        return False

    suffix = token[len(root):]
    return suffix in _RUSSIAN_NOUN_SUFFIXES


# Overlay-anchor matching is broader than the brand-anchor rule above: hook
# overlay icons need to match third-declension nouns ("нейросеть", "вкладок"),
# verb forms ("позвали"), and adjective endings — none of which are covered by
# `_RUSSIAN_NOUN_SUFFIXES` (intentionally narrow to protect broll/brand picks).
_MIN_OVERLAY_ROOT_LEN = 4
_MAX_OVERLAY_SUFFIX_LEN = 4


def matches_overlay_anchor(word: str, anchor: str) -> bool:
    """Prefix-match used by the hook-overlay picker. Stricter than naive
    startswith (requires a 4+ char root and a small suffix budget) but laxer
    than `matches_anchor_root` (no closed suffix list)."""

    token = normalize_word_token(word)
    root = normalize_word_token(anchor)
    if not token or not root:
        return False
    if token == root:
        return True
    if len(root) < _MIN_OVERLAY_ROOT_LEN:
        return False
    if not token.startswith(root):
        return False
    return len(token) - len(root) <= _MAX_OVERLAY_SUFFIX_LEN
