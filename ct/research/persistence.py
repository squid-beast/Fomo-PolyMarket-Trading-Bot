"""
Does trader performance PERSIST, or is the leaderboard just this week's noise?

Three independent tests, because any one alone is easy to fool:

  1. SPLIT-HALF RANK CORRELATION
     Split each trader's history chronologically. Does first-half rank predict
     second-half rank? Rank-based on purpose: memecoin returns are so skewed
     that one 80x trade would otherwise decide everything.

  2. TOP-QUINTILE PERSISTENCE
     Of the top 20% in period 1, how many stay top 20% in period 2?
     Null is exactly 20%. This is the number that matters for actually
     choosing someone to follow.

  3. SKILL-vs-LUCK VARIANCE DECOMPOSITION  <- the one that usually gets omitted
     Traders differ in measured return for two reasons: real skill, and noise.
     With N trades and per-trade variance s^2, noise alone produces a spread of
     s^2/N. So:

         Var(observed)  =  Var(skill)  +  E[s^2 / N]
         Var(skill)     =  Var(observed) - E[s^2 / N]

     If that comes out <= 0, the entire observed spread between traders is
     explained by luck and there is no evidence any of them is better than
     any other. Significance comes from an explicit null simulation: give every
     trader the SAME true edge, keep their real trade counts and volatilities,
     and see how often pure luck reproduces the observed spread.

All returns are handled as LOG returns so that compounding is additive and a
single outlier cannot dominate a mean.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
import numpy as np
from scipy import stats

TOTAL_LOSS_FLOOR = -0.99   # a -100% trade would be log(0) = -inf; floor it


@dataclass
class Trader:
    handle: str
    returns: list[float]            # simple per-trade returns, e.g. 0.25 = +25%
    timestamps: list[float] = field(default_factory=list)

    def sorted_log_returns(self) -> np.ndarray:
        r = np.asarray(self.returns, dtype=float)
        if self.timestamps and len(self.timestamps) == len(r):
            r = r[np.argsort(np.asarray(self.timestamps, dtype=float))]
        r = np.clip(r, TOTAL_LOSS_FLOOR, None)
        return np.log1p(r)

    @property
    def n(self) -> int:
        return len(self.returns)


@dataclass
class PersistenceReport:
    n_traders: int
    n_trades_total: int
    median_trades: float
    # test 1
    spearman_rho: float | None
    spearman_p: float | None
    n_split: int
    # test 2
    quintile_persistence: float | None
    quintile_p: float | None
    quintile_n: int
    # test 3
    var_observed: float
    var_noise: float
    var_skill: float
    skill_sd_pct: float | None
    decomp_p: float | None
    skill_ci: tuple[float, float] | None
    verdict: str
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        L = []
        A = L.append
        A("")
        A("  " + "=" * 64)
        A("  TRADER PERSISTENCE ANALYSIS")
        A("  " + "=" * 64)
        A(f"  traders {self.n_traders}   trades {self.n_trades_total:,}   "
          f"median trades/trader {self.median_trades:.0f}")
        A("")
        A("  1. SPLIT-HALF RANK CORRELATION")
        if self.spearman_rho is None:
            A("       insufficient data")
        else:
            sig = "SIGNIFICANT" if (self.spearman_p or 1) < 0.05 else "not significant"
            A(f"       rho = {self.spearman_rho:+.3f}   p = {self.spearman_p:.4f}   "
              f"n = {self.n_split}   [{sig}]")
            A(f"       (0.00 = past tells you nothing about future)")
        A("")
        A("  2. TOP-QUINTILE PERSISTENCE")
        if self.quintile_persistence is None:
            A("       insufficient data")
        else:
            sig = "SIGNIFICANT" if (self.quintile_p or 1) < 0.05 else "not significant"
            A(f"       {self.quintile_persistence*100:.1f}% of period-1 top quintile "
              f"stayed top quintile   (chance = 20.0%)")
            A(f"       p = {self.quintile_p:.4f}   n = {self.quintile_n}   [{sig}]")
        A("")
        A("  3. SKILL vs LUCK DECOMPOSITION")
        A(f"       observed spread between traders   {self.var_observed:.6f}")
        A(f"       spread luck alone would produce   {self.var_noise:.6f}")
        A(f"       residual attributable to skill    {self.var_skill:+.6f}")
        if self.skill_sd_pct is not None:
            A(f"       => skill dispersion  ~{self.skill_sd_pct:.2f}% per trade (SD)")
        if self.skill_ci:
            A(f"       95% CI on skill variance  [{self.skill_ci[0]:+.6f}, {self.skill_ci[1]:+.6f}]")
        if self.decomp_p is not None:
            sig = "SIGNIFICANT" if self.decomp_p < 0.05 else "not significant"
            A(f"       p = {self.decomp_p:.4f}  [{sig}]")
        A("")
        A(f"  VERDICT: {self.verdict}")
        for n in self.notes:
            A(f"    - {n}")
        A("  " + "=" * 64)
        return "\n".join(L)


def _half_means(t: Trader, min_per_half: int) -> tuple[float, float] | None:
    r = t.sorted_log_returns()
    h = len(r) // 2
    if h < min_per_half:
        return None
    return float(r[:h].mean()), float(r[h:2 * h].mean())


def analyse(traders: list[Trader], min_trades: int = 20, min_per_half: int = 10,
            n_sims: int = 4000, n_boot: int = 1500, seed: int = 0,
            quiet: bool = False) -> PersistenceReport:
    rng = np.random.default_rng(seed)
    notes: list[str] = []

    usable = [t for t in traders if t.n >= min_trades]
    if len(usable) < len(traders):
        notes.append(f"{len(traders)-len(usable)} traders dropped for <{min_trades} trades")
    if len(usable) < 8:
        return PersistenceReport(len(usable), sum(t.n for t in usable), 0, None, None, 0,
                                 None, None, 0, 0, 0, 0, None, None, None,
                                 "INSUFFICIENT DATA - need at least 8 traders", notes)

    n_tr = [t.n for t in usable]
    logs = [t.sorted_log_returns() for t in usable]

    # ---- test 1: split-half rank correlation --------------------------
    pairs = [p for p in (_half_means(t, min_per_half) for t in usable) if p]
    rho = pv = None
    if len(pairs) >= 8:
        a, b = zip(*pairs)
        rho, pv = stats.spearmanr(a, b)
        rho, pv = float(rho), float(pv)

    # ---- test 2: top-quintile persistence ------------------------------
    qp = qpv = None
    qn = 0
    if len(pairs) >= 10:
        a, b = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
        k = max(2, int(round(len(a) * 0.2)))
        top1 = set(np.argsort(-a)[:k].tolist())
        top2 = set(np.argsort(-b)[:k].tolist())
        hits = len(top1 & top2)
        qn = k
        qp = hits / k
        # one-sided binomial: is persistence better than the 20% chance rate?
        qpv = float(stats.binomtest(hits, k, 0.2, alternative="greater").pvalue)

    # ---- test 3: skill vs luck ------------------------------------------
    means = np.array([float(l.mean()) for l in logs])
    varis = np.array([float(l.var(ddof=1)) if len(l) > 1 else 0.0 for l in logs])
    ns = np.array(n_tr, dtype=float)

    var_obs = float(means.var(ddof=1))
    var_noise = float(np.mean(varis / ns))
    var_skill = var_obs - var_noise

    # Null simulation: every trader has the SAME true edge (the grand mean),
    # but keeps their real trade count and real volatility. How often does
    # pure luck alone reproduce the observed spread?
    grand = float(means.mean())
    null_vars = np.empty(n_sims)
    for s in range(n_sims):
        sim = rng.normal(grand, np.sqrt(np.maximum(varis / ns, 1e-18)))
        null_vars[s] = sim.var(ddof=1)
    decomp_p = float((null_vars >= var_obs).mean())

    # Bootstrap CI on skill variance (resample traders)
    boot = np.empty(n_boot)
    idx_all = np.arange(len(usable))
    for s in range(n_boot):
        idx = rng.choice(idx_all, size=len(idx_all), replace=True)
        m, v, n = means[idx], varis[idx], ns[idx]
        boot[s] = m.var(ddof=1) - np.mean(v / n)
    ci = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))

    skill_sd_pct = (math.expm1(math.sqrt(var_skill)) * 100) if var_skill > 0 else None

    # ---- verdict ---------------------------------------------------------
    signals = [
        (rho is not None and pv is not None and pv < 0.05 and rho > 0),
        (qpv is not None and qpv < 0.05),
        (decomp_p < 0.05 and var_skill > 0),
    ]
    n_sig = sum(signals)
    if n_sig == 0:
        verdict = "NO EVIDENCE OF SKILL - consistent with pure luck"
        notes.append("Observed spread between traders is fully explained by noise.")
        notes.append("Following last week's leaderboard has no expected edge.")
    elif n_sig == 3:
        verdict = "PERSISTENT SKILL DETECTED on all three tests"
    else:
        verdict = f"WEAK / MIXED EVIDENCE - {n_sig} of 3 tests significant"
        notes.append("Treat with suspicion: partial significance across correlated "
                     "tests is what multiple testing produces by chance.")

    if var_skill <= 0:
        notes.append("Skill variance is negative, i.e. traders differ LESS than luck "
                     "alone predicts. Strong evidence of no differential skill.")
    if float(np.median(ns)) < 40:
        notes.append(f"Median {np.median(ns):.0f} trades/trader is low - the test has "
                     "limited power to detect small edges (see power analysis).")

    return PersistenceReport(
        n_traders=len(usable), n_trades_total=int(sum(n_tr)),
        median_trades=float(np.median(ns)),
        spearman_rho=rho, spearman_p=pv, n_split=len(pairs),
        quintile_persistence=qp, quintile_p=qpv, quintile_n=qn,
        var_observed=var_obs, var_noise=var_noise, var_skill=var_skill,
        skill_sd_pct=skill_sd_pct, decomp_p=decomp_p, skill_ci=ci,
        verdict=verdict, notes=notes)
