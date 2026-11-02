import httpx
import pytest

from app.fetcher import FetchError, Fetcher

UA = "TestBot/1.0"


def make(handler, **kw):
    sleeps = []
    f = Fetcher(UA, client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=1.0, min_interval=0.0,
                sleep=sleeps.append, **kw)
    return f, sleeps


def ok_robots(request):
    return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")


def test_robots_disallow_is_respected_and_reported():
    def handler(req):
        return ok_robots(req) if req.url.path == "/robots.txt" else httpx.Response(200, text="<html></html>")
    f, _ = make(handler)
    with pytest.raises(FetchError) as e:
        f.get("http://site.test/private/list")
    assert e.value.kind == "robots_disallowed"
    assert f.get("http://site.test/public").status == 200


def test_robots_403_means_disallow_all_and_5xx_means_unavailable():
    f, _ = make(lambda r: httpx.Response(403))
    with pytest.raises(FetchError) as e:
        f.get("http://site.test/x")
    assert e.value.kind == "robots_disallowed"
    f2, _ = make(lambda r: httpx.Response(500))
    with pytest.raises(FetchError) as e2:
        f2.get("http://site.test/x")
    assert e2.value.kind == "robots_unavailable"


def test_retries_with_exponential_backoff_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="<html>hi</html>")
    f, sleeps = make(handler)
    res = f.get("http://site.test/list")
    assert res.attempts == 3 and sleeps == [1.0, 2.0]


def test_retry_ceiling_then_failure_reports_attempts():
    f, sleeps = make(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(503))
    with pytest.raises(FetchError) as e:
        f.get("http://site.test/list")
    assert e.value.status == 503 and e.value.attempts == 3 and len(sleeps) == 2


def test_client_errors_are_not_retried():
    seen = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(1)
        return httpx.Response(404)
    f, _ = make(handler)
    with pytest.raises(FetchError):
        f.get("http://site.test/gone")
    assert len(seen) == 1


def test_timeout_is_retried_and_classified():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("slow", request=req)
    f, _ = make(handler)
    with pytest.raises(FetchError) as e:
        f.get("http://site.test/list")
    assert e.value.kind == "timeout" and e.value.attempts == 3


def test_rate_limit_waits_between_requests_to_same_host():
    now = {"t": 100.0}
    waits = []
    f = Fetcher(UA, client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(200, text="x"))),
        min_interval=2.0, sleep=lambda s: (waits.append(s), now.__setitem__("t", now["t"] + s)),
        clock=lambda: now["t"])
    f.get("http://site.test/a")
    f.get("http://site.test/b")
    assert waits and waits[0] == pytest.approx(2.0)
