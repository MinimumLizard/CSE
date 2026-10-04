"""Live record mechanics on real closes (tests/fixtures/.../companyChartDataByStock)."""
import math

import pytest

from cse import record
from cse.models import ms_to_date
from tests.conftest import load, snapshot_files

CFG = {"record_notional_lkr": 10_000_000, "cost_per_side": 0.0112, "dividend_withholding": 0.10,
       "band_relative": 0.25, "band_absolute": 0.02, "min_trade_lkr": 25_000, "rebalance_months": [1, 4, 7, 10],
       "portfolio_size_lkr": 5_000_000}
SYMS = ["LOLC.N0000", "COMB.N0000", "HAYL.N0000", "CIC.N0000"]


def closes():
    out = {}
    for s in SYMS:
        out[s] = {ms_to_date(r["t"]).isoformat(): r["p"] for r in load(f"companyChartDataByStock/{s}.json")["chartData"]}
    return out


def aspi():
    return {ms_to_date(r["d"]).isoformat(): r["v"] for r in load("chartData_1_p5.json")}


SESSIONS = sorted(aspi())
S0 = next(s for s in SESSIONS if s >= "2025-11-05")   # 05 Nov 2025 was a Poya holiday: first session after
TARGETS = {"LOLC.N0000": 0.4, "COMB.N0000": 0.3, "HAYL.N0000": 0.3}
ALL = {p: TARGETS for p in record.TRADED}


def run(root, session, actions=(), targets=ALL, cfg=CFG):
    pricer = record.Pricer(closes(), list(actions), session)
    up = record.update(root, "test", session, SESSIONS, targets, pricer, list(actions), aspi(), cfg)
    record.commit(root, up)
    return up


def test_inception_buys_whole_shares_with_costs(tmp_path):
    up = run(tmp_path, S0)
    assert up.status == f"inception {S0}"
    c = closes()
    buys = [t for t in up.trades if t["portfolio"] == "min_variance"]
    for t in buys:
        p = c[t["symbol"]][S0]
        assert t["shares"] == math.floor(TARGETS[t["symbol"]] * 10_000_000 / (p * 1.0112))
        assert t["cost"] == pytest.approx(t["value"] * 0.0112)
    nav = next(n for n in up.nav if n["portfolio"] == "min_variance")
    spent = sum(t["value"] + t["cost"] for t in buys)
    assert nav["cash"] == pytest.approx(10_000_000 - spent) and nav["cash"] >= 0
    assert nav["nav"] == pytest.approx(10_000_000 - sum(t["cost"] for t in buys))
    mk = next(n for n in up.nav if n["portfolio"] == "market")
    assert mk["nav"] == 10_000_000


def test_second_run_same_day_leaves_record_unchanged(tmp_path):
    run(tmp_path, S0)
    before = {p.name: p.read_bytes() for p in (tmp_path / "record").iterdir()}
    up = run(tmp_path, S0)
    assert up.status.startswith(f"record already has {S0}")
    assert {p.name: p.read_bytes() for p in (tmp_path / "record").iterdir()} == before


def test_no_performance_before_inception(tmp_path):
    run(tmp_path, S0)
    rows = (tmp_path / "record" / "nav.csv").read_text().splitlines()[1:]
    assert min(r.split(",")[0] for r in rows) == S0
    with pytest.raises(RuntimeError, match="after session"):
        run(tmp_path, SESSIONS[SESSIONS.index(S0) - 1])


def test_cash_dividend_credited_on_ex_date_net_of_withholding(tmp_path):
    run(tmp_path, "2026-06-24", targets={p: {"CIC.N0000": 1.0} for p in record.TRADED})
    _, states, _ = record.last_state(record.RecordPaths(tmp_path), "test")
    held = states["min_variance"].shares["CIC.N0000"]
    cash0 = states["min_variance"].cash
    div = [{"symbol": "CIC.N0000", "type": "cash_dividend", "ex_date": "2026-07-01", "amount_per_share": 0.5}]
    # 01 Jul 2026 is the first session of July, a scheduled rebalance day that would reinvest the
    # dividend; take July out of the schedule to see the credit on its own.
    up = run(tmp_path, "2026-07-01", div, targets={p: {"CIC.N0000": 1.0} for p in record.TRADED},
             cfg={**CFG, "rebalance_months": [1, 4, 10]})
    d = [t for t in up.trades if t["portfolio"] == "min_variance" and t["action"] == "dividend"][0]
    assert d["value"] == pytest.approx(held * 0.5 * 0.90)              # shares × D × (1 − 10% WHT)
    nav = next(n for n in up.nav if n["portfolio"] == "min_variance")
    assert nav["cash"] == pytest.approx(cash0 + d["value"])


def test_split_adjusts_share_count_without_a_nav_jump(tmp_path):
    t = {p: {"CIC.N0000": 1.0} for p in record.TRADED}
    run(tmp_path, "2025-10-13", targets=t)
    _, states, navs = record.last_state(record.RecordPaths(tmp_path), "test")
    held = states["risk_parity"].shares["CIC.N0000"]
    split = [{"symbol": "CIC.N0000", "type": "subdivision", "ex_date": "2025-10-22", "factor": 5.0}]
    up = run(tmp_path, "2025-10-22", split, targets=t)
    h = next(x for x in up.holdings if x["portfolio"] == "risk_parity" and x["symbol"] == "CIC.N0000")
    assert h["shares"] == held * 5
    nav1 = next(n for n in up.nav if n["portfolio"] == "risk_parity")["nav"]
    assert nav1 / navs["risk_parity"] - 1 == pytest.approx((34.2 * 5 - 170) * held / navs["risk_parity"], abs=1e-6)


def test_pricer_corrects_stale_price_after_unexecuted_split():
    split = [{"symbol": "CIC.N0000", "type": "subdivision", "ex_date": "2025-10-15", "factor": 5.0}]
    p = record.Pricer(closes(), split, "2025-10-20")      # CIC suspended 14-21 Oct: last trade 13 Oct at 170
    assert p.price("CIC.N0000") == pytest.approx(170 / 5)


def test_rights_subscription_paid_from_cash(tmp_path):
    t = {p: {"HAYL.N0000": 0.5} for p in record.TRADED}   # half in cash
    run(tmp_path, "2026-03-11", targets=t)
    _, states, _ = record.last_state(record.RecordPaths(tmp_path), "test")
    held, cash0 = states["max_sharpe"].shares["HAYL.N0000"], states["max_sharpe"].cash
    rights = [{"symbol": "HAYL.N0000", "type": "rights", "ex_date": "2026-03-18", "ratio_new": 3.0,
               "ratio_held": 50.0, "subscription_price": 200.0}]
    up = run(tmp_path, "2026-03-18", rights, targets=t)
    r = [x for x in up.trades if x["portfolio"] == "max_sharpe" and x["action"] == "rights"][0]
    assert r["shares"] == math.floor(held * 3 / 50) and r["value"] == pytest.approx(r["shares"] * 200.0)


def test_drift_flags_trade_at_next_close(tmp_path):
    run(tmp_path, S0)
    rows = []
    for s in SESSIONS[SESSIONS.index(S0) + 1:]:
        if s[5:7] in ("01", "04", "07", "10") and record.is_scheduled(s, SESSIONS, CFG["rebalance_months"]):
            break
        up = run(tmp_path, s)
        flagged = {h["symbol"] for h in up.holdings if h["portfolio"] == "min_variance" and h["flag"] == "1"}
        rows.append((s, flagged, [t for t in up.trades if t["portfolio"] == "min_variance" and t["reason"] == "drift band"]))
    # whenever a name was flagged, the very next session traded it (unless below min_trade)
    for (s0, flagged, _), (s1, _, trades) in zip(rows, rows[1:]):
        traded = {t["symbol"] for t in trades}
        assert traded <= flagged


def test_band_and_schedule():
    assert record.band(0.10, CFG) == pytest.approx(0.025)        # 25% of 10%
    assert record.band(0.04, CFG) == pytest.approx(0.02)         # absolute floor
    assert record.is_scheduled("2026-01-02", ["2025-12-31", "2026-01-02", "2026-01-05"], [1, 4, 7, 10])
    assert not record.is_scheduled("2026-01-05", ["2025-12-31", "2026-01-02", "2026-01-05"], [1, 4, 7, 10])
    assert not record.is_scheduled("2026-02-02", ["2026-01-30", "2026-02-02"], [1, 4, 7, 10])


def test_scheduled_rebalance_retargets(tmp_path):
    run(tmp_path, "2025-12-31")
    new = {p: {"LOLC.N0000": 0.5, "COMB.N0000": 0.5} for p in record.TRADED}
    first_jan = next(s for s in SESSIONS if s.startswith("2026-01"))
    up = run(tmp_path, first_jan, targets=new)
    assert "scheduled rebalance" in up.status
    held = {h["symbol"]: h for h in up.holdings if h["portfolio"] == "min_variance"}
    assert "HAYL.N0000" not in held                       # fully sold: no longer held or targeted
    assert any(t["symbol"] == "HAYL.N0000" and t["action"] == "sell" for t in up.trades)
    assert held["LOLC.N0000"]["target_weight"] == 0.5


def test_trade_list_scaled_to_portfolio_size():
    rows = record.trade_list({"A": 0.10}, {"A": 0.06}, ["A"], {"A": 50.0}, {"A": 1_000_000.0}, CFG, "next close")
    assert rows == [{"symbol": "A", "side": "add", "shares": 4000, "amount": pytest.approx(200_000.0),
                     "cost": pytest.approx(2240.0), "x_median_turnover": pytest.approx(0.2), "when": "next close"}]
    assert record.trade_list({"A": 0.10}, {"A": 0.099}, ["A"], {"A": 50.0}, {}, CFG, "x") == []   # < min_trade


def test_no_targets_keeps_valuing_and_skips_scheduled_rebalance(tmp_path):
    run(tmp_path, "2025-12-31")
    first_jan = next(s for s in SESSIONS if s.startswith("2026-01"))
    up = run(tmp_path, first_jan, targets=None)
    assert "scheduled rebalance skipped" in up.status
    assert not [t for t in up.trades if t["action"] in ("buy", "sell")]
    assert {n["portfolio"] for n in up.nav} >= set(record.TRADED) | {"market"}


def test_no_targets_and_no_record_writes_nothing(tmp_path):
    up = run(tmp_path, S0, targets=None)
    assert up.nav == [] and not (tmp_path / "record").exists()
