"""
Telegram notification for matched EV tenders.

Sends a compact digest message via Telegram Bot API using httpx (no extra library).

Setup:
    1. Create a bot via @BotFather → copy the token
    2. Send any message to your bot, then get your chat_id from:
       https://api.telegram.org/bot<TOKEN>/getUpdates
    3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file

If either env var is missing, notifications are silently skipped.
"""

import logging
import os
import asyncio
from datetime import datetime, timezone
from html import escape as html_escape
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from main import TenderRow

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_MESSAGE_LEN = 4096  # Telegram hard limit
_MAX_DESCRIPTION_LEN = 180
_TELEGRAM_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _resolve_credentials(
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> tuple[str, str]:
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, cid


def telegram_credentials_configured(
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    token, cid = _resolve_credentials(bot_token, chat_id)
    return bool(token and cid)


def _urgency_label(days_left: int | None) -> str:
    if days_left is None:
        return ""
    if days_left <= 3:
        return " [URGENT]"
    if days_left <= 7:
        return " [Soon]"
    return ""


def _escape_telegram(text: str) -> str:
    """Escape user-controlled text for Telegram HTML parse mode."""
    return html_escape(text, quote=True)


def _clip_text(text: str, limit: int = _MAX_DESCRIPTION_LEN) -> str:
    """Trim long descriptions without cutting words mid-way."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "..."


def _redact_token(text: str, token: str) -> str:
    if not text:
        return ""
    return text.replace(token, "[REDACTED]") if token else text


def _response_excerpt(response: httpx.Response, token: str, limit: int = 300) -> str:
    return _redact_token(" ".join(response.text.split())[:limit], token)


def _telegram_retry_after(response: httpx.Response, fallback: float) -> float:
    try:
        payload = response.json()
    except ValueError:
        return fallback

    retry_after = payload.get("parameters", {}).get("retry_after")
    try:
        return max(float(retry_after), fallback)
    except (TypeError, ValueError):
        return fallback


async def _post_telegram_message(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    token: str,
    *,
    max_attempts: int = 3,
) -> bool:
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in _TELEGRAM_RETRYABLE_STATUSES and attempt < max_attempts:
                delay = _telegram_retry_after(exc.response, delay)
                logger.warning("Telegram API returned %s; retrying", status_code)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            logger.error("Telegram API error: %s — %s", status_code, _response_excerpt(exc.response, token))
            return False
        except httpx.RequestError as exc:
            if attempt < max_attempts:
                logger.warning("Telegram request failed with %s; retrying", type(exc).__name__)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            logger.error("Telegram request failed with %s", type(exc).__name__)
            return False
        except Exception as exc:
            logger.error("Telegram send failed with %s", type(exc).__name__)
            return False

    return False


def _format_tender(t: "TenderRow", index: int) -> str:
    urgency = _urgency_label(t.days_left)
    deadline = t.close_date if t.close_date else "Unknown"
    days = f" ({t.days_left}d left)" if t.days_left is not None else ""
    safe_company = _escape_telegram(t.company_match)
    safe_title = _escape_telegram(t.title)
    safe_site = _escape_telegram(t.site)
    safe_ref = _escape_telegram(t.ref_number)
    safe_deadline = _escape_telegram(f"{deadline}{days}")
    safe_link = html_escape(t.link, quote=True)

    lines = [
        f"<b>{index}.{_escape_telegram(urgency)} [{safe_company}]</b>",
        safe_title,
        f"Source: {safe_site} | Ref: {safe_ref}",
        f"Deadline: {safe_deadline}",
    ]

    if t.description:
        lines.append(f"Scope: {_escape_telegram(_clip_text(t.description))}")
    if t.link:
        lines.append(f"<a href=\"{safe_link}\">View tender</a>")

    return "\n".join(lines)


def _build_messages(tenders: list["TenderRow"], date_str: str) -> list[str]:
    """
    Build a single Telegram message with Climatech and EVS sections.
    Splits into multiple messages only if the total exceeds 4096 chars.
    """
    climatech = [t for t in tenders if t.company_match in ("Climatech", "Both")]
    evs = [t for t in tenders if t.company_match in ("EVS", "Both")]

    # Build full message as one block
    parts = []
    parts.append(
        f"<b>KSA EV Tenders - {_escape_telegram(date_str)}</b>\n"
        f"{len(tenders)} new match{'es' if len(tenders) != 1 else ''} found"
    )

    if climatech:
        parts.append(f"\n<b>Climatech Charger ({len(climatech)})</b>")
        for i, t in enumerate(climatech, 1):
            parts.append("\n" + _format_tender(t, i))

    if evs:
        parts.append(f"\n<b>EVS ({len(evs)})</b>")
        for i, t in enumerate(evs, 1):
            parts.append("\n" + _format_tender(t, i))

    # Join and split only if over Telegram limit
    full_text = "\n".join(parts)
    if len(full_text) <= _MAX_MESSAGE_LEN:
        return [full_text]

    # Fallback: split into chunks if extremely long
    messages = []
    current = ""
    for part in parts:
        if len(current) + len(part) + 1 > _MAX_MESSAGE_LEN:
            messages.append(current)
            current = part
        else:
            current = (current + "\n" + part).lstrip()
    if current:
        messages.append(current)
    return messages


async def send_telegram_alert(
    tenders: list["TenderRow"],
    date_str: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """
    Send matched tenders as a Telegram digest message.

    Args:
        tenders:   List of matched TenderRow objects.
        date_str:  Run date string (e.g. "2025-03-29").
        bot_token: Telegram bot token. Falls back to TELEGRAM_BOT_TOKEN env var.
        chat_id:   Telegram chat/channel ID. Falls back to TELEGRAM_CHAT_ID env var.

    Returns:
        True if all messages sent successfully, False otherwise.
    """
    token, cid = _resolve_credentials(bot_token, chat_id)

    if not token or not cid:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False

    url = _API_BASE.format(token=token)

    # Always send a status message, even when no matches found
    if not tenders:
        no_match_msg = (
            f"<b>KSA EV Tenders - {_escape_telegram(date_str)}</b>\n"
            f"Run complete. No new EV-related tenders found today."
        )
        async with httpx.AsyncClient(timeout=15) as client:
            ok = await _post_telegram_message(
                client,
                url,
                {
                    "chat_id": cid,
                    "text": no_match_msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                token,
            )
            if ok:
                logger.info("Telegram no-match status sent")
                return True
            return False

    messages = _build_messages(tenders, date_str)
    success = True

    async with httpx.AsyncClient(timeout=15) as client:
        for index, msg in enumerate(messages):
            ok = await _post_telegram_message(
                client,
                url,
                {
                    "chat_id": cid,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                token,
            )
            if ok:
                logger.info("Telegram message sent (%d chars)", len(msg))
                if index < len(messages) - 1:
                    await asyncio.sleep(0.5)
            else:
                success = False

    return success


async def send_telegram_test_message(
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Send a one-off smoke-test message to verify Telegram credentials."""
    token, cid = _resolve_credentials(bot_token, chat_id)

    if not token or not cid:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing)")
        return False

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (
        "<b>KSA EV Tender Monitor test</b>\n"
        f"Telegram is connected.\n"
        f"Generated at: {_escape_telegram(generated_at)}"
    )

    url = _API_BASE.format(token=token)
    async with httpx.AsyncClient(timeout=15) as client:
        ok = await _post_telegram_message(
            client,
            url,
            {
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            token,
        )
        if ok:
            logger.info("Telegram test message sent")
            return True
        return False
