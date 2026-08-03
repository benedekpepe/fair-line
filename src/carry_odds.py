"""
carry_odds.py — on free (--no-odds) days, copy the market odds from the last
published data.js onto today's fresh (model-only) fixtures, so the odds fetched
on the weekly paid run stay visible all week without spending quota.

The weekly paid run (Mondays / forced) writes real odds. On the other days the
pipeline rebuilds fixtures from free ESPN data (no odds). This script pairs each
fresh fixture with the same game in last week's published data.js and copies:
  * top-level mkt / mkt_ou / mkt_ah   (football 1X2 / over-under / asian handicap)
  * per-outcome 'mkt' in base/extra   (moneyline / match winner, by outcome label)

Pairing is order-independent on {normalised home, away} + kickoff date, so a game
that is still upcoming keeps its odds; games that already started drop off (they
are no longer in today's fixtures) and brand-new games show model-only until the
next paid run prices them.

Usage:  python carry_odds.py <prev_data.js>     # current file = config.DATA_JS
"""
import sys, re, json, unicodedata
from config import DATA_JS


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse(s):
    return json.loads(s[s.find("{"):s.rfind("}") + 1])


def _key(m):
    return (frozenset((_norm(m.get("home")), _norm(m.get("away")))), (m.get("kickoff") or "")[:10])


def _index(prev):
    idx = {}
    for blk in prev.values():
        if isinstance(blk, dict):
            for m in blk.get("matches", []):
                idx[_key(m)] = m
    return idx


def _carry_outs(new_groups, old_groups):
    """Copy 'mkt' onto each new outcome from the same-named group + same label in old."""
    old_by_name = {g.get("name"): g for g in (old_groups or [])}
    for g in new_groups or []:
        og = old_by_name.get(g.get("name"))
        if not og:
            continue
        old_mkt = {_norm(o.get("k")): o.get("mkt") for o in og.get("outs", []) if o.get("mkt") is not None}
        for o in g.get("outs", []):
            if o.get("mkt") is None and _norm(o.get("k")) in old_mkt:
                o["mkt"] = old_mkt[_norm(o.get("k"))]


def carry(prev, cur):
    idx = _index(prev)
    n = 0
    for blk in cur.values():
        if not isinstance(blk, dict):
            continue
        for m in blk.get("matches", []):
            old = idx.get(_key(m))
            if not old:
                continue
            before = json.dumps(m, sort_keys=True)
            for f in ("mkt", "mkt_ou", "mkt_ah"):
                if m.get(f) is None and old.get(f) is not None:
                    m[f] = old[f]
            _carry_outs(m.get("base"), old.get("base"))
            _carry_outs(m.get("extra"), old.get("extra"))
            if json.dumps(m, sort_keys=True) != before:
                n += 1
    return n


def main():
    if len(sys.argv) < 2:
        print("  carry_odds: no previous data.js path given — skipped."); return
    try:
        prev = _parse(open(sys.argv[1], encoding="utf-8").read())
    except Exception as e:
        print(f"  carry_odds: could not read previous data.js ({str(e)[:60]}) — skipped."); return
    if not DATA_JS.exists():
        print("  carry_odds: current data.js missing — skipped."); return
    cur = _parse(DATA_JS.read_text(encoding="utf-8"))
    n = carry(prev, cur)
    DATA_JS.write_text("window.SPORTS_DATA = " + json.dumps(cur, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print(f"  carry_odds: carried last week's odds onto {n} of today's fixtures.")


if __name__ == "__main__":
    main()
