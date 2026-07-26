"""
Loads VOACAP's own bundled worldwide location databases for the TX/RX
"pick a location" convenience feature on the point-to-point form, from
itshfbc/geocity/, itshfbc/geonatio/, and itshfbc/geostate/ - all three
already present in the image (the Dockerfile symlinks each in from
voacapl's own distribution), so this parses real files at import time
rather than baking a static list into the source (unlike circuits.py's
approach, which it replaced - Circuits.def wasn't available in the
container at all, these files are).

These directories mix a few different fixed-column table layouts, told
apart by each file's own header banner (line 2) rather than a filename
list, so new files dropped into any of the three directories are picked
up automatically as long as their header matches one of these shapes:

  city/nation (most of geocity/ and geonatio/'s regional files, e.g.
  VOCAP FILES/itshfbc/geocity/AFRICA.GEO) - header has "CITY" + "NATION":
    |======CITY========|==============NATION| LATITUDE|LONGITUDE|
    ABA                                ZAIRE   03 52 N   30 14 E
    col:  1-20 (city)    21-40 (nation)        41-50 (lat) 51-60 (lon)

  city/state (all of geostate/, e.g. .../itshfbc/geostate/ALABAMA.GEO) -
  header has "CITY" + "STATE" but not "BASE"/"INSTALLATION":
    |======CITY==================|=====State| Latitude|Longitude|
    ABBEVILLE                             AL   31 34 N   85 15 W
    col:  1-30 (city)             31-40 (state) 41-50 (lat) 51-60 (lon)

  military (geocity/MILITARY.GEO) - header has "BASE"/"INSTALLATION":
    |US Base/Installation   State|Cty/Nation| Latitude|Longitude|...|
    ANNISTON ARMY DEPOT        AL   ANNISTON   33 35 N   85 51 W ...
    col:  1-30 (name+state, state right-justified within the same field -
    not cleanly split by the original format, left as-is rather than
    guessing a split point) 31-40 (nearest city/nation) 41-50 (lat)
    51-60 (lon)

Files matching none of these (geocity/Ciraf.geo - ITU test points;
geocity/ncdxf.geo, geocity/dxcc1.geo, geonatio/HF-list.geo - ham radio
call+country beacon lists with a merged callsign+city column; OTHER.GEO -
an explicit "how to make your own" sample) are skipped. geonatio's
regional files largely duplicate geocity's own (same city/nation format,
same content) - harmless, since results are de-duplicated below.

Tuple shape: (name, region, lat, lon) - `region` is nation, US state, or
nearest city depending on source file; uniform enough for one picker UI.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ITSHFBC_DIR = Path(os.environ.get("ITSHFBC_DIR", "/opt/itshfbc"))
GEO_DIRS = [ITSHFBC_DIR / d for d in ("geocity", "geonatio", "geostate")]

_DEG_RE = re.compile(r"(\d{1,3})\s+(\d{1,2})\s+([NSEW])")


def _to_decimal(token: str):
    m = _DEG_RE.search(token)
    if not m:
        return None
    deg, minutes, hemi = m.groups()
    value = int(deg) + int(minutes) / 60.0
    if hemi in ("S", "W"):
        value = -value
    return round(value, 4)


def _detect_columns(header: str):
    """Returns (name_span, region_span) for a recognized header, or None."""
    h = header.upper()
    if "BASE" in h or "INSTALLATION" in h:
        return (0, 30), (30, 40)
    if "NATION" in h and "CITY" in h:
        return (0, 20), (20, 40)
    if "STATE" in h and "CITY" in h:
        return (0, 30), (30, 40)
    return None


def _find_header(lines: list):
    """Most files have a one-line description before the column-header
    banner (e.g. geocity/AFRICA.GEO); geostate/*.GEO has no description
    line at all, so the banner is line 1 there instead of line 2 - search
    the first few lines for the "|"-prefixed banner rather than assuming
    a fixed position. Returns (header_index, columns) or None."""
    for i, line in enumerate(lines[:3]):
        if not line.startswith("|"):
            continue
        columns = _detect_columns(line)
        if columns is not None:
            return i, columns
    return None


def _load_locations() -> list:
    locations = []
    seen = set()
    for geo_dir in GEO_DIRS:
        if not geo_dir.is_dir():
            continue
        for path in sorted(geo_dir.glob("*")):
            if path.suffix.lower() != ".geo":
                continue
            try:
                lines = path.read_text(encoding="ascii", errors="replace").splitlines()
            except OSError:
                continue
            found = _find_header(lines)
            if found is None:
                continue
            header_index, columns = found
            (name_start, name_end), (region_start, region_end) = columns
            for line in lines[header_index + 1 :]:
                if len(line) < 60:
                    continue
                name = " ".join(line[name_start:name_end].split())
                region = " ".join(line[region_start:region_end].split())
                lat = _to_decimal(line[40:50])
                lon = _to_decimal(line[50:60])
                if not name or lat is None or lon is None:
                    continue
                key = (name, region)
                if key in seen:
                    continue
                seen.add(key)
                locations.append((name, region, lat, lon))
    locations.sort(key=lambda c: (c[0], c[1]))
    return locations


CITIES = _load_locations()
