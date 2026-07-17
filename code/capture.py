"""Capture ECMWF open-data IFS ENS 2 m temperature forecasts at klymot stations.

Each 00z run of the 15-day IFS ensemble (stream ``enfo``, whose open data
carries only the 50 perturbed members' 2t — no control) and of the 10-day HRES
run (stream ``oper``) is downloaded from the ECMWF open-data AWS mirror via
byte-range requests, sampled at every station in the www.klymot.com index, and
reduced to ensemble mean (``em``), ensemble standard deviation (``es``) and
HRES (``hres``) daily means per lead day 1-15. The same values are written
along two axes:

- ``docs/data/date/<init_date>.csv`` — every station for one run (immutable),
- ``docs/data/station/<id[:2]>/<id>.csv`` — every run for one station (one row
  appended per day), so a browser can pull either axis in a single request.

Lead day L covers the 24 h ending at 00z on init+L days: the daily mean is the
average of the four 6-hourly valid times at steps 24(L-1)+6, +12, +18, +24.

Contains modified ECMWF open data, © European Centre for Medium-Range Weather
Forecasts (ECMWF), licensed under CC-BY-4.0. See README.md.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import eccodes
import numpy as np
from ecmwf.opendata import Client

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = REPO_ROOT / "docs" / "data"
STATIONS_JSON = REPO_ROOT / "stations.json"

TIME = 0  # the 00z run: the only ENS cycle that runs the full 15 days
PARAM = "2t"
PRODUCTS = ("em", "es", "hres")
LEAD_DAYS = range(1, 16)
# Uniform 6-hourly steps so all leads aggregate alike. The open-data ENS
# (stream enfo) publishes only the 50 perturbed members for 2t — no control —
# so the deterministic companion is the 10-day HRES run (stream oper).
ENS_STEPS = list(range(6, 361, 6))
HRES_STEPS = list(range(6, 241, 6))
MEMBERS = 50  # perturbed members only
VALUE_COLUMNS = [f"{p}_d{lead:02d}" for p in PRODUCTS for lead in LEAD_DAYS]


def load_stations() -> list[dict]:
    with STATIONS_JSON.open() as f:
        stations = json.load(f)["stations"]
    # date/ CSVs promise rows in station-id order; guarantee it here rather
    # than trusting how stations.json was generated.
    return sorted(stations, key=lambda s: s["id"])


def grid_indices(handle, stations: list[dict]) -> np.ndarray:
    """Flat indices of each station's nearest grid point on a regular_ll grid."""
    if eccodes.codes_get(handle, "gridType") != "regular_ll":
        raise ValueError("expected a regular_ll grid")
    ni = eccodes.codes_get(handle, "Ni")
    nj = eccodes.codes_get(handle, "Nj")
    lat0 = eccodes.codes_get(handle, "latitudeOfFirstGridPointInDegrees")
    lon0 = eccodes.codes_get(handle, "longitudeOfFirstGridPointInDegrees")
    di = eccodes.codes_get(handle, "iDirectionIncrementInDegrees")
    dj = eccodes.codes_get(handle, "jDirectionIncrementInDegrees")
    lats = np.array([s["lat"] for s in stations])
    lons = np.array([s["lon"] for s in stations])
    rows = np.clip(np.round((lat0 - lats) / dj).astype(int), 0, nj - 1)
    cols = np.round(((lons - lon0) % 360.0) / di).astype(int) % ni
    return rows * ni + cols


def stream_points(grib_path: Path, stations: list[dict]):
    """Yield (step_hours, values[station] in °C) per grib message."""
    idx: np.ndarray | None = None
    with grib_path.open("rb") as f:
        while True:
            handle = eccodes.codes_grib_new_from_file(f)
            if handle is None:
                break
            try:
                if idx is None:
                    idx = grid_indices(handle, stations)
                step = int(eccodes.codes_get(handle, "endStep"))
                points = eccodes.codes_get_values(handle)[idx] - 273.15
            finally:
                eccodes.codes_release(handle)
            yield step, points


def sample_ensemble(
    grib_path: Path, stations: list[dict]
) -> dict[str, dict[int, np.ndarray]]:
    """Reduce the 50-member grib to per-step ensemble mean and spread."""
    n = len(stations)
    sums: dict[int, np.ndarray] = {}
    sumsqs: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    for step, points in stream_points(grib_path, stations):
        if step not in sums:
            sums[step] = np.zeros(n)
            sumsqs[step] = np.zeros(n)
            counts[step] = 0
        sums[step] += points
        sumsqs[step] += points**2
        counts[step] += 1
    out: dict[str, dict[int, np.ndarray]] = {"em": {}, "es": {}}
    for step, count in counts.items():
        if count != MEMBERS:
            print(f"  note: step {step} has {count} members (expected {MEMBERS})")
        mean = sums[step] / count
        out["em"][step] = mean
        out["es"][step] = np.sqrt(np.maximum(sumsqs[step] / count - mean**2, 0.0))
    return out


def sample_hres(grib_path: Path, stations: list[dict]) -> dict[int, np.ndarray]:
    return dict(stream_points(grib_path, stations))


def daily_means(series: dict[int, np.ndarray], n: int) -> dict[int, np.ndarray]:
    """Aggregate 6-hourly step series to per-lead-day means."""
    out: dict[int, np.ndarray] = {}
    for lead in LEAD_DAYS:
        steps = [24 * (lead - 1) + h for h in (6, 12, 18, 24)]
        if all(s in series for s in steps):
            out[lead] = np.mean([series[s] for s in steps], axis=0)
        else:
            out[lead] = np.full(n, np.nan)
    return out


def fmt(value: float) -> str:
    """Deci-°C integer, empty for missing."""
    return "" if np.isnan(value) else str(int(round(value * 10.0)))


def station_path(station_id: str) -> Path:
    return DOCS_DATA / "station" / station_id[:2] / f"{station_id}.csv"


def last_line_date(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            lines = f.read().splitlines()
        return lines[-1].split(b",", 1)[0].decode() if lines else None
    except FileNotFoundError:
        return None


def write_date_csv(
    date: dt.date, stations: list[dict], products: dict[str, dict[int, np.ndarray]]
) -> None:
    path = DOCS_DATA / "date" / f"{date.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["station_id"] + VALUE_COLUMNS)
        for i, station in enumerate(stations):
            row = [station["id"]] + [
                fmt(products[p][lead][i]) for p in PRODUCTS for lead in LEAD_DAYS
            ]
            writer.writerow(row)


def append_station_rows(
    date: dt.date, stations: list[dict], products: dict[str, dict[int, np.ndarray]]
) -> None:
    header = "init_date," + ",".join(VALUE_COLUMNS) + "\n"
    iso = date.isoformat()
    for i, station in enumerate(stations):
        path = station_path(station["id"])
        if last_line_date(path) == iso:
            continue  # crash-recovery: this station already has today's row
        path.parent.mkdir(parents=True, exist_ok=True)
        values = [fmt(products[p][lead][i]) for p in PRODUCTS for lead in LEAD_DAYS]
        with path.open("a") as f:
            if f.tell() == 0:
                f.write(header)
            f.write(iso + "," + ",".join(values) + "\n")


def captured_dates() -> set[str]:
    path = DOCS_DATA / "runs.txt"
    return set(path.read_text().split()) if path.exists() else set()


def capture_run(client: Client, date: dt.date, stations: list[dict]) -> bool:
    """Fetch one 00z run; returns True if captured, False if unavailable."""
    if date.isoformat() in captured_dates():
        print(f"{date}: already captured, skipping")
        return True
    requests = {
        "ens": dict(stream="enfo", type="pf", step=ENS_STEPS),
        "hres": dict(stream="oper", type="fc", step=HRES_STEPS),
    }
    step_series: dict[str, dict[int, np.ndarray]] = {}
    with tempfile.TemporaryDirectory(prefix="ec45-grib-") as tmp:
        for name, request in requests.items():
            target = Path(tmp) / f"{name}.grib2"
            try:
                client.retrieve(
                    param=PARAM,
                    date=date.isoformat(),
                    time=TIME,
                    target=str(target),
                    **request,
                )
            except Exception as exc:
                print(f"{date}: {name} unavailable ({exc}); run not captured")
                return False
            print(f"{date}: {name} downloaded {target.stat().st_size / 1e9:.2f} GB")
            if name == "ens":
                step_series.update(sample_ensemble(target, stations))
            else:
                step_series["hres"] = sample_hres(target, stations)
    products = {
        product: daily_means(series, len(stations))
        for product, series in step_series.items()
    }
    write_date_csv(date, stations, products)
    append_station_rows(date, stations, products)
    with (DOCS_DATA / "runs.txt").open("a") as f:
        f.write(date.isoformat() + "\n")
    print(f"{date}: captured {len(stations)} stations x {len(LEAD_DAYS)} lead days")
    return True


def write_stations_csv(stations: list[dict]) -> None:
    path = DOCS_DATA / "stations.csv"
    if path.exists():
        return  # frozen: defines the row order of every date/ CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["station_id", "name", "lat", "lon"])
        for s in stations:
            writer.writerow([s["id"], s["name"], s["lat"], s["lon"]])


def write_manifest(stations: list[dict]) -> None:
    manifest = {
        "description": (
            "2 m temperature forecasts from the ECMWF open-data IFS ensemble "
            "(stream enfo, 00z run, 15-day horizon), sampled at the GHCN "
            "station locations shown on www.klymot.com and reduced to daily "
            "means per lead day 1-15."
        ),
        "attribution": (
            "Contains modified ECMWF open data, © European Centre for "
            "Medium-Range Weather Forecasts (ECMWF), CC-BY-4.0."
        ),
        "license": "CC-BY-4.0",
        "products": {
            "em": "ENS ensemble mean (50 perturbed members)",
            "es": "ENS ensemble standard deviation (50 perturbed members)",
            "hres": "HRES deterministic forecast (10-day horizon; d11-d15 empty)",
        },
        "axes": {
            "date/<init_date>.csv": "all stations for one run",
            "station/<id[:2]>/<id>.csv": "all runs for one station",
        },
        "lead_day_definition": (
            "lead day L = mean of the four 6-hourly valid times in the 24 h "
            "ending 00z on init+L days (steps 24(L-1)+6/12/18/24)"
        ),
        "units": "deci-degrees Celsius (divide by 10)",
        "station_count": len(stations),
        "stations": "stations.csv",
        "runs": "runs.txt",
    }
    (DOCS_DATA / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", help="capture a single run date (YYYY-MM-DD, default: today UTC)"
    )
    parser.add_argument(
        "--backfill",
        type=int,
        default=3,
        metavar="N",
        help="also try the N previous days still on the mirror (default 3)",
    )
    parser.add_argument(
        "--source",
        default="aws",
        choices=["aws", "ecmwf", "azure"],
        help="open-data mirror to download from (default aws)",
    )
    args = parser.parse_args()

    stations = load_stations()
    write_stations_csv(stations)
    client = Client(source=args.source)
    if args.date:
        newest = dt.date.fromisoformat(args.date)
    else:
        newest = dt.datetime.now(dt.timezone.utc).date()
    dates = [newest - dt.timedelta(days=n) for n in range(args.backfill + 1)]

    captured_any = False
    for date in sorted(dates):
        captured_any |= capture_run(client, date, stations)
    write_manifest(stations)
    return 0 if captured_any else 1


if __name__ == "__main__":
    sys.exit(main())
