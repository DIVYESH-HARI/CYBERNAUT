"""Alerting: exactly one alert per (notice, revision, subscriber), enforced by a UNIQUE constraint.
Demo mode (default) writes to logs/alerts.log and the alert_log table instead of sending real email."""
import json
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from . import timeutil as T
from .ingest import effective_status

log = logging.getLogger("transitnotice.alerts")


def matches(filter_json, notice, routes, stations):
    try:
        f = json.loads(filter_json or "{}")
    except ValueError:
        f = {}
    low = lambda xs: {str(x).casefold() for x in xs}
    if f.get("routes") and not (low(f["routes"]) & low(routes)):
        return False
    if f.get("stations") and not (low(f["stations"]) & low(stations)):
        return False
    if f.get("types") and notice["type"].casefold() not in low(f["types"]):
        return False
    if f.get("areas") and not any(a.casefold() in notice["area"].casefold() for a in f["areas"]):
        return False
    return True


class Alerter:
    def __init__(self, cfg):
        self.cfg = cfg

    def dispatch(self, conn, changes, now_iso):
        sent = 0
        subs = conn.execute("SELECT * FROM subscribers WHERE active=1").fetchall()
        for nid, rev, kind in changes:
            n = conn.execute("SELECT * FROM notices WHERE notice_id=?", (nid,)).fetchone()
            if kind == "new" and effective_status(n, now_iso) == "expired":
                continue                                    # do not alert about history we only just discovered
            routes = [r[0] for r in conn.execute("SELECT route_id FROM notice_routes WHERE notice_id=?", (nid,))]
            stations = [r[0] for r in conn.execute("SELECT station FROM notice_stations WHERE notice_id=?", (nid,))]
            for s in subs:
                if not matches(s["subscriber_filter"], n, routes, stations):
                    continue
                cur = conn.execute("INSERT OR IGNORE INTO alert_log (notice_id, revision_id, subscriber_id, alert_status) "
                                   "VALUES (?,?,?, 'pending')", (nid, rev, s["subscriber_id"]))
                conn.commit()
                if cur.rowcount == 0:
                    continue                                # already alerted for this revision
                status, detail = self._deliver(s, n, rev, kind)
                with conn:
                    conn.execute("UPDATE alert_log SET alert_status=?, alerted_at=?, detail=? WHERE alert_id=?",
                                 (status, now_iso, detail, cur.lastrowid))
                    conn.execute("UPDATE notices SET alert_status=?, alerted_at=? WHERE notice_id=?", (status, now_iso, nid))
                sent += status in ("sent", "logged")
        return sent

    def _message(self, n, rev, kind):
        subject = "[TransitNotice] %s: %s" % (kind.upper(), n["summary"][:80])
        body = ("%s\n\nRoutes: %s\nValid: %s -> %s (UTC)\nWhat changed: %s\nSource: %s\nRevision %s"
                % (n["summary"], n["route_id"] or "-", n["valid_from"] or "-", n["valid_to"] or "open-ended",
                   n["change_summary"], n["source_url"], rev))
        return subject, body

    def _deliver(self, sub, n, rev, kind):
        subject, body = self._message(n, rev, kind)
        if self.cfg.alert_mode == "smtp" and self.cfg.smtp["host"]:
            try:
                m = EmailMessage()
                m["Subject"], m["From"], m["To"] = subject, self.cfg.smtp["sender"], sub["email"]
                m.set_content(body)
                with smtplib.SMTP(self.cfg.smtp["host"], self.cfg.smtp["port"], timeout=20) as s:
                    s.starttls()
                    if self.cfg.smtp["user"]:
                        s.login(self.cfg.smtp["user"], self.cfg.smtp["password"])
                    s.send_message(m)
                return "sent", "smtp"
            except Exception as e:  # noqa: BLE001
                log.error("smtp delivery failed: %s", e)
                return "failed", str(e)[:200]
        Path(self.cfg.log_dir).mkdir(parents=True, exist_ok=True)
        with open(Path(self.cfg.log_dir) / "alerts.log", "a", encoding="utf-8") as fh:
            fh.write("%s to=%s %s\n" % (T.now_iso(), sub["email"], subject))
        return "logged", "demo mode"
