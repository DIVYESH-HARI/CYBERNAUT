"""Configuration. Everything secret or environment specific comes from env vars / .env, never source."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


class Config:
    def __init__(self, **overrides):
        g = os.environ.get
        self.db_path = g("TN_DB_PATH", str(ROOT / "data" / "transitnotice.db"))
        self.admin_token = g("TN_ADMIN_TOKEN") or secrets.token_urlsafe(16)
        self.admin_token_generated = not g("TN_ADMIN_TOKEN")
        self.contact = g("TN_CONTACT", "unset@example.invalid")
        self.scheduler = g("TN_SCHEDULER", "1") == "1"
        self.demo_base_url = g("TN_DEMO_BASE_URL", "http://127.0.0.1:5000")
        self.alert_mode = g("TN_ALERT_MODE", "demo")  # demo | smtp
        # None = use the 'enabled' flags in agencies.json; TN_SOURCES=a,b overrides them
        self.sources = [s.strip() for s in g("TN_SOURCES").split(",") if s.strip()] if g("TN_SOURCES") else None
        self.display_tz = g("TN_DISPLAY_TZ", "Asia/Kolkata")  # zone used for the service-date filter
        self.smtp = dict(host=g("TN_SMTP_HOST"), port=int(g("TN_SMTP_PORT", "587")),
                         user=g("TN_SMTP_USER"), password=g("TN_SMTP_PASSWORD"),
                         sender=g("TN_SMTP_FROM"))
        self.demo_source_dir = str(ROOT / "demo_source")
        self.log_dir = str(ROOT / "logs")
        # fetch policy
        self.http_timeout = 15
        self.max_attempts = 3          # retry ceiling per refresh
        self.backoff_base = 2.0        # seconds; doubles each attempt
        self.min_request_interval = 1.0  # polite rate limit per host
        self.max_backoff_seconds = 6 * 3600
        self.stale_factor = 3          # source is stale after 3 missed refresh intervals
        self.user_agent = "TransitNoticeBot/1.0 (student project; contact: %s)" % self.contact
        for k, v in overrides.items():
            setattr(self, k, v)

    @property
    def user_agent_str(self):
        return "TransitNoticeBot/1.0 (student project; contact: %s)" % self.contact
