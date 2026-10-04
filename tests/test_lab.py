"""End-to-end lab runs on a temporary repo built from real fixtures (LOLC, CIC, HAYL, COMB; NTB is
excluded). Four stocks can't satisfy a 10% max weight, which exercises the infeasible path."""
import json
from pathlib import Path

import pytest
import yaml

from cse import backfill, fetch, lab
from tests.conftest import snapshot_files

REPO = Path(__file__).resolve().parents[1]


def setup_root(root, client, **overrides):
    fetch.run(client, root)
    backfill.run(client, root)
    cfg = yaml.safe_load((REPO / "config" / "portfolio.yaml").read_text())
    cfg.update(overrides)
    (root / "config" / "portfolio.yaml").write_text(yaml.safe_dump(cfg))
    for name in ("sector_overrides.yaml", "corporate_actions_review.yaml"):
        (root / "config" / name).write_text((REPO / "config" / name).read_text())


def test_waiting_until_history_is_long_enough(root, client):
    setup_root(root, client)                               # min_history_weeks 52; fixtures have 51
    out = lab.run(root)
    assert out["status"] == "waiting" and "51 weekly returns and 52 are required" in out["message"]
    assert not (root / "record").exists()


def test_infeasible_constraints_are_reported_and_nothing_is_traded(root, client):
    setup_root(root, client)
    out = lab.run(root, min_history_override=51)
    assert out["status"] == "infeasible" and "max_weight" in out["message"]
    assert sorted(out["universe"]) == ["CIC.N0000", "COMB.N0000", "HAYL.N0000", "LOLC.N0000"]
    assert {"symbol": "NTB.N0000", "name": "NATIONS TRUST BANK PLC", "reason": "excluded (conflict of interest)"} in out["dropped"]
    assert not (root / "record").exists()


def test_full_run_then_second_run_leaves_record_unchanged(root, client):
    setup_root(root, client, max_weight=0.4, max_sector_weight=0.4)
    out = lab.run(root, min_history_override=51)
    assert out["status"] == "ok", out["message"]
    for k in ("min_variance", "risk_parity", "max_sharpe"):
        c = out["portfolios"][k]["constraints"]
        assert c["ok"] and c["sum"] == pytest.approx(1.0)
    assert out["record_status"] == "inception 2026-10-02"
    rec = {p.name: p.read_bytes() for p in (root / "record").iterdir()}
    assert set(rec) == {"nav.csv", "holdings.csv", "trades.csv"}
    out2 = lab.run(root, min_history_override=51)
    assert out2["record_status"].startswith("record already has 2026-10-02")
    assert {p.name: p.read_bytes() for p in (root / "record").iterdir()} == rec
    saved = json.loads((root / "data" / "lab" / "latest.json").read_text())
    assert saved["session"] == "2026-10-02" and len(saved["frontier"]) > 5


def test_dry_run_writes_nothing(root, client):
    setup_root(root, client, max_weight=0.4, max_sector_weight=0.4)
    before = snapshot_files(root)
    out = lab.run(root, dry_run=True, min_history_override=51)
    assert out["status"] == "ok"
    assert snapshot_files(root) == before and not (root / "record").exists()
