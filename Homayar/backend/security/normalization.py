from __future__ import annotations

import re
import unicodedata

from email_validator import EmailNotValidError, validate_email

from backend.speech.text_normalize import normalize_digits


def normalize_email(value: str) -> str:
    try:
        normalized = validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("Invalid email address") from exc
    return normalized.casefold()


def normalize_iranian_mobile(value: str) -> str:
    raw = normalize_digits(unicodedata.normalize("NFKC", value))
    digits = re.sub(r"[^0-9+]", "", raw)
    if digits.startswith("0098"):
        digits = "+" + digits[2:]
    elif digits.startswith("98") and not digits.startswith("+"):
        digits = "+" + digits
    elif digits.startswith("09"):
        digits = "+98" + digits[1:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "+98" + digits
    if not re.fullmatch(r"\+989\d{9}", digits):
        raise ValueError("Invalid Iranian mobile number")
    return digits


def normalize_workspace_slug(value: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return candidate[:80] or "workspace"

