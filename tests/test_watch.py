import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watch  # noqa: E402

TODAY = datetime.now(timezone.utc).date().isoformat()
POLYS = {"Riau": box(100, -1, 103, 2), "Jambi": box(102, -3, 104, -1.01)}


def row(lat, lon, conf="nominal", sat="NOAA-20", date=TODAY, t="0300", frp="5"):
    return {"latitude": str(lat), "longitude": str(lon), "confidence": conf, "sat": sat,
            "acq_date": date, "acq_time": t, "frp": frp}


def test_points_are_assigned_to_provinces_and_low_confidence_is_dropped(tmp_path):
    rows = [row(0.5, 101), row(-2, 103), row(0.6, 101.2, conf="low"), row(10, 120)]
    con, new = watch.update_db(rows, POLYS, db=tmp_path / "h.db")
    got = dict(con.execute("SELECT prov, COUNT(*) FROM hotspots GROUP BY prov").fetchall())
    assert got == {"Riau": 1, "Jambi": 1} and new == 2  # low confidence and out-of-area rows ignored


def test_same_detection_is_not_stored_twice(tmp_path):
    db = tmp_path / "h.db"
    watch.update_db([row(0.5, 101)], POLYS, db=db)
    _, new = watch.update_db([row(0.5, 101)], POLYS, db=db)
    assert new == 0


def test_page_renders_counts_and_disclaimer(tmp_path, monkeypatch):
    con, _ = watch.update_db([row(0.5, 101, conf="high"), row(0.7, 101.4)], POLYS, db=tmp_path / "h.db")
    html = watch.page(con, POLYS, "test")
    assert "Titik panas Riau" in html and ">2<" in html and "belum tentu kebakaran" in html
    assert 'name="robots"' in html


def test_empty_database_still_renders(tmp_path):
    con, _ = watch.update_db([], POLYS, db=tmp_path / "e.db")
    assert "Titik panas Riau" in watch.page(con, POLYS, "test")
