"""Background scheduler: each source runs when its next_run_at is due (backoff is stored by refresh_source)."""
import logging
import random
import threading

from . import timeutil as T
from .db import open_db
from .ingest import refresh_source
from .sources.base import Fetcher

log = logging.getLogger("transitnotice.scheduler")


def run_due(conn, cfg, adapters, fetcher, alerter, force=False):
    results = {}
    now = T.now_iso()
    for ad in adapters:
        row = conn.execute("SELECT next_run_at, enabled FROM sources WHERE source_key=?", (ad.source_key,)).fetchone()
        if row and not row["enabled"]:
            continue
        due = force or row is None or row["next_run_at"] is None or row["next_run_at"] <= now
        if due:
            results[ad.source_key] = refresh_source(conn, cfg, ad, fetcher, alerter)
    return results


class Scheduler(threading.Thread):
    def __init__(self, cfg, adapters, alerter, tick=30):
        super().__init__(daemon=True, name="tn-scheduler")
        self.cfg, self.adapters, self.alerter, self.tick = cfg, adapters, alerter, tick
        self.stop_event = threading.Event()

    def run(self):
        conn = open_db(self.cfg.db_path)          # own connection: sqlite connections are per thread here
        fetcher = Fetcher(self.cfg)
        self.stop_event.wait(5 + random.random() * 5)   # let the web server come up (demo source is self-hosted)
        while not self.stop_event.is_set():
            try:
                run_due(conn, self.cfg, self.adapters, fetcher, self.alerter)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self.stop_event.wait(self.tick)

    def stop(self):
        self.stop_event.set()
