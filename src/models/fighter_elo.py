"""
fighter_elo.py — a simple global Elo for combat sports (boxing, MMA/UFC).

Combat is head-to-head with no score, so the natural fair line is an Elo win
probability (like tennis, minus the surface). We update ratings fight by fight in
chronological order. Input DataFrame columns: winner, loser (+ optional date).
"""
import pandas as pd

BASE = 1500.0
K = 24.0


def build_elo(df, k=K, base=BASE):
    if df is None or not len(df):
        return None
    if "date" in df.columns:
        df = df.sort_values("date")
    r = {}; nfights = {}
    for _, row in df.iterrows():
        w, l = row.get("winner"), row.get("loser")
        if not isinstance(w, str) or not isinstance(l, str):
            continue
        rw, rl = r.get(w, base), r.get(l, base)
        ew = 1.0 / (1.0 + 10 ** ((rl - rw) / 400.0))
        r[w] = rw + k * (1.0 - ew)
        r[l] = rl + k * (0.0 - (1.0 - ew))
        nfights[w] = nfights.get(w, 0) + 1; nfights[l] = nfights.get(l, 0) + 1
    return {"r": r, "base": base, "fighters": set(r), "n": nfights}


def predict(model, a, b, shrink=True):
    ra = model["r"].get(a, model["base"])
    rb = model["r"].get(b, model["base"])
    p = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
    if shrink:
        n = model.get("n", {})
        rel = min(1.0, min(n.get(a, 0), n.get(b, 0)) / 10.0)   # need ~10 fights for full confidence
        p = 0.5 + (p - 0.5) * rel                              # thin data -> pulled toward 50%
    return p


if __name__ == "__main__":
    # self-test: a clearly stronger fighter should get a high win probability
    rows = []
    for _ in range(30):
        rows.append({"winner": "Strong Fighter", "loser": "Weak Fighter", "date": "2024-01-01"})
    for i in range(10):
        rows.append({"winner": "Mid A", "loser": "Mid B", "date": "2024-02-01"} if i % 2 else
                    {"winner": "Mid B", "loser": "Mid A", "date": "2024-02-01"})
    m = build_elo(pd.DataFrame(rows))
    print("ratings:", {k: round(v) for k, v in m["r"].items()})
    print("P(Strong beats Weak):", round(predict(m, "Strong Fighter", "Weak Fighter"), 3))
    print("P(Mid A beats Mid B):", round(predict(m, "Mid A", "Mid B"), 3))
    print("P(unknown vs Strong):", round(predict(m, "Newcomer", "Strong Fighter"), 3))
