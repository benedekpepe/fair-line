"""
config.py — project paths + a tiny zero-dependency .env loader.

ONE place that knows where the project lives, so no module has to compute
fragile __file__-relative paths. Everything imports the paths it needs from here:

    from config import load_env, PROJECT, RAW, DATA_JS
    load_env()                       # load the project-root .env into os.environ

config.py always sits in src/, so PROJECT is reliably its grandparent — no matter
how deep the importing module lives (models/, sources/, exporters/, backtests/).
"""
import os
from pathlib import Path

SRC = Path(__file__).resolve().parent       # .../fair-line/src
PROJECT = SRC.parent                          # .../fair-line
RAW = PROJECT / "data" / "raw"                # cached historical CSVs
WEB = PROJECT / "web"                         # static frontend
DATA_JS = WEB / "data.js"                     # the generated data the frontend reads
ENV_FILE = PROJECT / ".env"


def load_env(path=None):
    """Read KEY=VALUE lines from the project-root .env into os.environ WITHOUT
    overriding variables already set in the shell. No external package needed."""
    p = Path(path) if path else ENV_FILE
    try:
        text = p.read_text(encoding="utf-8-sig")
    except Exception:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)
