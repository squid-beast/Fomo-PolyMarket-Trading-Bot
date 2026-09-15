"""Config loading. Every threshold in the system resolves through here."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any
import yaml

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


class Config(dict):
    """dict with dotted access: cfg.get_path('risk.max_position_pct')."""

    def get_path(self, path: str, default: Any = None) -> Any:
        cur: Any = self
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def require(self, path: str) -> Any:
        val = self.get_path(path, _MISSING)
        if val is _MISSING:
            raise KeyError(f"config missing required key: {path}")
        return val


_MISSING = object()


def load(path: str | None = None) -> Config:
    with open(path or _DEFAULT) as fh:
        return Config(yaml.safe_load(fh))
