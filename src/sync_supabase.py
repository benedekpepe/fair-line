"""
sync_supabase.py — push the model output into Supabase via the REST API.

Uses only the Python standard library (urllib) — no 'supabase' package, so it
works on any Python without compilers or heavy dependencies.

Writes two tables (full refresh: delete + insert):
  - matches        (FREE tier: card + base market / headline odds)
  - match_details  (PAID tier: expected goals, extra markets, structured insight)

Football base (1X2) is computed here from the expected goals, so free users get
the headline odds without receiving the paid model internals.

Server-side ONLY. Uses the SERVICE (secret) key — never expose it in the frontend.

Environment variables required:
  SUPABASE_URL          e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  the secret key (sb_secret_... ; legacy service_role also works)
"""
import os, math, json, urllib.request, urllib.error


def _poisson_1x2(lam, mu, maxg=10):
    """Plain Poisson scoreline matrix -> (home, draw, away) probabilities."""
    ph = [math.exp(-lam) * lam ** i / math.factorial(i) for i in range(maxg + 1)]
    pa = [math.exp(-mu) * mu ** j / math.factorial(j) for j in range(maxg + 1)]
    home = draw = away = 0.0
    for i in range(maxg + 1):
        for j in range(maxg + 1):
            p = ph[i] * pa[j]
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    s = home + draw + away or 1.0
    return home / s, draw / s, away / s


def _base_for(m):
    """Base market for a match. Non-football already has it; football is computed."""
    if m.get("base"):
        return m["base"]
    h, d, a = _poisson_1x2(m["lam"], m["mu"])
    return [{"name": "Match result (1X2)", "grid": "c3", "outs": [
        {"k": "Home (1)", "p": round(h, 3)},
        {"k": "Draw (X)", "p": round(d, 3)},
        {"k": "Away (2)", "p": round(a, 3)}]}]


def _req(method, url, key, body=None, prefer=None):
    """Minimal PostgREST call via urllib. Raises with the response body on error."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode("utf-8")
            return json.loads(txt) if txt.strip() else []
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {msg[:200]}")


def push(data):
    """Full refresh: replace all rows with the current model output."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("  Supabase env not set (SUPABASE_URL / SUPABASE_SERVICE_KEY) — push skipped.")
        return
    base = url.rstrip("/") + "/rest/v1"

    # build the new rows FIRST (so we can compare against what's live before deleting anything)
    match_rows, meta = [], []
    for sport_key, blk in data.items():
        for m in blk["matches"]:
            match_rows.append({
                "sport": sport_key, "sport_label": blk["label"], "league": m["league"],
                "match_date": m.get("date"), "kickoff": m.get("kickoff"),
                "mkt": m.get("mkt"), "mkt_ou": m.get("mkt_ou"), "mkt_ah": m.get("mkt_ah"),
                "home": m["home"], "away": m["away"],
                "base": _base_for(m)})
            if m.get("lam") is not None:                      # football
                ivars = {"lam": f'{m["lam"]:.2f}', "mu": f'{m["mu"]:.2f}'}
                if m.get("md") is not None:                   # club league round
                    ikey = "round"; ivars = {**ivars, "md": str(m["md"])}
                else:                                         # World Cup group stage
                    ikey = "group_stage"
                det = {"lam": m["lam"], "mu": m["mu"], "extra": None,
                       "insight_key": ikey, "insight_vars": ivars}
            else:                                             # tennis / margin / combat / other
                ikey = "tennis" if sport_key == "tenisz" else None
                det = {"lam": None, "mu": None, "extra": m.get("extra"),
                       "insight_key": ikey, "insight_vars": {}}
            meta.append(det)

    if not match_rows:
        print("  nothing to push."); return

    # shrink-guard: a quota/odds failure can leave the new set far smaller than what's live.
    # Refuse a destructive full refresh in that case so the dashboard keeps its last good data.
    try:
        current = _req("GET", f"{base}/matches?select=id", key) or []
        cur_n = len(current)
    except Exception:
        cur_n = 0
    if cur_n >= 20 and len(match_rows) < 0.5 * cur_n:
        print(f"  Supabase: refusing full refresh — new set ({len(match_rows)}) is far smaller than "
              f"current ({cur_n}); kept existing data (likely an odds/quota failure).")
        return

    # full refresh (match_details first because it references matches)
    _req("DELETE", f"{base}/match_details?match_id=neq.-1", key)
    _req("DELETE", f"{base}/matches?id=neq.-1", key)

    inserted = _req("POST", f"{base}/matches", key, body=match_rows, prefer="return=representation")
    ids = [row["id"] for row in inserted]                     # same order as inserted
    detail_rows = []
    for mid, det in zip(ids, meta):
        if det:
            row = {"match_id": mid}
            row.update(det)
            detail_rows.append(row)
    if detail_rows:
        _req("POST", f"{base}/match_details", key, body=detail_rows, prefer="return=minimal")
    print(f"  Supabase: pushed {len(match_rows)} matches, {len(detail_rows)} details.")
