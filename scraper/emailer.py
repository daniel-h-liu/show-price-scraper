"""Gmail SMTP sender for the daily digest (STARTTLS). Credentials from the environment / .env:
GMAIL_USER, GMAIL_APP_PASSWORD (a Gmail app password), ALERT_TO. Send failures are returned, not raised."""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587
REQUIRED = ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_TO")


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class EmailConfig:
    user: str
    password: str
    to: str

    @classmethod
    def from_env(cls) -> "EmailConfig":
        missing = [k for k in REQUIRED if not os.environ.get(k)]
        if missing:
            raise ValueError(f"missing email setting(s): {', '.join(missing)} (set them in .env)")
        return cls(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"], os.environ["ALERT_TO"])


def send(cfg: EmailConfig, subject: str, body: str, html: str | None = None,
         smtp_factory=smtplib.SMTP) -> str | None:
    """Returns None on success, or an error string."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg.user, cfg.to, subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        with smtp_factory(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(cfg.user, cfg.password)
            s.send_message(msg)
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"
