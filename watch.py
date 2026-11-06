"""Hotspot watch: NASA FIRMS VIIRS active-fire points -> SQLite history -> static dashboard (Sumatra, focus Riau).
Usage: python watch.py <out.html>
A hotspot is a satellite heat detection, NOT a confirmed fire."""
import csv, io, json, sqlite3, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if (ROOT / ".deps").exists():
    sys.path.insert(0, str(ROOT / ".deps"))
from shapely.geometry import Point, shape  # noqa: E402
from shapely.prepared import prep  # noqa: E402

SOURCES = {
    "NOAA-20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_24h.csv",
    "SNPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_SouthEast_Asia_24h.csv",
    "NOAA-21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_SouthEast_Asia_24h.csv",
}
UA = "hotspot-watch/1.0 (portfolio project; github.com/andriarezki)"
SUMATRA = ["Aceh", "North Sumatra", "West Sumatra", "Riau", "Riau Islands", "Jambi", "South Sumatra", "Bengkulu", "Lampung", "Bangka-Belitung Islands"]
NAME_ID = {"North Sumatra": "Sumut", "West Sumatra": "Sumbar", "Riau Islands": "Kepri", "South Sumatra": "Sumsel", "Bangka-Belitung Islands": "Babel"}
DB = ROOT / "data" / "hotspots.db"


GEO_URL = "https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/IDN/ADM1/geoBoundaries-IDN-ADM1_simplified.geojson"
GEO_FILE = ROOT / "data" / "idn_adm1.geojson"


def provinces():
    """Province polygons (geoBoundaries, CC BY 4.0), downloaded once and cached."""
    if not GEO_FILE.exists():
        GEO_FILE.parent.mkdir(exist_ok=True)
        req = urllib.request.Request(GEO_URL, headers={"User-Agent": UA})
        GEO_FILE.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    g = json.load(open(GEO_FILE))
    out = {}
    for f in g["features"]:
        n = f["properties"]["shapeName"]
        if n in SUMATRA:
            out[n] = shape(f["geometry"])
    return out


def fetch():
    rows = []
    for sat, url in SOURCES.items():
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read().decode()
        except Exception as e:  # one satellite failing must not stop the run
            print("skip", sat, e); continue
        for r in csv.DictReader(io.StringIO(raw)):
            r["sat"] = sat
            rows.append(r)
    return rows


def update_db(rows, polys, db=DB):
    Path(db).parent.mkdir(exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS hotspots(lat REAL, lon REAL, date TEXT, time TEXT, sat TEXT, conf TEXT, frp REAL, prov TEXT,
                   PRIMARY KEY(lat, lon, date, time, sat))""")
    prepared = {n: prep(p) for n, p in polys.items()}
    minx = min(p.bounds[0] for p in polys.values()); miny = min(p.bounds[1] for p in polys.values())
    maxx = max(p.bounds[2] for p in polys.values()); maxy = max(p.bounds[3] for p in polys.values())
    new = 0
    for r in rows:
        lat, lon = float(r["latitude"]), float(r["longitude"])
        if not (minx <= lon <= maxx and miny <= lat <= maxy) or r["confidence"] == "low":
            continue
        pt = Point(lon, lat)
        prov = next((n for n, p in prepared.items() if p.contains(pt)), None)
        if prov is None:
            continue
        cur = con.execute("INSERT OR IGNORE INTO hotspots VALUES(?,?,?,?,?,?,?,?)",
                          (lat, lon, r["acq_date"], r["acq_time"], r["sat"], r["confidence"], float(r["frp"] or 0), prov))
        new += cur.rowcount
    con.commit()
    return con, new


def svg_map(points, riau):
    minx, miny, maxx, maxy = riau.bounds
    W = 380; H = int(W * (maxy - miny) / (maxx - minx) * 1.0)
    sx = lambda x: (x - minx) / (maxx - minx) * W
    sy = lambda y: H - (y - miny) / (maxy - miny) * H
    polys = list(riau.geoms) if riau.geom_type == "MultiPolygon" else [riau]
    paths = "".join('<path d="M' + " L".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in p.exterior.coords) + ' Z" class="land"/>' for p in polys)
    dots = "".join(f'<circle cx="{sx(lon):.1f}" cy="{sy(lat):.1f}" r="{2.2 + min(frp, 40) / 14:.1f}" class="dot"/>' for lat, lon, frp in points)
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Peta titik panas Riau">{paths}{dots}</svg>'


def svg_bars(items, w=380, h=150, color="var(--a)"):
    if not items: return "<p class='muted'>Belum ada data.</p>"
    mx = max(v for _, v in items) or 1
    bw = w / len(items)
    out = []
    for i, (lab, v) in enumerate(items):
        bh = (h - 30) * v / mx
        out.append(f'<rect x="{i*bw+3:.1f}" y="{h-18-bh:.1f}" width="{bw-6:.1f}" height="{bh:.1f}" fill="{color}" rx="2"/>'
                   f'<text x="{i*bw+bw/2:.1f}" y="{h-5}" text-anchor="middle" class="ax">{lab}</text>'
                   f'<text x="{i*bw+bw/2:.1f}" y="{h-21-bh:.1f}" text-anchor="middle" class="vl">{v}</text>')
    return f'<svg viewBox="0 0 {w} {h}" role="img">{"".join(out)}</svg>'


def page(con, polys, updated):
    today = datetime.now(timezone.utc).date()
    d1 = (today - timedelta(days=1)).isoformat()
    d7 = (today - timedelta(days=6)).isoformat()
    cur = con.cursor()
    riau_24 = cur.execute("SELECT COUNT(*) FROM hotspots WHERE prov='Riau' AND date>=?", (d1,)).fetchone()[0]
    riau_hi = cur.execute("SELECT COUNT(*) FROM hotspots WHERE prov='Riau' AND date>=? AND conf='high'", (d1,)).fetchone()[0]
    pts = cur.execute("SELECT lat, lon, frp FROM hotspots WHERE prov='Riau' AND date>=?", (d1,)).fetchall()
    daily = dict(cur.execute("SELECT date, COUNT(*) FROM hotspots WHERE prov='Riau' AND date>=? GROUP BY date", (d7,)).fetchall())
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    first = cur.execute("SELECT MIN(date) FROM hotspots").fetchone()[0]
    bars = [(d.strftime("%d/%m"), daily.get(d.isoformat(), 0)) for d in days if first and d.isoformat() >= first]
    prov = cur.execute("SELECT prov, COUNT(*) FROM hotspots WHERE date>=? GROUP BY prov ORDER BY 2 DESC", (d7,)).fetchall()
    prov_bars = [(NAME_ID.get(p, p)[:7], c) for p, c in prov[:8]]
    total_days = cur.execute("SELECT COUNT(DISTINCT date) FROM hotspots").fetchone()[0]
    return f"""<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Titik panas Riau</title><style>
:root{{--bg:#f7f8f6;--fg:#1d2733;--card:#fff;--line:#e1e5e9;--mut:#52606d;--a:#c0392b;--land:#e8efe9;--ls:#9fb3a4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14181c;--fg:#e8ecef;--card:#1d2328;--line:#2d353c;--mut:#9aa7b2;--land:#26322b;--ls:#4a6354;--a:#ff6b5a}}}}
body{{font-family:system-ui,Arial,sans-serif;margin:0;background:var(--bg);color:var(--fg)}}main{{max-width:900px;margin:0 auto;padding:20px 16px 48px}}
h1{{font-size:1.5rem;margin:0 0 4px}}h2{{font-size:1rem;margin:0 0 8px}}.muted{{color:var(--mut);font-size:.88rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:14px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.big{{font-size:2.4rem;font-weight:700;color:var(--a);line-height:1}}svg{{width:100%;height:auto}}.land{{fill:var(--land);stroke:var(--ls);stroke-width:1}}.dot{{fill:var(--a);fill-opacity:.55}}
.ax{{font-size:9px;fill:var(--mut)}}.vl{{font-size:9px;fill:var(--fg)}}footer{{margin-top:24px;color:var(--mut);font-size:.8rem}}
</style></head><body><main>
<h1>Titik panas Riau</h1><p class="muted">Deteksi panas satelit VIIRS (NASA FIRMS), diperbarui tiap 3 jam. Terakhir: {updated}.</p>
<div class="grid">
<div class="card"><h2>24 jam terakhir</h2><div class="big">{riau_24}</div><p class="muted">titik panas di Riau ({riau_hi} berkepercayaan tinggi). Titik panas adalah deteksi panas dari satelit, belum tentu kebakaran lahan.</p></div>
<div class="card"><h2>Sebaran di Riau</h2>{svg_map(pts, polys["Riau"])}</div>
<div class="card"><h2>Riau per hari (7 hari)</h2>{svg_bars(bars)}<p class="muted">Riwayat dikumpulkan sejak {first or '-'} ({total_days} hari data); grafik terisi bertambah seiring waktu.</p></div>
<div class="card"><h2>Perbandingan provinsi Sumatra (7 hari)</h2>{svg_bars(prov_bars, color="var(--a)")}</div>
</div>
<footer>Data: NASA FIRMS (VIIRS NOAA-20, NOAA-21, Suomi NPP), batas provinsi geoBoundaries. Deteksi berkepercayaan rendah dibuang. Proyek belajar, bukan sumber resmi peringatan; rujuk BMKG/BNPB/KLHK untuk informasi resmi.</footer>
</main></body></html>"""


def main(out):
    polys = provinces()
    rows = fetch()
    con, new = update_db(rows, polys)
    updated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    Path(out).write_text(page(con, polys, updated), encoding="utf-8")
    print("rows", len(rows), "new", new)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "out.html"))
