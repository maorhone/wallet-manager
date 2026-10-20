"""Optional email copy of a notification (FR-N1: only if an internal mail relay is available)."""

import smtplib
from email.message import EmailMessage

from app.config import Settings


def send(settings: Settings, upn: str, subject: str, body: str) -> None:
    """Send one plain-text mail through ``smtp_host``; the caller decides whether to call."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.notify_email_from, upn, subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(msg)
