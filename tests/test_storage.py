import pytest

from cse import storage


def test_fmt_is_stable():
    assert storage.fmt(175294.0) == "175294"
    assert storage.fmt(1048.5) == "1048.5"
    assert storage.fmt(None) == ""
    assert storage.fmt(7534534216386.0) == "7534534216386"


def test_append_dedupes_on_key(tmp_path):
    p = tmp_path / "a.csv"
    cols = ["id", "v"]
    assert storage.append(p, cols, [{"id": "1", "v": 1.5}, {"id": "2", "v": 2}], key=("id",)) == 2
    before = p.read_bytes()
    assert storage.append(p, cols, [{"id": "1", "v": 9}, {"id": "2", "v": 9}], key=("id",)) == 0
    assert p.read_bytes() == before
    assert storage.append(p, cols, [{"id": "3", "v": 3}, {"id": "3", "v": 4}], key=("id",)) == 1
    rows = storage.read_rows(p)
    assert [r["id"] for r in rows] == ["1", "2", "3"]
    assert rows[0]["v"] == "1.5"  # existing rows untouched


def test_header_mismatch_refuses_to_write(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("x,y\n1,2\n")
    with pytest.raises(ValueError, match="header"):
        storage.append(p, ["id", "v"], [{"id": "1", "v": 1}], key=("id",))
    assert p.read_text() == "x,y\n1,2\n"


def test_last_daily_session_ignores_backfill(tmp_path):
    p = tmp_path / "prices.csv"
    storage.append(p, storage.PRICE_COLS, [
        {"date": "2026-10-02", "symbol": "LOLC.N0000", "close": 439.25, "source": "backfill"},
        {"date": "2026-10-01", "symbol": "LOLC.N0000", "close": 443.0, "source": "daily"},
    ], key=("date", "symbol", "source"))
    assert storage.last_daily_session(p) == "2026-10-01"
