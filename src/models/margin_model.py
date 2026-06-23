"""
margin_model.py — a general scoring-margin model for high-scoring sports
(basketball: NBA/WNBA/NCAAB/EuroLeague, gridiron: NFL/NCAAF, AFL, NRL...).

Unlike low-scoring goal sports (soccer, hockey) where Poisson fits, high-scoring
sports are well approximated by a NORMAL distribution on the scoring margin and
total. We fit a two-way offense/defense rating by ridge least squares:

    home_points ≈ base + hfa + off[home] + def[away]
    away_points ≈ base       + off[away] + def[home]

From a fitted model we get, for any matchup, an expected margin and total (with
residual sigmas), which give probabilities for the three featured markets:
    * moneyline  (margin > 0)
    * spread     (margin + line > 0)        — any handicap line
    * total      (total  > line)            — any over/under line

No SciPy dependency (normal CDF via math.erf). Input DataFrame columns:
    home, away, home_score, away_score   (one row per game)
"""
import math
import numpy as np
import pandas as pd

SQRT2 = math.sqrt(2.0)


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def fit_margin(df, ridge=4.0):
    df = df.dropna(subset=["home", "away", "home_score", "away_score"]).copy()
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"])
    teams = sorted(set(df["home"]) | set(df["away"]))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    if n < 2 or len(df) < n:
        return None
    # params: [base, hfa, off_0..off_{n-1}, def_0..def_{n-1}]
    P = 2 + 2 * n
    rows, y = [], []
    for _, r in df.iterrows():
        h, a = idx[r["home"]], idx[r["away"]]
        # home points scored
        v = np.zeros(P); v[0] = 1; v[1] = 1; v[2 + h] = 1; v[2 + n + a] = 1
        rows.append(v); y.append(r["home_score"])
        # away points scored
        v = np.zeros(P); v[0] = 1; v[2 + a] = 1; v[2 + n + h] = 1
        rows.append(v); y.append(r["away_score"])
    X = np.array(rows); y = np.array(y, dtype=float)
    # ridge: regularise off/def toward 0 (not base/hfa) for identifiability
    R = np.eye(P) * ridge; R[0, 0] = 0; R[1, 1] = 0
    w = np.linalg.solve(X.T @ X + R, X.T @ y)
    base, hfa = w[0], w[1]
    off = {t: w[2 + idx[t]] for t in teams}
    dfn = {t: w[2 + n + idx[t]] for t in teams}
    # residual sigmas for margin and total
    pred = X @ w
    res = (y - pred).reshape(-1, 2)            # [home_resid, away_resid] per game
    margin_res = res[:, 0] - res[:, 1]
    total_res = res[:, 0] + res[:, 1]
    sig_m = float(np.std(margin_res)) or 1.0
    sig_t = float(np.std(total_res)) or 1.0
    return {"base": base, "hfa": hfa, "off": off, "def": dfn,
            "sig_margin": sig_m, "sig_total": sig_t, "teams": set(teams)}


def expected(model, home, away, total_factor=1.0):
    o, d = model["off"], model["def"]
    mh = model["base"] + model["hfa"] + o.get(home, 0.0) + d.get(away, 0.0)
    ma = model["base"] + o.get(away, 0.0) + d.get(home, 0.0)
    if total_factor != 1.0:
        margin = mh - ma
        total = (mh + ma) * total_factor      # scale total (e.g. playoff defense), keep margin
        mh = (total + margin) / 2.0
        ma = (total - margin) / 2.0
    return {"home_pts": mh, "away_pts": ma, "margin": mh - ma, "total": mh + ma}


def p_home_win(model, home, away):
    e = expected(model, home, away)
    return _ncdf(e["margin"] / model["sig_margin"])


def p_home_cover(model, home, away, line):
    # line = home handicap (e.g. -5.5). Home covers if margin + line > 0.
    e = expected(model, home, away)
    return _ncdf((e["margin"] + line) / model["sig_margin"])


def p_over(model, home, away, line, total_factor=1.0):
    e = expected(model, home, away, total_factor)
    return _ncdf((e["total"] - line) / model["sig_total"])


if __name__ == "__main__":
    # self-test: synthesise a league with known offense/defense, fit, sanity-check
    rng = np.random.default_rng(0)
    teams = [f"T{i}" for i in range(10)]
    skill = {t: rng.normal(0, 6) for t in teams}     # true rating
    base_pts, hfa_true, sigma = 100.0, 3.0, 10.0
    rows = []
    for _ in range(1500):
        h, a = rng.choice(teams, 2, replace=False)
        hs = base_pts + hfa_true + skill[h] - skill[a] + rng.normal(0, sigma)
        as_ = base_pts - skill[h] + skill[a] + rng.normal(0, sigma)
        rows.append({"home": h, "away": a, "home_score": round(hs), "away_score": round(as_)})
    df = pd.DataFrame(rows)
    m = fit_margin(df)
    print(f"fitted base={m['base']:.1f} (true {base_pts}), hfa={m['hfa']:.2f} (true {hfa_true})")
    print(f"sigma margin={m['sig_margin']:.1f}, total={m['sig_total']:.1f}")
    strong = max(teams, key=lambda t: skill[t]); weak = min(teams, key=lambda t: skill[t])
    print(f"strongest {strong} (skill {skill[strong]:.1f}) vs weakest {weak} (skill {skill[weak]:.1f}):")
    e = expected(m, strong, weak)
    print(f"  exp {e['home_pts']:.1f}-{e['away_pts']:.1f}, margin {e['margin']:.1f}, total {e['total']:.1f}")
    print(f"  P(home win)={p_home_win(m, strong, weak)*100:.1f}%  "
          f"P(home -5.5)={p_home_cover(m, strong, weak, -5.5)*100:.1f}%  "
          f"P(over {e['total']:.0f}.5)={p_over(m, strong, weak, e['total']+0.5)*100:.1f}%")
