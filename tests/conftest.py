import sys, os, copy
from pathlib import Path
import pytest, yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ct import config
from ct.costs import CostModel
from ct.portfolio import Portfolio
from ct.risk import RiskEngine
from ct.safety import SafetyFilter
from ct.datasource import Pair
import time


@pytest.fixture
def cfg():
    return config.load(str(Path(__file__).resolve().parents[1] / "config.yaml"))


@pytest.fixture
def cm(cfg):
    return CostModel(cfg)


@pytest.fixture
def pf(cfg, cm):
    return Portfolio(cfg, cm)


@pytest.fixture
def risk(cfg):
    return RiskEngine(cfg)


@pytest.fixture
def safety(cfg):
    return SafetyFilter(cfg)


def mk_pair(symbol="TEST", price=0.001, liq=300_000.0, token=None, age_days=5,
            h1_buys=60, h1_sells=55, vol24=None, m5=1.0, h1=4.0, h6=12.0, h24=20.0):
    liq = float(liq)
    return Pair(chain="solana", dex="raydium", pair_address=f"PA_{symbol}",
                token_address=token or f"TK_{symbol}", symbol=symbol, name=symbol,
                price_usd=price, liquidity_usd=liq, fdv=liq * 20, market_cap=liq * 20,
                volume_h24=vol24 if vol24 is not None else liq * 3,
                volume_h6=liq, volume_h1=liq / 4, volume_m5=liq / 40,
                txns_h1_buys=h1_buys, txns_h1_sells=h1_sells,
                txns_h24_buys=900, txns_h24_sells=880,
                price_change_m5=m5, price_change_h1=h1, price_change_h6=h6,
                price_change_h24=h24,
                pair_created_at_ms=int((time.time() - 86400 * age_days) * 1000), url="")


@pytest.fixture
def pair():
    return mk_pair
