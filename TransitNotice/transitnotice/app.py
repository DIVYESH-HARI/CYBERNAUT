"""Flask application factory."""
import hmac
import json
import logging
import re
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import queries
from . import timeutil as T
from .alerts import Alerter
from .config import Config
from .db import ensure_source, open_db
from .ingest import effective_status, source_freshness
from .scheduler import Scheduler, run_due
from .sources import build_adapters
from .sources.base import Fetcher

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def create_app(cfg=None, start_scheduler=None):
    cfg = cfg or Config()
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"),
                static_folder=str(Path(__file__).parent / "static"))
    app.config["TN"] = cfg
    app.config["ADAPTERS"] = build_adapters(cfg)
    c = open_db(cfg.db_path)                     # run migrations once at start-up, then sync enabled sources
    try:
        for ad in app.config["ADAPTERS"]:
            ensure_source(c, ad.source_key, ad.agency, ad.base_url, ad.timezone, ad.refresh_seconds,
                          getattr(ad, "operator_type", "Government"))
        keys = [ad.source_key for ad in app.config["ADAPTERS"]]
        c.execute("UPDATE sources SET enabled = CASE WHEN source_key IN (%s) THEN 1 ELSE 0 END"
                  % ",".join("?" * len(keys)) if keys else "UPDATE sources SET enabled = 0", keys)
        c.commit()
    finally:
        c.close()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if cfg.admin_token_generated:
        logging.getLogger("transitnotice").warning("TN_ADMIN_TOKEN not set; temporary admin token for this run: %s", cfg.admin_token)

    def db():
        if "db" not in g:
            g.db = open_db(cfg.db_path)
        return g.db

    @app.teardown_appcontext
    def _close(_):
        c = g.pop("db", None)
        if c:
            c.close()

    def is_admin():
        tok = request.headers.get("X-Admin-Token") or request.values.get("token") or ""
        return hmac.compare_digest(tok, cfg.admin_token)

    @app.template_filter("local")
    def _local(iso, tz="UTC"):
        return T.localize(iso, tz)

    @app.template_filter("ago")
    def _ago(iso):
        return T.human_age(iso)

    # ------------------------------------------------------------------ pages
    def run_search(a):
        return queries.search_f(db(), T.now_iso(), a, cfg.display_tz)

    def enabled_sources():
        return [(s, *source_freshness(s)) for s in
                db().execute("SELECT * FROM sources WHERE enabled=1 ORDER BY operator_type, agency")]

    @app.template_global()
    def url_without(key):
        """Current dashboard URL with one filter removed (for the 'x' on a filter chip)."""
        args = {k: v for k, v in request.args.items() if k not in (key, "n") and v}
        return url_for("dashboard", **args)

    source_labels = {ad.source_key: getattr(ad, "label", ad.agency) for ad in app.config["ADAPTERS"]}

    @app.template_filter("sourcelabel")
    def _sourcelabel(src):
        return source_labels.get(src["source_key"], src["agency"])

    @app.template_filter("typelabel")
    def _typelabel(t):
        return queries.TYPE_LABELS.get(t, t)

    @app.template_filter("headline")
    def _headline(summary):
        """Split 'Otteri Traffic Diversion: long text' into a short headline and the body text."""
        cuts = [(summary.find(sep), sep) for sep in (": ", " - ") if 3 < summary.find(sep) <= 90]
        if cuts:
            i, sep = min(cuts)                       # earliest separator wins
            return summary[:i], summary[i + len(sep):]
        return (summary[:110] + "...", summary) if len(summary) > 110 else (summary, "")

    @app.template_filter("statuslabel")
    def _statuslabel(s):
        return queries.STATUS_LABELS.get(s, s)

    @app.route("/")
    def dashboard():
        a = {k: v for k, v in request.args.items() if k in queries.FILTER_KEYS + ("sort",) and v.strip()}
        now = T.now_iso()
        # the dashboard shows everything by default (current notices first, ended ones clearly labelled)
        try:
            shown = max(30, min(int(request.args.get("n", 30)), 3000))   # "show more" grows this
        except ValueError:
            shown = 30
        all_rows = queries.search_f(db(), now, a, cfg.display_tz, limit=None, default_status="all")
        rows, total = all_rows[:shown], len(all_rows)
        options = queries.facet_options(db(), now, a, cfg.display_tz, default_status="all")
        # chips for the filters in use, labelled the same way as in the dropdowns
        names = dict(agency="Agency", operator="Operator", route="Route", station="Location", area="Area",
                     type="Kind", date="Day", year="Year", ends="End date", status="Showing", q="Text")
        chips = []
        for k, v in a.items():
            if k == "sort" or (k == "status" and v == "all"):
                continue
            label = next((o["label"] for o in options.get(k, []) if o["value"] == v), v)
            chips.append((k, names[k], label))
        by_type, by_route = queries.stats(all_rows)
        sources = enabled_sources()
        fresh = {s["source_id"]: (state, text) for s, state, text in sources}
        return render_template("dashboard.html", rows=rows, total=total, f=a, opts=options, chips=chips,
                               sources=sources, fresh=fresh, by_type=by_type, by_route=by_route,
                               status=a.get("status", "all"), sort=a.get("sort", "relevant"),
                               sort_labels=queries.SORT_LABELS,
                               more_url=url_for("dashboard", **{**a, "n": shown + 30}) if total > shown else None)

    @app.route("/notice/<path:nid>")
    def notice(nid):
        d = db()
        n = d.execute("SELECT n.*, s.timezone AS tz FROM notices n JOIN sources s USING(source_id) WHERE notice_id=?", (nid,)).fetchone()
        if not n:
            abort(404)
        revs = d.execute("SELECT * FROM notice_revisions WHERE notice_id=? ORDER BY revision_id DESC", (nid,)).fetchall()
        st = d.execute("SELECT * FROM sources WHERE source_id=?", (n["source_id"],)).fetchone()
        stations = [r[0] for r in d.execute("SELECT station FROM notice_stations WHERE notice_id=? ORDER BY 1", (nid,))]
        now = T.now_iso()
        return render_template("notice.html", n=n, revs=revs, stations=stations, eff=effective_status(n, now),
                               fresh=source_freshness(st))

    @app.route("/status")
    def status_page():
        d = db()
        sources = enabled_sources()
        runs = d.execute("""SELECT r.*, s.source_key FROM fetch_runs r JOIN sources s USING(source_id)
                            WHERE s.enabled=1 ORDER BY run_id DESC LIMIT 30""").fetchall()
        return render_template("status.html", sources=sources, runs=runs)

    @app.route("/subscribe", methods=["GET", "POST"])
    def subscribe():
        d = db()
        msg = err = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            if not EMAIL_RE.match(email):
                err = "Please enter a valid email address."
            else:
                flt = {k: [x.strip() for x in request.form.getlist(k) if x.strip()]
                       for k in ("routes", "stations", "areas", "types")}
                with d:
                    d.execute("""INSERT INTO subscribers (email, subscriber_filter, created_at) VALUES (?,?,?)
                                 ON CONFLICT(email) DO UPDATE SET subscriber_filter=excluded.subscriber_filter, active=1""",
                              (email, json.dumps(flt), T.now_iso()))
                msg = "Subscribed. You will get one alert per new or revised advisory matching your filter."
        return render_template("subscribe.html", msg=msg, err=err, demo=cfg.alert_mode != "smtp",
                               facets=queries.facets(d))

    @app.route("/admin/alerts")
    def admin_alerts():
        if not is_admin():
            abort(403)
        rows = db().execute("""SELECT a.*, s.email, n.summary FROM alert_log a JOIN subscribers s USING(subscriber_id)
                               JOIN notices n USING(notice_id) ORDER BY alert_id DESC LIMIT 100""").fetchall()
        return render_template("alerts.html", rows=rows)

    @app.route("/admin/refresh", methods=["POST"])
    def admin_refresh():
        if not is_admin():
            abort(403)
        res = run_due(db(), cfg, app.config["ADAPTERS"], Fetcher(cfg), Alerter(cfg), force=True)
        return jsonify(res)

    # ------------------------------------------------------------------ JSON API
    @app.route("/api/notices")
    def api_notices():
        now = T.now_iso()
        rows = run_search(request.args)
        fresh = {s["source_id"]: source_freshness(s) for s in db().execute("SELECT * FROM sources")}
        return jsonify(generated_at=now, count=len(rows), notices=[
            {**{k: r[k] for k in ("notice_id", "agency", "route_id", "area", "source_url", "type", "summary",
                                  "published_at", "valid_from", "valid_to", "observed_at", "last_confirmed_at",
                                  "revision_id", "change_summary")},
             "status": r["eff_status"], "source_freshness": fresh[r["source_id"]][0]} for r in rows])

    @app.route("/healthz")
    def healthz():
        db().execute("SELECT 1")
        return "ok"

    # ------------------------------------------------------------------ demo source (fictional agency page)
    @app.route("/demo-source/<path:name>")
    def demo_source(name):
        return send_from_directory(cfg.demo_source_dir, name)

    @app.route("/robots.txt")
    def robots():
        return send_from_directory(cfg.demo_source_dir, "robots.txt")

    start = cfg.scheduler if start_scheduler is None else start_scheduler
    if start:
        sch = Scheduler(cfg, app.config["ADAPTERS"], Alerter(cfg))
        sch.start()
        app.config["SCHEDULER"] = sch
    return app
