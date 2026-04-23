from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any


def decode_jwt(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(decoded.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def jwt_email(token: str | None) -> str:
    claims = decode_jwt(token)
    profile = claims.get("https://api.openai.com/profile", {})
    if isinstance(profile, dict):
        email = profile.get("email")
        if isinstance(email, str) and email:
            return email
    email = claims.get("email")
    return email if isinstance(email, str) and email else "-"


def jwt_account_id(token: str | None) -> str | None:
    claims = decode_jwt(token)
    auth = claims.get("https://api.openai.com/auth", {})
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def jwt_user_id(token: str | None) -> str | None:
    claims = decode_jwt(token)
    auth = claims.get("https://api.openai.com/auth", {})
    if isinstance(auth, dict):
        user_id = auth.get("chatgpt_user_id") or auth.get("user_id")
        if isinstance(user_id, str) and user_id:
            return user_id
    user_id = claims.get("user_id")
    if isinstance(user_id, str) and user_id:
        return user_id
    return None


def jwt_exp_epoch(token: str | None) -> float | None:
    claims = decode_jwt(token)
    exp = claims.get("exp")
    if exp is None:
        return None
    try:
        return float(exp)
    except (TypeError, ValueError):
        return None


def jwt_exp_display(token: str | None) -> str:
    exp = jwt_exp_epoch(token)
    if exp is None:
        return "-"
    dt = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
    return dt.strftime("%d/%m %H:%M")


def to_epoch_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 1_000_000_000_000:
        return parsed / 1000.0
    return parsed


def parse_quota(payload: dict[str, Any] | None) -> dict[str, float | str | None]:
    body = payload or {}
    result: dict[str, float | str | None] = {
        "plan": body.get("plan_type", "-"),
        "five_hour_pct": None,
        "five_hour_reset": None,
        "weekly_pct": None,
        "weekly_reset": None,
    }
    found_limits: list[tuple[float, float | None]] = []

    def hunt(node: Any) -> None:
        if isinstance(node, dict):
            pct = node.get("percent_left")
            if pct is None:
                pct = node.get("remaining_percent")
            if pct is None and node.get("used_percent") is not None:
                try:
                    pct = 100.0 - float(node["used_percent"])
                except (TypeError, ValueError):
                    pct = None
            if pct is not None:
                reset_value = node.get("reset_time_ms") or node.get("reset_at")
                if reset_value is None and node.get("reset_after_seconds") is not None:
                    try:
                        reset_value = time.time() + float(node["reset_after_seconds"])
                    except (TypeError, ValueError):
                        reset_value = None
                if reset_value is None and isinstance(node.get("primary_window"), dict):
                    reset_value = node["primary_window"].get("reset_time_ms")
                try:
                    found_limits.append((float(pct), to_epoch_seconds(reset_value)))
                except (TypeError, ValueError):
                    pass
            for value in node.values():
                hunt(value)
        elif isinstance(node, list):
            for item in node:
                hunt(item)

    hunt(body)
    if found_limits:
        result["five_hour_pct"] = found_limits[0][0]
        result["five_hour_reset"] = found_limits[0][1]
    if len(found_limits) > 1:
        result["weekly_pct"] = found_limits[1][0]
        result["weekly_reset"] = found_limits[1][1]
    if result["plan"] == "-":
        result["plan"] = "Team/Biz"
    return result


def format_remaining(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    delta = float(timestamp) - time.time()
    if delta <= 0:
        return "agora"
    hours, rem = divmod(int(delta), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def format_reset_abs(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    dt = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).astimezone()
    return dt.strftime("%d/%m %H:%M")


def email_label(email: str, fallback: str) -> str:
    if "@" in email:
        return email.split("@", 1)[0]
    return fallback
