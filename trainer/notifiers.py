"""Notification dispatchers: Slack, ntfy.sh, email."""

import logging
import smtplib
from email.mime.text import MIMEText

import requests

from lib.config import settings

log = logging.getLogger(__name__)


def notify_training_complete(run_name: str, metrics: dict, run_id: str) -> list[str]:
    """Send notifications via all configured channels. Returns list of channels notified."""
    notified = []

    summary = _format_summary(run_name, metrics, run_id)

    if settings.slack_webhook_url:
        try:
            _send_slack(summary)
            notified.append("slack")
        except Exception as e:
            log.warning(f"Slack notification failed: {e}")

    if settings.ntfy_topic:
        try:
            _send_ntfy(run_name, summary)
            notified.append("ntfy")
        except Exception as e:
            log.warning(f"ntfy notification failed: {e}")

    if settings.smtp_host and settings.alert_email:
        try:
            _send_email(run_name, summary)
            notified.append("email")
        except Exception as e:
            log.warning(f"Email notification failed: {e}")

    return notified


def _format_summary(name: str, metrics: dict, run_id: str) -> str:
    lines = [f"Training complete: {name}"]
    if metrics:
        for k, v in metrics.items():
            if isinstance(v, float):
                lines.append(f"  {k}: {v:.4f}")
            else:
                lines.append(f"  {k}: {v}")
    lines.append(f"Run ID: {run_id}")
    return "\n".join(lines)


def _send_slack(message: str) -> None:
    requests.post(
        settings.slack_webhook_url,
        json={"text": f"```\n{message}\n```"},
        timeout=10,
    )


def _send_ntfy(title: str, message: str) -> None:
    requests.post(
        f"{settings.ntfy_server}/{settings.ntfy_topic}",
        data=message.encode(),
        headers={"Title": f"Waldo: {title}"},
        timeout=10,
    )


def _send_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = f"Waldo: {subject}"
    msg["From"] = settings.smtp_from
    msg["To"] = settings.alert_email

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
