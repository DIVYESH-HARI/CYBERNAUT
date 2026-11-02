"""API tests against a temp database, using the built-in demo site and a real (in-process) fetch."""
import importlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import normalize as nz
from conftest import FakeFetcher


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOLARTRACK_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("SCHOLARTRACK_SCHEDULER", "0")
    monkeypatch.setenv("SCHOLARTRACK_OPERATOR_TOKEN", "secret-token")
    monkeypatch.setenv("SCHOLARTRACK_BASE_URL", "http://testserver")
    monkeypatch.setenv("SCHOLARTRACK_DEMO", "1")
    # Demo sources only: the API tests must never reach the real internet.
    monkeypatch.setenv("SCHOLARTRACK_SOURCES", str(Path(__file__).parent / "fixtures" / "sources_demo.json"))
    monkeypatch.delenv("SCHOLARTRACK_STALE_MIN", raising=False)
    monkeypatch.setenv("SCHOLARTRACK_SNAPSHOT", "0")
    import app.config, app.demo_site, app.main
    for m in (app.config, app.demo_site, app.main):
        importlib.reload(m)
    with TestClient(app.main.app, base_url="http://testserver") as client:
        # point the fetcher at the in-process ASGI app instead of the network
        def make_fetcher():
            from app.fetcher import Fetcher
            http = httpx.Client(transport=_AsgiBridge(client), base_url="http://testserver")
            return Fetcher("TestBot/1.0", client=http, min_interval=0, sleep=lambda s: None)
        app.main.make_fetcher = make_fetcher
        yield client, app.main


class _AsgiBridge(httpx.BaseTransport):
    def __init__(self, test_client):
        self.c = test_client

    def handle_request(self, request):
        r = self.c.get(request.url.path)
        return httpx.Response(r.status_code, headers={"content-type": r.headers.get("content-type", "text/html")},
                              content=r.content, request=request)


H = {"X-Operator-Token": "secret-token"}


def refresh(client):
    r = client.post("/api/operator/refresh", headers=H)
    assert r.status_code == 200
    return r.json()["results"]


def test_operator_endpoints_require_token(api):
    client, _ = api
    assert client.post("/api/operator/refresh").status_code == 401
    assert client.get("/api/operator/overview", headers={"X-Operator-Token": "wrong"}).status_code == 401
    assert client.get("/api/operator/overview", headers=H).status_code == 200


def test_refresh_collects_both_layouts_and_search_filters_work(api):
    client, _ = api
    results = refresh(client)
    assert {r["status"] for r in results} <= {"ok", "warning"}
    notices = client.get("/api/notices").json()["notices"]
    assert len(notices) >= 8
    assert all(n["url"].startswith("http") and n["source_name"] for n in notices)

    ug = client.get("/api/notices", params={"level": "Undergraduate"}).json()["notices"]
    assert ug and all("Undergraduate" in n["education_level"] for n in ug)
    need = client.get("/api/notices", params={"category": "Need-based"}).json()["notices"]
    assert need and all(n["funding_category"] == "Need-based" for n in need)
    q = client.get("/api/notices", params={"q": "aurora"}).json()["notices"]
    assert [n["title"] for n in q] == ["Aurora Merit Scholarship"]
    assert client.get("/api/notices", params={"q": "_"}).json()["exact"] is False  # SQL wildcard is escaped


def test_unknown_deadlines_excluded_by_date_filters_but_listed_otherwise(api):
    client, _ = api
    refresh(client)
    all_notices = client.get("/api/notices").json()["notices"]
    unknown = [n for n in all_notices if n["due_at"] is None]
    assert unknown and all(n["deadline_state"] == "unknown" and n["due_local"] is None for n in unknown)
    within = client.get("/api/notices", params={"within_days": 3650}).json()["notices"]
    assert within and all(n["due_at"] for n in within)


def test_date_filter_is_timezone_aware(api):
    client, main = api
    refresh(client)
    target = next(n for n in client.get("/api/notices").json()["notices"] if n["title"] == "Aurora Merit Scholarship")
    day_utc = target["due_at"][:10]            # 17:00 IST == 11:30 UTC on that day
    hit = client.get("/api/notices", params={"due_from": day_utc, "due_to": day_utc, "tz": "Asia/Kolkata"}).json()
    assert "Aurora Merit Scholarship" in [n["title"] for n in hit["notices"]]
    # In Pacific/Kiritimati (UTC+14) the same instant falls on the NEXT calendar day.
    miss = client.get("/api/notices", params={"due_from": day_utc, "due_to": day_utc, "tz": "Pacific/Kiritimati"}).json()
    if miss["exact"]:
        assert "Aurora Merit Scholarship" not in [n["title"] for n in miss["notices"]]
    else:   # nothing else that day: the API says so and falls back to closest matches
        assert miss["relaxed"] == ["removed the deadline date filter"]
    assert client.get("/api/notices", params={"due_from": "2026-13-45"}).status_code == 422


def test_mentor_revision_creates_update_history_and_single_alert(api):
    client, _ = api
    refresh(client)
    before = len(client.get("/api/notices", params={"include_withdrawn": True}).json()["notices"])
    client.post("/api/operator/demo", headers=H,
                json={"action": "set_deadline", "key": "aurora-merit", "text": "20 Dec 2030, 10:00 Asia/Tokyo"})
    for _ in range(3):
        refresh(client)
    after = client.get("/api/notices", params={"include_withdrawn": True}).json()["notices"]
    assert len(after) == before                                   # an update, not a new notice
    nid = next(n["notice_id"] for n in after if n["title"] == "Aurora Merit Scholarship")
    detail = client.get(f"/api/notices/{nid}").json()
    assert detail["due_at"] == "2030-12-20T01:00:00Z" and detail["timezone"] == "Asia/Tokyo"
    assert [h["change_type"] for h in detail["history"]] == ["deadline_revised"]
    alerts = client.get("/api/alerts").json()["alerts"]
    assert len([a for a in alerts if a["notice_id"] == nid]) == 1
    a = alerts[0]
    assert a["url"] and a["src_name"] and a["verification"]["code"] == "verified"


def test_inaccessible_source_is_flagged_not_skipped(api):
    client, _ = api
    refresh(client)
    client.post("/api/operator/demo", headers=H, json={"action": "set_mode", "portal": "b", "mode": "down"})
    results = refresh(client)
    failed = [r for r in results if r["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["source_id"] == "demo-portal-b"
    ov = client.get("/api/operator/overview", headers=H).json()
    flagged = [s for s in ov["sources"] if s["needs_review"]]
    assert [s["source_id"] for s in flagged] == ["demo-portal-b"]
    status = client.get("/api/status").json()
    assert status["state"] == "degraded"
    b_notice = next(n for n in client.get("/api/notices").json()["notices"] if n["title"].startswith("Global Horizons"))
    assert b_notice["verification"]["code"] == "unreachable"
    client.post("/api/operator/demo", headers=H, json={"action": "set_mode", "portal": "b", "mode": "ok"})
    refresh(client)
    assert client.get("/api/status").json()["state"] == "fresh"


def test_status_never_reports_stale_data_as_fresh(api, monkeypatch):
    client, main = api
    assert client.get("/api/status").json()["state"] == "never"
    refresh(client)
    assert client.get("/api/status").json()["state"] == "fresh"
    monkeypatch.setattr(main.config, "STALE_AFTER_MIN", -1)
    assert client.get("/api/status").json()["state"] == "stale"
    n = client.get("/api/notices").json()["notices"][0]
    assert n["verification"]["code"] == "stale"


def test_each_notice_links_to_official_announcement(api):
    client, _ = api
    refresh(client)
    for n in client.get("/api/notices", params={"include_withdrawn": True}).json()["notices"]:
        r = client.get(n["url"].replace("http://testserver", ""))
        assert r.status_code == 200


def test_no_dead_ends_closest_matches_are_returned_with_explanation(api):
    client, _ = api
    refresh(client)
    data = client.get("/api/notices", params={"level": "Doctoral", "category": "Need-based"}).json()
    assert data["exact"] is False and data["count"] > 0
    assert data["relaxed"] and any("funding type" in r or "study level" in r for r in data["relaxed"])
    exact = client.get("/api/notices", params={"level": "Doctoral"}).json()
    assert exact["exact"] is True and exact["relaxed"] == []


def test_closed_only_matches_are_offered_before_dropping_filters(api):
    client, _ = api
    refresh(client)
    client.post("/api/operator/demo", headers=H,
                json={"action": "set_deadline", "key": "ironwood-school", "text": "1 Jan 2020"})
    refresh(client)
    data = client.get("/api/notices", params={"level": "School"}).json()
    assert data["exact"] is False
    assert data["relaxed"] == ["also showing applications that have closed for this cycle"]
    assert [n["deadline_state"] for n in data["notices"]] == ["closed"]


def test_filter_options_only_list_values_that_exist_with_counts(api):
    client, _ = api
    refresh(client)
    f = client.get("/api/filters").json()
    levels = {x["value"]: x for x in f["levels"]}
    assert "Undergraduate" in levels and levels["Undergraduate"]["open"] >= 1
    assert all(x["total"] >= 1 for group in f.values() for x in group)
    order = [x["value"] for x in f["levels"]]
    assert order.index("School") < order.index("Undergraduate") < order.index("Doctoral")


def test_disabled_sources_are_hidden_everywhere(api):
    client, main = api
    refresh(client)
    conn = main.connect(main.config.DB_PATH)
    conn.execute("UPDATE sources SET enabled = 0 WHERE source_id = 'demo-portal-b'")
    names = {n["source_name"] for n in client.get("/api/notices", params={"include_closed": True}).json()["notices"]}
    assert names == {"Demo National Scholarship Portal (table layout)"}
    assert [s["name"] for s in client.get("/api/sources").json()["sources"]] == list(names)


def test_suggestions_only_offer_choices_that_have_results(api):
    client, _ = api
    refresh(client)
    data = client.get("/api/suggestions").json()
    assert data["searches"] and data["providers"]
    for item in data["searches"] + data["providers"]:
        found = client.get("/api/notices", params={"q": item["q"]}).json()
        assert found["exact"] and found["count"] == item["count"] > 0


def test_status_explains_unreachable_sources_and_retry_time(api):
    client, _ = api
    client.post("/api/operator/demo", headers=H, json={"action": "set_mode", "portal": "a", "mode": "down"})
    client.post("/api/operator/demo", headers=H, json={"action": "set_mode", "portal": "b", "mode": "down"})
    refresh(client)
    s = client.get("/api/status").json()
    assert s["state"] == "degraded" and s["all_failing"] and s["notice_count"] == 0
    assert s["retry_in_min"] == 2 and "Can't reach 2 of 2" in s["label"]


def test_public_retry_is_rate_limited(api):
    client, _ = api
    first = client.post("/api/retry").json()
    second = client.post("/api/retry").json()
    assert first["started"] is True and second["started"] is False
