"""Password policy, strength scoring, and secure generation for PiHerder users."""
from __future__ import annotations

import re
import secrets
import string
from typing import Any, Mapping

# bcrypt hard ceiling (never raise Settings max above this)
MAX_PASSWORD_BYTES = 72

# Default / seed policy (also the documented floor except min length)
MIN_LENGTH = 10
FLOOR_MIN_LENGTH = 8
REQUIRE_UPPER = True
REQUIRE_LOWER = True
REQUIRE_DIGIT = True
REQUIRE_SPECIAL = False  # recommended in strength, not hard-required

_SPECIALS = "!@#$%^&*_-+=?"
_AMBIGUOUS = "0OIl1"  # omit from generated passwords for readability


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_policy(raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalise a settings-like mapping to a safe policy dict.

    Min length cannot go below 8. Max length cannot exceed 72 UTF-8 bytes
    and cannot sit below min length.
    """
    src = raw or {}
    min_len = _as_int(src.get("password_min_length", src.get("min_length")), MIN_LENGTH)
    min_len = max(FLOOR_MIN_LENGTH, min(min_len, MAX_PASSWORD_BYTES))
    max_len = _as_int(
        src.get("password_max_length", src.get("max_length")), MAX_PASSWORD_BYTES
    )
    max_len = max(min_len, min(max_len, MAX_PASSWORD_BYTES))
    return {
        "min_length": min_len,
        "max_length": max_len,
        "require_upper": _as_bool(
            src.get("password_require_upper", src.get("require_upper")), REQUIRE_UPPER
        ),
        "require_lower": _as_bool(
            src.get("password_require_lower", src.get("require_lower")), REQUIRE_LOWER
        ),
        "require_digit": _as_bool(
            src.get("password_require_digit", src.get("require_digit")), REQUIRE_DIGIT
        ),
        "require_special": _as_bool(
            src.get("password_require_special", src.get("require_special")),
            REQUIRE_SPECIAL,
        ),
    }


def settings_from_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Persistable AppSetting keys for a clamped policy."""
    p = clamp_policy(policy)
    return {
        "password_min_length": p["min_length"],
        "password_max_length": p["max_length"],
        "password_require_upper": p["require_upper"],
        "password_require_lower": p["require_lower"],
        "password_require_digit": p["require_digit"],
        "password_require_special": p["require_special"],
    }


def get_policy() -> dict[str, Any]:
    """Effective policy from instance Settings (defaults if Settings unavailable)."""
    try:
        from .app_settings import load_settings

        return clamp_policy(load_settings())
    except Exception:
        return clamp_policy({})


def policy_summary(policy: Mapping[str, Any] | None = None) -> str:
    """Compact one-line summary for audit (no secrets)."""
    p = clamp_policy(policy) if policy is not None else get_policy()
    flags = []
    if p["require_upper"]:
        flags.append("upper")
    if p["require_lower"]:
        flags.append("lower")
    if p["require_digit"]:
        flags.append("digit")
    if p["require_special"]:
        flags.append("special")
    classes = "+".join(flags) if flags else "any-class"
    return f"min={p['min_length']} max={p['max_length']} {classes}"


def policy_rules_text(policy: Mapping[str, Any] | None = None) -> str:
    """Human-readable policy for forms (matches validate_password)."""
    p = clamp_policy(policy) if policy is not None else get_policy()
    parts = [f"at least {p['min_length']} characters"]
    if p["require_upper"]:
        parts.append("one uppercase letter")
    if p["require_lower"]:
        parts.append("one lowercase letter")
    if p["require_digit"]:
        parts.append("one digit")
    if p["require_special"]:
        parts.append("one special character")
    # Soft cap: bcrypt uses 72 UTF-8 bytes (~72 Latin letters; fewer with emoji).
    return (
        "Password must include "
        + ", ".join(parts)
        + f". Use at most {p['max_length']} Latin letters/digits "
        f"(emoji and symbols count as more than one)."
    )


def validate_password(
    password: str, policy: Mapping[str, Any] | None = None
) -> tuple[bool, str]:
    """Return (ok, error_message). Empty error when ok."""
    p = clamp_policy(policy) if policy is not None else get_policy()
    if password is None:
        return False, "Password is required"
    if not isinstance(password, str):
        password = str(password)
    if len(password.encode("utf-8")) > p["max_length"]:
        return (
            False,
            f"Password is too long — use at most {p['max_length']} characters "
            "(or fewer if you use symbols/emoji).",
        )
    if len(password) < p["min_length"]:
        return False, f"Password must be at least {p['min_length']} characters"
    if p["require_upper"] and not re.search(r"[A-Z]", password):
        return False, "Password must include an uppercase letter"
    if p["require_lower"] and not re.search(r"[a-z]", password):
        return False, "Password must include a lowercase letter"
    if p["require_digit"] and not re.search(r"[0-9]", password):
        return False, "Password must include a digit"
    if p["require_special"] and not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must include a special character"
    return True, ""


def password_strength(password: str) -> dict[str, Any]:
    """
    Score 0–4 for UI meter.
    0 empty/very weak, 1 weak, 2 fair, 3 good, 4 strong.
    """
    p = get_policy()
    if not password:
        return {"score": 0, "label": "empty", "percent": 0, "ok": False}
    score = 0
    length = len(password)
    if length >= p["min_length"]:
        score += 1
    if length >= max(14, p["min_length"] + 4):
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
    if classes >= 4 and length >= p["min_length"]:
        score += 1
    score = min(4, score)
    labels = {0: "very weak", 1: "weak", 2: "fair", 3: "good", 4: "strong"}
    ok, _ = validate_password(password, p)
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
    p = get_policy()
    cap = min(int(p["max_length"]), 48)
    length = max(int(p["min_length"]), min(int(length or 16), cap))
    lower = "".join(c for c in string.ascii_lowercase if c not in _AMBIGUOUS)
    upper = "".join(c for c in string.ascii_uppercase if c not in _AMBIGUOUS)
    digits = "".join(c for c in string.digits if c not in _AMBIGUOUS)
    specials = _SPECIALS
    # Always mix classes so generated passwords meet any Settings subset
    chars = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    alphabet = lower + upper + digits + specials
    if length < len(chars):
        chars = chars[:length]
    while len(chars) < length:
        chars.append(secrets.choice(alphabet))
    # Shuffle
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    pwd = "".join(chars)
    ok, _ = validate_password(pwd, p)
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
