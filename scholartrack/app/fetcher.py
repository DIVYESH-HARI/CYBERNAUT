"""Polite HTTP fetching: robots.txt, per-host rate limit, timeout, bounded retry with backoff."""
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("scholartrack.fetch")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """kind: robots_disallowed | robots_unavailable | http_error | timeout | network | offsite_redirect | bad_content"""

    def __init__(self, kind: str, message: str, status: int | None = None, attempts: int = 0):
        super().__init__(message)
        self.kind, self.status, self.attempts = kind, status, attempts


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    attempts: int


class Fetcher:
    def __init__(self, user_agent: str, client: httpx.Client | None = None, timeout: float = 10.0,
                 max_retries: int = 3, backoff: float = 1.0, min_interval: float = 2.0,
                 max_bytes: int = 2_000_000, sleep=time.sleep, clock=time.monotonic):
        self.user_agent = user_agent
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": user_agent})
        self.max_retries, self.backoff, self.min_interval = max_retries, backoff, min_interval
        self.max_bytes, self._sleep, self._clock = max_bytes, sleep, clock
        self._robots: dict[str, tuple[RobotFileParser | None, bool]] = {}  # origin -> (parser, allow_all)
        self._last_hit: dict[str, float] = {}

    # ---- robots.txt
    def _robots_for(self, url: str):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        try:
            resp = self.client.get(origin + "/robots.txt", headers={"User-Agent": self.user_agent})
        except httpx.ConnectError as exc:
            raise FetchError("network", f"cannot reach {parts.netloc} - check the internet connection ({exc})") from exc
        except httpx.HTTPError as exc:
            raise FetchError("robots_unavailable", f"could not read robots.txt: {exc}") from exc
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
            entry = (parser, False)
        elif resp.status_code in (401, 403):
            entry = (None, False)       # access denied -> treat as disallow all
        elif resp.status_code >= 500:
            raise FetchError("robots_unavailable", f"robots.txt returned {resp.status_code}", resp.status_code)
        else:
            entry = (None, True)        # 404 etc. -> no rules published
        self._robots[origin] = entry
        return entry

    def allowed(self, url: str) -> bool:
        parser, allow_all = self._robots_for(url)
        if allow_all:
            return True
        if parser is None:
            return False
        return parser.can_fetch(self.user_agent, url)

    def _throttle(self, url: str) -> None:
        parts = urlsplit(url)
        host = parts.netloc
        if parts.hostname in ("127.0.0.1", "localhost", "testserver"):
            return                      # our own built-in demo site: no need to throttle
        delay = self.min_interval
        parser, _ = self._robots.get(f"{parts.scheme}://{host}", (None, True))
        if parser is not None:
            crawl = parser.crawl_delay(self.user_agent)
            if crawl:
                delay = max(delay, min(float(crawl), 30.0))
        wait = self._last_hit.get(host, -1e9) + delay - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_hit[host] = self._clock()

    # ---- main entry
    def get(self, url: str) -> FetchResult:
        if not self.allowed(url):
            raise FetchError("robots_disallowed", f"robots.txt disallows {url}")
        host = urlsplit(url).netloc
        last_error: FetchError | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle(url)
            try:
                resp = self.client.get(url, headers={"User-Agent": self.user_agent}, follow_redirects=True)
            except httpx.TimeoutException as exc:
                last_error = FetchError("timeout", f"timeout fetching {url}: {exc}", attempts=attempt)
            except httpx.HTTPError as exc:
                last_error = FetchError("network", f"network error fetching {url}: {exc}", attempts=attempt)
            else:
                if urlsplit(str(resp.url)).netloc != host:
                    raise FetchError("offsite_redirect", f"{url} redirected to {resp.url}", attempts=attempt)
                if resp.status_code == 200:
                    ctype = resp.headers.get("content-type", "text/html").lower()
                    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                        raise FetchError("bad_content", f"unexpected content-type {ctype}", 200, attempt)
                    if len(resp.content) > self.max_bytes:
                        raise FetchError("bad_content", "response too large", 200, attempt)
                    return FetchResult(str(resp.url), 200, resp.text, attempt)
                last_error = FetchError("http_error", f"HTTP {resp.status_code} for {url}", resp.status_code, attempt)
                if resp.status_code not in RETRYABLE_STATUS:
                    raise last_error            # 403/404 etc.: do not hammer
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    self._sleep(min(int(retry_after), 30))
            if attempt < self.max_retries:
                pause = self.backoff * (2 ** (attempt - 1))
                log.warning("fetch attempt %d/%d failed (%s); retrying in %.1fs",
                            attempt, self.max_retries, last_error, pause)
                self._sleep(pause)
        assert last_error is not None
        raise last_error
