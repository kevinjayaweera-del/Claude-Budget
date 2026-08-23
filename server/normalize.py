import re

_UMLAUT_MAP = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    # French/Italian accented Latin letters — folds accented statement text
    # (e.g. "Genève", "Pathé", "hôpital") to the same plain-ASCII form seeded
    # keywords are written in, so one keyword (written without the accent)
    # matches both the accented and unaccented spelling instead of needing a
    # duplicate keyword pair for every accented merchant/term.
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "à": "a", "â": "a",
    "î": "i", "ï": "i",
    "ô": "o",
    "ù": "u", "û": "u",
    "ç": "c",
})
_CARD_SUFFIX_RE = re.compile(r"nr\.?\s*xxxx\s*\d+")
_REFERENCE_RE = re.compile(r"auftrags-nr\.?\s*\S+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_description(text):
    normalized = text.lower().translate(_UMLAUT_MAP)
    normalized = _CARD_SUFFIX_RE.sub(" ", normalized)
    normalized = _REFERENCE_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized
