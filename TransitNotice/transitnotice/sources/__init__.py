"""Builds the enabled source adapters from agencies.json (TN_SOURCES can override which keys are enabled)."""
import json
from pathlib import Path

from ..config import ROOT
from .cmrl import CmrlAdapter
from .cmrl_press import CmrlPressAdapter
from .feed import FeedAdapter
from .mbta import MbtaAdapter
from .sample_metro import SampleMetroAdapter

REGISTRY = ROOT / "agencies.json"


def load_registry(path=REGISTRY):
    return json.loads(Path(path).read_text(encoding="utf-8"))["agencies"]


def _make(entry, config):
    kind = entry["adapter"]
    if kind == "cmrl":
        ad = CmrlAdapter(cache_path=Path(config.db_path).parent / "cmrl_detail_cache.json"
                         if config.db_path != ":memory:" else None)
    elif kind == "cmrl_press":
        ad = CmrlPressAdapter()
    elif kind == "sample_metro":
        ad = SampleMetroAdapter(config.demo_base_url)
    elif kind == "mbta":
        ad = MbtaAdapter()
    elif kind == "rss":
        ad = FeedAdapter(entry["key"], entry["agency"], entry["url"].replace("{demo_base_url}", config.demo_base_url),
                         entry.get("operator_type", "Government"), entry.get("timezone", "Asia/Kolkata"),
                         int(entry.get("refresh_seconds", 1800)), entry.get("area", ""), entry.get("type_hint", ""))
    else:
        raise ValueError("unknown adapter %r in agencies.json" % kind)
    ad.operator_type = entry.get("operator_type", getattr(ad, "operator_type", "Government"))
    return ad


def build_adapters(config, registry=None):
    entries = registry if registry is not None else load_registry()
    if config.sources is not None:
        wanted = set(config.sources)
        entries = [e for e in entries if e["key"] in wanted]
    else:
        entries = [e for e in entries if e.get("enabled", True)]
    return [_make(e, config) for e in entries]
