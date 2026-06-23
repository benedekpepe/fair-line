"""
tennis_elo.py — surface-aware (clay) Elo for ATP/WTA data.
General Elo (all matches) + clay-specific Elo (clay matches only).
When predicting a clay match, the two are blended.
"""
import pandas as pd

def build_elo(df, k=32, clay_weight=0.7):
    df = df.copy()
    df["tourney_date"] = pd.to_datetime(df["tourney_date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["winner_name", "loser_name", "tourney_date"]).sort_values("tourney_date")
    gen, clay = {}, {}      # general and clay Elo
    n_clay = {}             # number of clay matches (for reliability)
    def g(d, p): return d.get(p, 1500.0)
    for _, r in df.iterrows():
        w, l = r["winner_name"], r["loser_name"]
        # general
        ew = 1 / (1 + 10 ** ((g(gen, l) - g(gen, w)) / 400))
        gen[w] = g(gen, w) + k * (1 - ew); gen[l] = g(gen, l) - k * (1 - ew)
        # clay
        if r["surface"] == "Clay":
            ewc = 1 / (1 + 10 ** ((g(clay, l) - g(clay, w)) / 400))
            clay[w] = g(clay, w) + k * (1 - ewc); clay[l] = g(clay, l) - k * (1 - ewc)
            n_clay[w] = n_clay.get(w, 0) + 1; n_clay[l] = n_clay.get(l, 0) + 1
    return {"gen": gen, "clay": clay, "n_clay": n_clay, "clay_weight": clay_weight}

def clay_rating(model, p):
    gen = model["gen"].get(p, 1500.0); clay = model["clay"].get(p, 1500.0)
    nc = model["n_clay"].get(p, 0)
    # if there are few clay matches, lean towards the general rating
    w = model["clay_weight"] * min(1.0, nc / 20.0)
    return w * clay + (1 - w) * gen

def predict_clay(model, p1, p2):
    r1, r2 = clay_rating(model, p1), clay_rating(model, p2)
    p = 1 / (1 + 10 ** ((r2 - r1) / 400))
    return p

if __name__ == "__main__":
    df = pd.read_csv("atp_all.csv")
    m = build_elo(df)
    # top clay players (at least 15 clay matches)
    rows = [(p, clay_rating(m, p), m["n_clay"].get(p, 0)) for p in m["gen"]]
    rows = [r for r in rows if r[2] >= 15]
    rows.sort(key=lambda x: -x[1])
    print("Top 12 clay Elo (min. 15 clay matches):")
    for p, r, nc in rows[:12]:
        print(f"  {p:<22} {r:7.1f}  ({nc} clay matches)")
    print("\nExample predictions (clay):")
    for a, b in [("Carlos Alcaraz", "Jannik Sinner"), ("Carlos Alcaraz", "Alexander Zverev"), ("Novak Djokovic", "Casper Ruud")]:
        p = predict_clay(m, a, b)
        print(f"  {a} vs {b}: {p*100:.1f}% / {(1-p)*100:.1f}%")
