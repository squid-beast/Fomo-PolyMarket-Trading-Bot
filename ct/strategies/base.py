"""Strategy plumbing. Each strategy scores 0-100 and must justify itself."""
from __future__ import annotations
from dataclasses import dataclass, field
from ..datasource import Pair


@dataclass
class Signal:
    name: str
    score: float                      # 0-100
    rationale: list[str] = field(default_factory=list)

    @property
    def fires(self) -> bool:
        return self.score >= 60.0

    def as_dict(self) -> dict:
        return {"name": self.name, "score": round(self.score, 1), "rationale": self.rationale}


class Strategy:
    name = "base"

    def evaluate(self, p: Pair) -> Signal:
        raise NotImplementedError

    @staticmethod
    def _band(value: float, lo: float, hi: float) -> float:
        """Map value into 0..1 across [lo,hi], clamped."""
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def load_strategies(cfg) -> list[Strategy]:
    from .momentum import Momentum
    from .volume_surge import VolumeSurge
    from .liquidity_quality import LiquidityQuality
    registry = {"momentum": Momentum, "volume_surge": VolumeSurge, "liquidity_quality": LiquidityQuality}
    weights = cfg.get_path("strategies.weights", {}) or {}
    out = []
    for key, cls in registry.items():
        if float(weights.get(key, 1.0)) > 0:
            out.append(cls())
    return out
