"""Password policy, strength scoring, and secure generation for PiHerder users."""
from __future__ import annotations

import re
import secrets
import string
from typing import Any

# bcrypt limit
MAX_PASSWORD_BYTES = 72

# Policy (create user, register, change password)
MIN_LENGTH = 10
REQUIRE_UPPER = True
REQUIRE_LOWER = True
REQUIRE_DIGIT = True
REQUIRE_SPECIAL = False  # recommended in strength, not hard-required

_SPECIALS = "!@#$%^&*_-+=?"
_AMBIGUOUS = "0OIl1"  # omit from generated passwords for readability


def policy_rules_text() -> str:
    """Human-readable policy for forms (no storage jargon)."""
    parts = [f"at least {MIN_LENGTH} characters"]
    if REQUIRE_UPPER:
        parts.append("one uppercase letter")
    if REQUIRE_LOWER:
        parts.append("one lowercase letter")
    if REQUIRE_DIGIT:
        parts.append("one digit")
    if REQUIRE_SPECIAL:
        parts.append("one special character")
    # Soft cap: bcrypt uses 72 bytes; for normal letters/digits that is ~72 characters.
    return (
        "Password must include "
        + ", ".join(parts)
        + f". Keep it under {MAX_PASSWORD_BYTES} characters."
    )


def validate_password(password: str) -> tuple[bool, str]:
    """Return (ok, error_message). Empty error when ok."""
    if password is None:
        return False, "Password is required"
    if not isinstance(password, str):
        password = str(password)
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return (
            False,
            f"Password is too long — use at most {MAX_PASSWORD_BYTES} characters "
            "(or fewer if you use symbols/emoji).",
        )
    if len(password) < MIN_LENGTH:
        return False, f"Password must be at least {MIN_LENGTH} characters"
    if REQUIRE_UPPER and not re.search(r"[A-Z]", password):
        return False, "Password must include an uppercase letter"
    if REQUIRE_LOWER and not re.search(r"[a-z]", password):
        return False, "Password must include a lowercase letter"
    if REQUIRE_DIGIT and not re.search(r"[0-9]", password):
        return False, "Password must include a digit"
    if REQUIRE_SPECIAL and not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must include a special character"
    return True, ""


def password_strength(password: str) -> dict[str, Any]:
    """
    Score 0–4 for UI meter.
    0 empty/very weak, 1 weak, 2 fair, 3 good, 4 strong.
    """
    if not password:
        return {"score": 0, "label": "empty", "percent": 0, "ok": False}
    score = 0
    length = len(password)
    if length >= MIN_LENGTH:
        score += 1
    if length >= 14:
        score += 1
    classes = 0
    if re.search(r"[a-z]", password):
        classes += 1
    if re.search(r"[A-Z]", password):
        classes += 1
    if re.search(r"[0-9]", password):
        classes += 1
    if re.search(r"[^A-Za-z0-9]", password):
        classes += 1
    if classes >= 3:
        score += 1
    if classes >= 4 and length >= MIN_LENGTH:
        score += 1
    score = min(4, score)
    labels = {0: "very weak", 1: "weak", 2: "fair", 3: "good", 4: "strong"}
    ok, _ = validate_password(password)
    # Cap score if policy fails
    if not ok and score > 2:
        score = 2
    return {
        "score": score,
        "label": labels.get(score, "weak"),
        "percent": int(score * 25),
        "ok": ok,
    }


def generate_password(length: int = 16) -> str:
    """Cryptographically random password that always meets policy."""
    length = max(MIN_LENGTH, min(int(length or 16), 48))
    lower = "".join(c for c in string.ascii_lowercase if c not in _AMBIGUOUS)
    upper = "".join(c for c in string.ascii_uppercase if c not in _AMBIGUOUS)
    digits = "".join(c for c in string.digits if c not in _AMBIGUOUS)
    specials = _SPECIALS
    # Ensure required classes
    chars = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    alphabet = lower + upper + digits + specials
    while len(chars) < length:
        chars.append(secrets.choice(alphabet))
    # Shuffle
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    pwd = "".join(chars)
    ok, _ = validate_password(pwd)
    if not ok:
        # Extremely unlikely; recurse once
        return generate_password(length)
    return pwd


def format_invite_text(
    *,
    email: str,
    password: str,
    role: str,
    login_url: str,
    display_name: str | None = None,
) -> str:
    name = (display_name or "").strip() or email
    return (
        f"PiHerder access\n"
        f"────────────────\n"
        f"URL:      {login_url}\n"
        f"Email:    {email}\n"
        f"Password: {password}\n"
        f"Role:     {role}\n"
        f"Name:     {name}\n"
        f"\n"
        f"Instructions:\n"
        f"1. Open the URL above and sign in with the temporary password.\n"
        f"2. You will be required to set a new password on first login.\n"
        f"3. If the admin requires 2FA, set up an authenticator app next.\n"
        f"4. Do not share this temporary password after you have changed it.\n"
    )
