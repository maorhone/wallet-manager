import smtplib

from app import notify_email
from app.config import Settings
from app.services.notifications import notify


async def test_email_disabled_by_default_no_smtp(seeded, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SMTP must not be used when notify_email_enabled is false")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    settings = Settings(_env_file=None)
    assert settings.notify_email_enabled is False
    async with seeded() as s:
        n = await notify(
            s, ["dani@corp.example"], "grant_received", None, None, {}, "k", settings=settings
        )
        await s.commit()
    assert n == 1


async def test_email_enabled_sends_once_per_new_row(seeded, monkeypatch):
    sent = []
    monkeypatch.setattr(notify_email, "send", lambda *a: sent.append(a[1]))
    settings = Settings(_env_file=None, notify_email_enabled=True)
    async with seeded() as s:
        for _ in range(2):  # the repeat is deduplicated, so no second mail
            await notify(
                s, ["dani@corp.example"], "grant_received", None, None, {}, "k", settings=settings
            )
        await s.commit()
    assert sent == ["dani@corp.example"]
