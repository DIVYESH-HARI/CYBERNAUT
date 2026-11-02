"""Command line entry point.

  python run.py serve [--port 5000]   web UI + background scheduler
  python run.py refresh [source_key]  one manual refresh of all (or one) sources, then exit
  python run.py demo <cmd>            change the fictional Sample Metro page (see transitnotice/demo_tools.py)
"""
import sys

from transitnotice import demo_tools
from transitnotice.config import Config


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "demo":
        return demo_tools.command(argv[2:])
    cfg = Config()
    if cmd == "refresh":
        from transitnotice.alerts import Alerter
        from transitnotice.db import open_db
        from transitnotice.scheduler import run_due
        from transitnotice.sources import build_adapters
        from transitnotice.sources.base import Fetcher
        import logging
        logging.basicConfig(level=logging.INFO)
        ads = [a for a in build_adapters(cfg) if len(argv) < 3 or a.source_key == argv[2]]
        for k, v in run_due(open_db(cfg.db_path), cfg, ads, Fetcher(cfg), Alerter(cfg), force=True).items():
            print(k, v)
        return
    if cmd == "serve":
        from transitnotice.app import create_app
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 5000
        cfg.demo_base_url = "http://127.0.0.1:%d" % port
        if not demo_tools.PAGE.exists() or not demo_tools.PRIVATE_FEED.exists():
            demo_tools.command(["reset"])
        create_app(cfg).run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
