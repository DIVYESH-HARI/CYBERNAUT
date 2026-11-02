"""Polite HTTP fetching (robots.txt, rate limit, timeout, retry with exponential backoff) and adapter types."""
import logging
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

log = logging.getLogger("transitnotice.fetch")


class FetchError(Exception):
    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


class ParseError(Exception):
    """The page/feed no longer looks like what the adapter expects (layout change, error page...)."""


class RobotsDenied(FetchError):
    pass


@dataclass
class ParsedNotice:
    external_id: str
    summary: str
    type: str = "other"
    route_ids: list = field(default_factory=list)
    stations: list = field(default_factory=list)
    area: str = ""
    published_at: str = None      # canonical UTC ISO
    valid_from: str = None
    valid_to: str = None
    source_url: str = ""
    source_status: str = "active"  # active | cancelled | withdrawn (as stated by the source)


@dataclass
class FetchResult:
    notices: list
    skipped: int = 0
    diagnostics: list = field(default_factory=list)
    http_status: int = 200
    unparsed_ids: list = field(default_factory=list)   # listed by the source but not parsable: never withdraw these


class Fetcher:
    def __init__(self, config, sleep=time.sleep, opener=None):
        self.cfg = config
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self._robots = {}
        self._last_hit = {}

    # -- robots.txt ---------------------------------------------------------
    def _robots_for(self, url):
        parts = urlsplit(url)
        origin = "%s://%s" % (parts.scheme, parts.netloc)
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                status, body, _ = self._raw_get(origin + "/robots.txt")
                rp.parse(body.decode("utf-8", "replace").splitlines())
            except FetchError as e:
                if e.http_status == 404:
                    rp.parse([])                       # no robots.txt -> everything allowed
                elif e.http_status in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.disallow_all = True             # cannot read policy -> be conservative
                    log.warning("robots.txt unreadable for %s: %s", origin, e)
            self._robots[origin] = rp
        return self._robots[origin]

    # -- low level ----------------------------------------------------------
    def _throttle(self, host):
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.cfg.min_request_interval - (time.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _raw_get(self, url, timeout=None):
        self._throttle(urlsplit(url).netloc)
        req = urllib.request.Request(url, headers={"User-Agent": self.cfg.user_agent_str, "Accept": "*/*"})
        try:
            with self.opener(req, timeout=timeout or self.cfg.http_timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            raise FetchError("HTTP %s for %s" % (e.code, url), e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError("network error for %s: %s" % (url, e)) from e

    # -- public -------------------------------------------------------------
    def get(self, url, timeout=None):
        """GET with robots check, retries (ceiling cfg.max_attempts) and exponential backoff."""
        if not self._robots_for(url).can_fetch(self.cfg.user_agent_str, url):
            raise RobotsDenied("robots.txt disallows %s" % url)
        last = None
        for attempt in range(1, self.cfg.max_attempts + 1):
            try:
                return self._raw_get(url, timeout)
            except FetchError as e:
                last = e
                retryable = e.http_status is None or e.http_status == 429 or e.http_status >= 500
                log.warning("fetch attempt %d/%d failed: %s", attempt, self.cfg.max_attempts, e)
                if not retryable or attempt == self.cfg.max_attempts:
                    break
                self.sleep(self.cfg.backoff_base * (2 ** (attempt - 1)))
        raise last
