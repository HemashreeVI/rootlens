"""Notifications: who to tell, what to say, and how to send it (Telegram)."""
import os

import httpx

# An incident this old when the controller first sees it is not announced as
# "new" (this avoids a flood of messages after a restart).
ANNOUNCE_MAX_AGE_SECONDS = 900
REMINDER_SECONDS = 900
MAX_MESSAGE_CHARS = 3500

_warned_disabled = False


def _env(environ):
    return os.environ if environ is None else environ


def _split(raw):
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def recipients(service, environ=None):
    """Chat ids for the owner of this service, else the default people."""
    env = _env(environ)
    chosen = []

    if service:
        key = "NOTIFY_CHAT_" + service.upper().replace("-", "_")
        chosen = _split(env.get(key))

    if not chosen:
        chosen = _split(env.get("NOTIFY_CHAT_DEFAULT"))

    unique = []
    for chat in chosen:
        if chat not in unique:
            unique.append(chat)

    return unique


def deliverable(service, environ=None):
    """True if a message about this service could actually be sent."""
    env = _env(environ)
    return bool(env.get("TELEGRAM_BOT_TOKEN")) and bool(recipients(service, env))


def _secs(n):
    n = int(round(n))
    if n < 90:
        return f"{n}s"
    return f"{n // 60}m {n % 60}s"


def duration_text(seconds):
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rest}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def trigger_text(trigger):
    if not trigger:
        return None

    when = (
        f"{_secs(trigger.get('seconds_before_detection', 0))} before "
        f"detection ({trigger.get('strength')} correlation)"
    )

    if trigger.get("kind") == "rollback":
        return (
            f"{trigger.get('service')} was rolled back to "
            f"{trigger.get('version')} (from {trigger.get('previous_version')}) "
            f"{when}"
        )

    return f"{trigger.get('service')} {trigger.get('version')} was deployed {when}"


def robot_text(decision, mode):
    kind = decision.get("decision")

    if kind == "wait":
        return "waiting before the next step"
    if kind != "act":
        return "escalated to a human"

    phrases = {"rollback_deployment": "roll back", "restart_service": "restart"}
    verb = phrases.get(decision.get("action"), str(decision.get("action")))
    prefix = "would " if mode == "dry-run" else ""
    text = f"{prefix}{verb} {decision.get('service')}"

    if decision.get("target_version"):
        text += f" to {decision['target_version']}"

    return text


def build_message(kind, info):
    cause = info.get("cause") or "unknown cause"
    owner = info.get("service") or "unassigned"
    lines = []

    if kind == "opened":
        lines.append(f"\U0001F534 Incident: {cause}")
    elif kind == "needs_human":
        lines.append(f"\U0001F6A8 NEEDS A HUMAN: {cause}")
    elif kind == "reminder":
        lines.append(f"\u23F0 Still open after {info.get('duration', 'a while')}: {cause}")
    elif kind == "resolved":
        lines.append(f"\u2705 Resolved after {info.get('duration', '?')}: {cause}")
    else:
        lines.append(f"RootLens: {cause}")

    if kind != "resolved":
        lines.append(f"Owner: {owner}")

        if info.get("confidence"):
            lines.append(f"Confidence: {info['confidence']}")

        if info.get("trigger"):
            lines.append(f"Likely trigger: {info['trigger']}")

        if kind == "needs_human" and info.get("reason"):
            lines.append(f"Why the robot is not acting: {info['reason']}")
        elif info.get("robot"):
            lines.append(f"Robot ({info.get('mode', '?')}): {info['robot']}")

    if info.get("dashboard_url"):
        lines.append(f"Dashboard: {info['dashboard_url']}")

    return "\n".join(lines)[:MAX_MESSAGE_CHARS]


def plan_notice(entry, decision_kind, age_seconds, now):
    """Which message, if any, should go out for an active incident?

    Returns None, "opened", "needs_human", "reminder", or "mute".
    """
    if entry.get("muted"):
        return None

    if not entry.get("announced"):
        if age_seconds > ANNOUNCE_MAX_AGE_SECONDS:
            return "mute"
        return "needs_human" if decision_kind == "escalate" else "opened"

    if decision_kind == "escalate" and entry.get("kind") != "escalate":
        return "needs_human"

    if now - entry.get("last_notice", 0) >= REMINDER_SECONDS:
        return "reminder"

    return None


def send_telegram(token, chat_id, text):
    # Never print the exception text: it can contain the URL, and the URL
    # contains the token.
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )

        if response.status_code != 200:
            try:
                description = response.json().get("description", "")
            except Exception:
                description = ""
            print(
                f"Telegram refused the message (HTTP {response.status_code}): "
                f"{description}",
                flush=True,
            )
            return False

        return True

    except Exception as error:
        print(f"Telegram send failed: {type(error).__name__}", flush=True)
        return False


def notify(kind, info, environ=None):
    """Send one notice. Returns how many people it reached."""
    global _warned_disabled

    env = _env(environ)
    token = env.get("TELEGRAM_BOT_TOKEN", "")

    if not token:
        if not _warned_disabled:
            print(
                "Notifications are disabled (TELEGRAM_BOT_TOKEN is not set).",
                flush=True,
            )
            _warned_disabled = True
        return 0

    chats = recipients(info.get("service"), env)
    if not chats:
        print(
            f"No recipients for {info.get('service') or 'this incident'} "
            "(set NOTIFY_CHAT_DEFAULT).",
            flush=True,
        )
        return 0

    text = build_message(kind, info)
    sent = sum(1 for chat in chats if send_telegram(token, chat, text))

    print(
        f"Notification '{kind}' sent to {sent} of {len(chats)} recipient(s).",
        flush=True,
    )
    return sent