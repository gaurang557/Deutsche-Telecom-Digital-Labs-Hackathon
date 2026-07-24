"""Redact sensitive data before it is written anywhere durable.

Must be called on data at the moment it's about to be logged -- never
after. Walks dicts/lists/strings recursively and returns a *new*
structure of the same shape, so a reader can still see that something
was there (e.g. "<EMAIL>" instead of the field just disappearing).
"""

import hashlib
import json
import re

# Dict keys that get redacted outright, whatever type their value is.
# Substring match, case-insensitive: a key CONTAINING any of these words
# is redacted, not just a key named exactly one of them. This is a
# deliberate choice to catch "confirmation_token" and "auth_token" (real
# field names in the shared contract) at the cost of also catching
# unrelated keys that happen to contain "auth" (e.g. "author").
_SENSITIVE_KEY_SUBSTRINGS = ("password", "token", "api_key", "secret", "auth", "credential")

_MAX_STRING_LEN = 2000

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# sk-... style secret keys (OpenAI-style) and "Bearer <token>" auth
# headers. Both get collapsed to <SECRET> including the prefix, so
# nothing about the key's shape leaks into the log.
_API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._-]+\b", re.IGNORECASE)

# 13-19 digits (covers Visa/Mastercard/Amex/Diners-length card numbers),
# optionally separated by spaces or dashes the way they're usually
# printed. Written as "one digit, then 12-18 more digits each with an
# optional separator in front" (rather than "13-19 digits each with an
# optional separator after") so the match always ends on a digit --
# otherwise a trailing separator before the next word gets swallowed
# into the match and eaten along with it.
# The \b on both ends matters too: a digit run glued onto letters or
# underscores (e.g. "invoice_2024001234567890") is NOT treated as a card
# number, because underscore and digit are both "word" characters, so
# there's no word-boundary in the middle of that token.
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")

# Indian mobile numbers: optional "+91" prefix (with optional space/dash)
# or a leading "0", then 10 digits starting 6-9 (the range TRAI allocates
# to mobile numbers). Uses lookaround instead of \b at the start because
# "+" isn't a word character, so a \b there would refuse to match right
# after the "+" in "+91 98765...". Applied after the card regex, so a
# card number's digits are already gone by the time this runs.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+91[-\s]?|0)?[6-9]\d{9}(?!\d)")


def redact_sensitive_data(data):
    """Recursively redact emails, API keys, card numbers, phone numbers,
    sensitive dict keys, and long text blobs.

    Returns a new structure of the same shape; `data` itself is never
    mutated in place.
    """
    if isinstance(data, dict):
        return _redact_dict(data)
    if isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    if isinstance(data, str):
        return _redact_string(data)
    # Numbers, bools, None, datetimes, etc. carry nothing to redact.
    return data


def _is_sensitive_key(key) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(word in lowered for word in _SENSITIVE_KEY_SUBSTRINGS)


def _mask_value(value) -> str:
    # Hash the value itself (not the key) so that the SAME token value
    # appearing under two different event types -- e.g. confirmation_token
    # in both a "confirmation_requested" and a "confirmation_granted"
    # audit event -- produces the SAME masked tag. That lets the two
    # events be correlated in the log without the real token ever being
    # written anywhere. Different values always produce different tags.
    try:
        canonical = json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        canonical = str(value)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:4]
    return f"<SECRET:{digest}>"


def _redact_dict(data: dict) -> dict:
    redacted = {}
    for key, value in data.items():
        if _is_sensitive_key(key):
            # Blanket redaction: whatever the value is -- string, nested
            # dict, list -- a key containing "token" etc. never gets its
            # value written out at all, only a correlatable fingerprint.
            redacted[key] = _mask_value(value)
        else:
            redacted[key] = redact_sensitive_data(value)
    return redacted


def _redact_string(text: str) -> str:
    # Long blobs (e.g. a whole extracted document) are collapsed to a
    # fingerprint instead of being pattern-scanned. Scanning megabytes of
    # text for every pattern on every audit write isn't worth it, and the
    # fingerprint is still enough to prove two log entries reference the
    # same text without ever writing the text itself.
    if len(text) > _MAX_STRING_LEN:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]
        return f"<TEXT:len={len(text)},sha={digest}>"

    text = _EMAIL_RE.sub("<EMAIL>", text)
    text = _API_KEY_RE.sub("<SECRET>", text)
    text = _BEARER_RE.sub("<SECRET>", text)
    text = _CARD_RE.sub("<CARD>", text)
    text = _PHONE_RE.sub("<PHONE>", text)
    return text
