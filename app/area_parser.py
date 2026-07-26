"""
Parses a voacapl area-coverage `.vg_` output file.

Format read directly from voacapl's own source (src/voacapw/outarea.for,
the OUTarea subroutine) rather than guessed - see area_deck.py's docstring
for how the fixture that grounds this was found. For the single-frequency
case we always use (see area_deck.py), OUTarea writes, in order:

  1. one header line: "VOACAPL Version <ver>"           (format 101)
  2. one free-text label line describing the run          (format 102)
  3. one column-header line, using -nx,-ny as a sentinel   (format 103):
     "ix iy  Latitude Longitude   MUF  MODE ANGLE DELAY VHITE MUFda  LOSS
      DBU  SDBW  NDBW   SNR RPWRG   REL MPROB SPROB TGAIN RGAIN SNRxx
      DU    DL SIGLW SIGUP PWRCTANGLER"
  4. nx*ny data lines, format '(2i3,2f10.4,24a6)':
     ix(3) iy(3) lat(10.4) lon(10.4) then 24 six-char metric fields in the
     order named above. REL (reliability, 0.000-1.000) is field 13 of 24 -
     right after RPWRG, right before MPROB.

This positional structure (not a scanned marker, unlike parser.py's P2P
table) is taken directly from the subroutine's write order, which always
emits exactly one version line then exactly one label line before the
column header - confirm against a real run before relying on it in
production (see the plan's Testing/Verification section).
"""
from __future__ import annotations

from dataclasses import dataclass

_REL_FIELD_INDEX = 12  # 0-indexed position of REL among the 24 alfs fields
_MUF_FIELD_INDEX = 0


@dataclass
class AreaPoint:
    lat: float
    lon: float
    muf: float
    reliability: float


@dataclass
class AreaResult:
    points: list  # list[AreaPoint]
    raw_output: str


def parse_area_output(text: str) -> AreaResult:
    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("area output too short to contain a header, label, and data")
    # lines[0] = "VOACAPL Version ...", lines[1] = free-text label,
    # lines[2] = column-header sentinel row - all skipped, data starts at 3.
    points = []
    for line in lines[3:]:
        if not line.strip():
            continue
        if len(line) < 26:
            raise ValueError(f"area data line too short: {line!r}")
        try:
            lat = float(line[6:16])
            lon = float(line[16:26])
        except ValueError as e:
            raise ValueError(f"could not parse lat/lon from area data line: {line!r}") from e
        fields_text = line[26:]
        fields = [fields_text[i : i + 6] for i in range(0, len(fields_text), 6)]
        if len(fields) < 24:
            raise ValueError(f"expected 24 metric fields, got {len(fields)}: {line!r}")
        muf = float(fields[_MUF_FIELD_INDEX])
        reliability = float(fields[_REL_FIELD_INDEX])
        points.append(AreaPoint(lat=lat, lon=lon, muf=muf, reliability=reliability))

    if not points:
        raise ValueError("no area data points parsed from output")
    return AreaResult(points=points, raw_output=text)
