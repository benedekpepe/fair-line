"""
run_all.py — ONE command to refresh every source and push to Supabase once.

Order:
  1) export_all.py        -> World Cup (martj42/API)  -> web/data.js
  2) export_club_auto.py  -> 11 club/continental leagues (football-data.org) merged into web/data.js
  3) read the complete web/data.js and push it to Supabase ONCE (full refresh)

Because the Supabase push is a full refresh (delete + insert), every sport must
be pushed together — that is exactly what this runner does, so the club leagues
are never wiped by a partial, single-sport push.

Environment variables required:
  FOOTBALL_DATA_KEY     football-data.org free key
  SUPABASE_URL          https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  the secret key (sb_secret_... ; legacy service_role also works)
"""
import subprocess, sys, json, os
from pathlib import Path

SRC = Path(__file__).resolve().parent
PROJECT = SRC.parent
sys.path.insert(0, str(SRC))
try:
    from config import load_env; load_env()   # load project-root .env into os.environ
except Exception:
    pass

NO_ODDS = "--no-odds" in sys.argv   # run the free parts only (no The Odds API calls -> 0 credits)


def run(module):
    """Run one exporter as a package module: `python -m exporters.<module>`,
    with src/ on PYTHONPATH so `from models import ...`, `from sources import ...`
    and `from config import ...` all resolve."""
    print(f"\n=== {module}.py ===")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    if NO_ODDS:
        env["FAIRLINE_NO_ODDS"] = "1"
    subprocess.run([sys.executable, "-m", f"exporters.{module}"], check=True, cwd=str(SRC), env=env)


def main():
    if NO_ODDS:
        print("### --no-odds mode: skipping all The Odds API calls (0 credits). "
              "Existing odds data is preserved. ###")
    run("export_all")        # World Cup (football) -> web/data.js  (push skipped here)
    run("export_club_auto")  # + club leagues (football-data.org fixtures) merged in
    run("export_odds_leagues")  # + summer leagues (The Odds API fixtures+odds) merged in
    run("export_tennis_odds")   # + tennis (The Odds API fixtures+odds, Elo fair line)
    run("export_margin_odds")   # + margin sports (basketball, AFL, NRL, MLB, NBA, NFL, NHL)
    run("export_fights_odds")   # + combat sports (UFC/boxing: ESPN history, fighter Elo, h2h odds)

    djs = PROJECT / "web" / "data.js"
    if not djs.exists():
        print("data.js not found — nothing to push."); return
    s = djs.read_text(encoding="utf-8")
    data = json.loads(s[s.find("{"):s.rfind("}") + 1])

    total = sum(len(blk.get("matches", [])) for blk in data.values())
    sys.path.insert(0, str(SRC))
    if not NO_ODDS:
        from sources import odds
        odds.attach(data)   # real market 1X2 odds (The Odds API) -> value signal
    else:
        print("\n=== odds.attach skipped (--no-odds) ===")
    print(f"\n=== push to Supabase (full refresh): {total} matches across {len(data)} sports ===")
    import sync_supabase
    sync_supabase.push(data)
    print("\nDone.")


if __name__ == "__main__":
    main()
