"""
prune_past.py — drop matches that are no longer upcoming, so the board never
accumulates stale fixtures.

The exporters preserve each sport's previous matches (so a failed refresh doesn't
blank a sport), which means finished games can linger — most visibly the World
Cup 2026 group games, which have no kickoff timestamp to filter on. This trims:
  * any match whose kickoff is in the past (reliable, ISO timestamp), and
  * any match from a finished tournament listed in DONE_LEAGUES (kickoff-less).

Run after the pipeline, before publishing. Reads/writes config.DATA_JS.
"""
import json
from datetime import datetime, timezone, timedelta
from config import DATA_JS

# Tournaments that have ended; their kickoff-less fixtures would otherwise linger.
DONE_LEAGUES = {"World Cup 2026"}

# Keep today's games; drop anything that kicked off before yesterday.
CUTOFF = datetime.now(timezone.utc) - timedelta(days=1)


def _parse_kickoff(k):
    if not k:
        return None
    try:
        return datetime.fromisoformat(str(k).replace("Z", "+00:00"))
    except Exception:
        return None


def _keep(m):
    if (m.get("league") or "") in DONE_LEAGUES:
        return False
    ko = _parse_kickoff(m.get("kickoff"))
    if ko is not None and ko < CUTOFF:
        return False
    return True


def main():
    if not DATA_JS.exists():
        print("  prune_past: no data.js — skipped."); return
    s = DATA_JS.read_text(encoding="utf-8")
    d = json.loads(s[s.find("{"):s.rfind("}") + 1])
    dropped = 0
    for blk in d.values():
        if not isinstance(blk, dict):
            continue
        ms = blk.get("matches", [])
        kept = [m for m in ms if _keep(m)]
        dropped += len(ms) - len(kept)
        blk["matches"] = kept
    DATA_JS.write_text("window.SPORTS_DATA = " + json.dumps(d, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print(f"  prune_past: dropped {dropped} finished/stale match(es).")


if __name__ == "__main__":
    main()
