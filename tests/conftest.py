"""Shared fixtures. All market data comes from real API responses captured in Step 0
(tests/fixtures/2026-10-02/); nothing is hand-typed."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cse.http import Response

FIX = Path(__file__).parent / "fixtures" / "2026-10-02"
REPO = Path(__file__).resolve().parents[1]


def load(name: str):
    return json.loads((FIX / name).read_bytes())


class FakeClient:
    """Serves recorded real responses in place of the network. Unrecorded announcement
    details behave as the live API does for ids an endpoint doesn't serve: 204 from
    getAnnouncementById, {} from getGeneralAnnouncementById."""

    def __init__(self, fixtures: Path = FIX, overrides: dict | None = None):
        self.fixtures = fixtures
        self.overrides = overrides or {}
        self.calls = 0
        self.log: list[tuple[str, dict]] = []

    def call(self, endpoint: str, **params) -> Response:
        self.calls += 1
        self.log.append((endpoint, params))
        if endpoint in self.overrides:
            data = self.overrides[endpoint]
            return Response(endpoint, params, 200, json.dumps(data).encode(), data)
        if endpoint == "companyInfoSummery":
            path = self.fixtures / "companyInfoSummery" / f"{params['symbol']}.json"
        elif endpoint == "getAnnouncementById":
            path = self.fixtures / "getAnnouncementById" / f"{params['announcementId']}.json"
            if not path.exists():
                return Response(endpoint, params, 204, b"", None)
        elif endpoint == "getGeneralAnnouncementById":
            path = self.fixtures / "getGeneralAnnouncementById" / f"{params['announcementId']}.json"
            if not path.exists():
                return Response(endpoint, params, 200, b"{}", {})   # live API answers {} for typed ids
        elif endpoint == "companyProfile":
            path = self.fixtures / "companyProfile" / f"{params['symbol']}.json"
            if not path.exists():
                return Response(endpoint, params, 200, b'{"reqComSumInfo":[]}', {"reqComSumInfo": []})
        elif endpoint == "companyChartDataByStock":
            sym = {s["id"]: s["symbol"] for s in load("allSecurityCode.json")}[int(params["stockId"])]
            path = self.fixtures / "companyChartDataByStock" / f"{sym}.json"
            if not path.exists():
                body = b'{"chartData":[]}'
                return Response(endpoint, params, 200, body, json.loads(body))
        elif endpoint == "chartData":
            path = self.fixtures / f"chartData_{params['chartId']}_p5.json"
            if not path.exists():
                return Response(endpoint, params, 200, b"[]", [])
        else:
            path = self.fixtures / f"{endpoint}.json"
        raw = path.read_bytes()
        return Response(endpoint, params, 200, raw, json.loads(raw))


UNIVERSE = """\
watchlist:
  CORE:
    - LOLC.N0000
    - CIC.N0000
excluded:
  - NTB.N0000
display:
  gainers_losers_min_turnover_lkr: 1000000
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "universe.yaml").write_text(UNIVERSE)
    return tmp_path


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


def snapshot_files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted((root / "data").rglob("*")) if p.is_file() and p.name != "runs.csv"}
