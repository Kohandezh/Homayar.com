"""Transactional e-mail over SMTP with the standard library.

The previous implementation imported ``fastapi_mail``, which is not a
declared dependency: every send failed inside a broad ``except`` and returned
``False``, so no verification, reset or invitation message could leave any
deployment (found in the Session C integration).  ``smtplib`` needs no new
dependency; the blocking send runs in a worker thread.

Logs never carry the recipient address or the message (both are personal
data and the links carry one-time tokens).
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from backend.config import Settings, get_settings
from backend.services.logging_service import logger

SMTP_TIMEOUT_SECONDS = 15
_BUTTON = (
    "background:#6366f1;color:white;padding:12px 24px;border-radius:8px;"
    "text-decoration:none;display:inline-block;"
)
_PAGE = "font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;"
_NOTE = "color:#6b7280;font-size:12px;"


def _message(
    settings: Settings, to: str, subject: str, body: str, html: str | None
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=settings.smtp_from.rpartition("@")[2] or None)
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message


def _deliver(settings: Settings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
    with smtp:
        if settings.smtp_port != 465 and (settings.smtp_use_tls or settings.smtp_port == 587):
            smtp.starttls(context=context)
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_email(to: str, subject: str, body: str, html: str | None = None) -> bool:
    """Send one message; ``False`` when SMTP is not configured or delivery failed."""
    settings = get_settings()
    if not settings.smtp_host:
        logger.warning("EMAIL_SKIP reason=smtp_not_configured")
        return False
    try:
        await asyncio.to_thread(_deliver, settings, _message(settings, to, subject, body, html))
    except (OSError, smtplib.SMTPException) as exc:
        logger.error("EMAIL_FAIL error_type=%s", type(exc).__name__)
        return False
    logger.info("EMAIL_SENT subject_kind=%s", subject.split("—")[-1].strip()[:40])
    return True


def send_email_sync(to: str, subject: str, body: str) -> bool:
    """Synchronous send for Celery tasks."""
    return asyncio.run(send_email(to, subject, body))


async def send_password_reset_email(email: str, token: str) -> bool:
    """Password reset link (the token expires after 30 minutes, see auth_service)."""
    settings = get_settings()
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"
    body = f"""سلام،

برای حساب هُمیار درخواست تغییر گذرواژه ثبت شده است. این پیوند تا ۳۰ دقیقه معتبر است:
{reset_url}

اگر این درخواست از شما نبوده، این پیام را نادیده بگیرید.

---

Hi,

You requested a password reset for your Homayar account. This link expires in 30 minutes:
{reset_url}

If you didn't request this, ignore this email.
"""
    html = f"""
<html><body style="{_PAGE}">
<div dir="rtl" lang="fa">
<h2>تغییر گذرواژه</h2>
<p>برای حساب هُمیار درخواست تغییر گذرواژه ثبت شده است. این پیوند تا ۳۰ دقیقه معتبر است.</p>
</div>
<p><a href="{reset_url}" style="{_BUTTON}">تغییر گذرواژه / Reset password</a></p>
<p style="{_NOTE}">This link expires in 30 minutes.
If you didn't request this, ignore this email.</p>
</body></html>
"""
    return await send_email(email, "Homayar — Password Reset", body, html)


async def send_verification_email(email: str, token: str) -> bool:
    """E-mail verification link (the token expires after 24 hours, see auth_service)."""
    settings = get_settings()
    verify_url = f"{settings.frontend_url}/verify-email?token={token}"
    body = f"""سلام،

برای تأیید نشانی ایمیل حساب هُمیار این پیوند را باز کنید (تا ۲۴ ساعت معتبر است):
{verify_url}

اگر حسابی نساخته‌اید، این پیام را نادیده بگیرید.

---

Hi,

Please verify the email address of your Homayar account (the link expires in 24 hours):
{verify_url}

If you didn't create an account, ignore this email.
"""
    html = f"""
<html><body style="{_PAGE}">
<div dir="rtl" lang="fa">
<h2>تأیید ایمیل</h2>
<p>برای تأیید نشانی ایمیل حساب هُمیار روی دکمهٔ زیر بزنید. این پیوند تا ۲۴ ساعت معتبر است.</p>
</div>
<p><a href="{verify_url}" style="{_BUTTON}">تأیید ایمیل / Verify email</a></p>
<p style="{_NOTE}">The link expires in 24 hours.
If you didn't create an account, ignore this email.</p>
</body></html>
"""
    return await send_email(email, "Homayar — Verify Your Email", body, html)
