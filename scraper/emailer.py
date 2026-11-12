"""Gmail SMTP sender for the daily digest (STARTTLS). Credentials from the environment / .env:
GMAIL_USER, GMAIL_APP_PASSWORD (a Gmail app password), ALERT_TO (the owner). Optional: PUBLIC_URL (the
read-only GitHub Pages site) and SIGNUP_URL (the Google Form visitors use to ask for the digest).
Send failures are returned, not raised."""
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
    """Send to ALERT_TO. Returns None on success, or an error string."""
    return send_all(cfg, [(cfg.to, subject, body, html)], smtp_factory)[0]


def send_all(cfg: EmailConfig, messages: list[tuple[str, str, str, str | None]],
             smtp_factory=smtplib.SMTP) -> list[str | None]:
    """Send (to, subject, text, html) messages over one connection, each addressed to its own recipient
    (no shared To/Cc, so recipients never see each other). Returns an error or None per message."""
    if not messages:
        return []
    try:
        with smtp_factory(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(cfg.user, cfg.password)
            out = []
            for to, subject, body, html in messages:
                msg = EmailMessage()
                msg["From"], msg["To"], msg["Subject"] = cfg.user, to, subject
                msg.set_content(body)
                if html:
                    msg.add_alternative(html, subtype="html")
                try:
                    s.send_message(msg)
                    out.append(None)
                except smtplib.SMTPException as e:
                    out.append(f"{type(e).__name__}: {e}")
            return out
    except Exception as e:
        return [f"{type(e).__name__}: {e}"] * len(messages)
