# ec45.klymot.com

Daily archive of ECMWF open-data **IFS ensemble 2 m temperature forecasts, sampled at
the 27,961 GHCN station locations** shown on [www.klymot.com](https://www.klymot.com).

ECMWF's open-data feed is a rolling window — each forecast run disappears from the
mirror after a few days. This repo captures the point data that would otherwise be
lost: once a day, a GitHub Actions job downloads the 00z run of the 15-day IFS
ensemble (stream `enfo`, all 50 perturbed members' 2t fields via byte-range
requests — open data publishes no control member) plus the 10-day HRES run
(stream `oper`), samples the global 0.25° fields at every station, reduces the
members to ensemble mean and spread, aggregates to daily means per lead day 1–15,
and commits the result. Over time this accumulates an archive of *what was forecast* for
every station, which can later be compared with *what happened*.

The name is aspirational: the 45-day extended-range ensemble (EC45) is **not** in
ECMWF's open data — only the 15-day ENS is. If extended-range data ever becomes
openly redistributable, it lands here under the same layout.

## Data layout (`docs/`, served via GitHub Pages)

The same values are stored along two axes, so either shape is one request away:

```
docs/data/
  manifest.json                what's here, formats, license
  stations.csv                 station_id, name, lat, lon (row order of date/ files)
  runs.txt                     captured run dates, one per line
  date/2026-07-17.csv          one run, every station   (written once, immutable)
  station/US/USW00053926.csv   one station, every run   (one row appended per day)
```

Station files are sharded by the first two characters of the station id; rows in
`date/` files are always in station-id order. Both file kinds share the same 45
value columns: `em_d01`…`em_d15`, `es_d01`…`es_d15`, `hres_d01`…`hres_d15` —
ENS ensemble mean (50 perturbed members), ENS ensemble standard deviation, and
the HRES deterministic forecast (10-day horizon, so `hres_d11`…`hres_d15` are
always empty). Lead day L is the mean of the four 6-hourly valid times in the
24 h ending 00z on init+L days. Values are **deci-degrees Celsius** (divide by
10), sampled at the nearest 0.25° grid point; empty cells are missing data.

## Running the capture by hand

```
uv sync
uv run python code/capture.py            # today's run + up to 3 days backfill
uv run python code/capture.py --date 2026-07-17 --backfill 0
uv run python code/build_stations.py     # regenerate stations.json from the main site
```

`stations.csv` is frozen once written — it defines the row order of the `date/`
files. If the station pool ever needs to change, that's a new data version, not an
edit in place.

## License and attribution

The data files contain modified [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data),
© European Centre for Medium-Range Weather Forecasts (ECMWF), licensed under
[CC-BY-4.0](https://creativecommons.org/licenses/4.0/). ECMWF does not accept any
liability whatsoever for any error or omission in the data, their availability, or
for any loss or damage arising from their use.

Station locations come from the [www.klymot.com](https://www.klymot.com) GHCN
station index (NOAA GHCN metadata).
