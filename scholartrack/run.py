"""Start ScholarTrack:  python run.py   (then open http://127.0.0.1:8000)"""
import json
import logging
import os
import socket
import sys
import urllib.request
import webbrowser

HOST = "127.0.0.1"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) != 0


def scholartrack_on(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=3) as r:
            return "data_version" in json.loads(r.read())
    except Exception:
        return False


def choose_port() -> int | None:
    """8000 if free. If ScholarTrack already runs there, None (just open it). Else the next free port."""
    if port_free(8000):
        return 8000
    if scholartrack_on(8000):
        return None
    return next((p for p in range(8001, 8021) if port_free(p)), None)


if __name__ == "__main__":
    if "SCHOLARTRACK_BASE_URL" not in os.environ:
        port = choose_port()
        if port is None:
            url = f"http://{HOST}:8000"
            print(f"\n  ScholarTrack is already running at {url} - opening it in your browser.")
            print("  To restart it with new code, close the other ScholarTrack window first, then run this again.\n")
            webbrowser.open(url)
            sys.exit(0)
        if port != 8000:
            print(f"\n  Port 8000 is used by another program - using port {port} instead.")
        os.environ["SCHOLARTRACK_BASE_URL"] = f"http://{HOST}:{port}"

    import uvicorn                     # imported after the port is chosen: app.config reads the URL at import
    from app import config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"\n  ScholarTrack  ->  {config.BASE_URL}")
    print(f"  Operator token: {config.operator_token()}   (paste it in the Operator tab)\n")
    port = int(config.BASE_URL.rsplit(":", 1)[-1])
    webbrowser.open(config.BASE_URL)
    uvicorn.run("app.main:app", host=HOST, port=port, log_level="info")
