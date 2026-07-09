import re

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_CARD_SUFFIX_RE = re.compile(r"nr\.?\s*xxxx\s*\d+")
_REFERENCE_RE = re.compile(r"auftrags-nr\.?\s*\S+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_description(text):
    normalized = text.lower().translate(_UMLAUT_MAP)
    normalized = _CARD_SUFFIX_RE.sub(" ", normalized)
    normalized = _REFERENCE_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized
