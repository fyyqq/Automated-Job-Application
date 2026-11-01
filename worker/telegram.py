TELEGRAM_LIMIT = 4096  # Telegram refuses longer messages
BUTTON_TEXT = "✅ Approve - open job"


def score_icon(score) -> str:
    try:
        score = float(score)
    except (TypeError, ValueError):
        return "⚪"
    return "🟢" if score >= 75 else "🟡"


def build_message(job: dict) -> dict:
    """Plain text only (no parse_mode), so symbols in a cover note can never break the message."""
    email = job.get("career_email") or ""
    source = job.get("email_source") or ""
    if email:
        found_at = f"\n🔎 Found at: {job['email_found_at']}" if source == "web search" and job.get("email_found_at") else ""
        note = f"\nℹ️ {job['email_note']}" if job.get("email_note") else ""
        email_line = f"📧 {email} (from {source}){found_at}{note}\n✉️ A Gmail draft with this note is ready for you to send"
    else:
        email_line = "📧 No career email found, apply through the link below"

    checked = "✅ read the full job post" if job.get("deep_checked") else "⚠️ title only, the post was not read"
    warnings = job.get("warnings") or []
    warning_line = "\n⚠️ Check before sending: " + " ".join(warnings) if warnings else ""

    head = (
        f"{score_icon(job.get('score'))} Score {job.get('score', '?')}: {job.get('title', '')}\n"
        f"🏢 {job.get('company', '')}\n"
        f"📍 {job.get('location', '')}\n"
        f"💡 {str(job.get('reason', ''))[:160]}\n"
        f"{checked}\n"
        f"{email_line}{warning_line}\n"
        f"🔗 Source: {job.get('source', '')}\n\n"
        f"✍️ Cover note:\n"
    )
    note = job.get("cover_note", "")
    room = TELEGRAM_LIMIT - len(head) - 20
    if len(note) > room:
        note = note[: max(room, 0)].rstrip() + " ..."

    return {
        "text": head + note,
        "button_text": BUTTON_TEXT,
        "url": job.get("url", ""),
        "dedupe_key": job.get("dedupe_key", ""),
    }
