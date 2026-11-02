"""Alert delivery with a delivery log. Default channel is 'log' (safe demo mode); a webhook is optional."""
import logging
import sqlite3

import httpx

from . import config
from .db import iso, utcnow

log = logging.getLogger("scholartrack.alerts")
MAX_ATTEMPTS = 3


def deliver_pending(conn: sqlite3.Connection, client: httpx.Client | None = None) -> int:
    """Try to deliver every undelivered alert. Each alert exists once per change (UNIQUE change_id),
    so a retry re-sends the SAME alert and never creates a duplicate."""
    rows = conn.execute(
        "SELECT alert_id, message FROM alerts WHERE delivery_status != 'delivered' AND delivery_attempts < ?",
        (MAX_ATTEMPTS,),
    ).fetchall()
    delivered = 0
    for row in rows:
        ok = _send(conn, row["alert_id"], row["message"], "log", lambda m=row["message"]: log.info("ALERT: %s", m))
        if config.WEBHOOK_URL:
            http = client or httpx.Client(timeout=5)
            ok &= _send(conn, row["alert_id"], row["message"], "webhook",
                        lambda m=row["message"], h=http: h.post(config.WEBHOOK_URL, json={"text": m}).raise_for_status())
        conn.execute(
            "UPDATE alerts SET delivery_attempts = delivery_attempts + 1, delivery_status = ? WHERE alert_id = ?",
            ("delivered" if ok else "failed", row["alert_id"]),
        )
        delivered += ok
    return delivered


def _send(conn, alert_id: int, message: str, channel: str, action) -> bool:
    try:
        action()
        status, detail = "ok", None
    except Exception as exc:  # delivery must never crash a refresh
        status, detail = "failed", str(exc)[:300]
    conn.execute(
        "INSERT INTO delivery_log (alert_id, channel, status, detail, at) VALUES (?, ?, ?, ?, ?)",
        (alert_id, channel, status, detail, iso(utcnow())),
    )
    return status == "ok"
