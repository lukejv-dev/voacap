"""
Second round-trip check using voacapd.dat's values (different month/SSN,
different SYSTEM values incl. multipath tolerance, different antennas).
voacapd.dat uses METHOD 16, but v1 hardcodes METHOD 24 - so we compare
every line except the METHOD line. render_deck also appends a second
METHOD 26/EXECUTE pass before QUIT (for the chart's FOT line - see
deck.py/parser.py) that voacapd.dat doesn't have; those two extra lines
are checked directly and stripped out before the rest of the comparison.
"""
import sys
from pathlib import Path

from deck import Antenna, CircuitRequest, render_deck

SAMPLE_PATH = Path("/mnt/c/Users/chris/Desktop/CLAUDE/VOCAP FILES/itshfbc/run/voacapd.dat")

req = CircuitRequest(
    label_tx="FT.HOOD",
    label_rx="FT.GORDON",
    tx_lat=31.13,
    tx_lon=-97.73,
    rx_lat=33.48,
    rx_lon=-81.95,
    month=11,
    year=2022,
    sunspot_number=85.0,
    tx_antenna=Antenna(sample_file="SAMPLE.22", bearing_deg=282.8, power_kw=0.15),
    rx_antenna=Antenna(sample_file="SAMPLE.09", bearing_deg=82.6, power_kw=0.0),
    frequencies_mhz=[6.07, 7.20, 9.70, 11.85, 13.70, 15.35, 17.73, 21.65, 25.89],
    noise_dbw=145.0,
    min_takeoff_angle_deg=3.00,
    required_reliability_pct=90.0,
    required_snr_db=48.0,
    multipath_power_tolerance_db=6.00,
    multipath_delay_tolerance_ms=0.85,
)

rendered_lines = render_deck(req).splitlines(keepends=True)
original_lines = SAMPLE_PATH.read_text(encoding="ascii", newline="").splitlines(keepends=True)

expected_extra = ["METHOD       26    0\r\n", "EXECUTE\r\n"]
extra_start = len(rendered_lines) - len(expected_extra) - 1  # -1 for the trailing QUIT
actual_extra = rendered_lines[extra_start : extra_start + len(expected_extra)]
if actual_extra != expected_extra:
    print(f"expected extra METHOD 26 pass {expected_extra!r}, got {actual_extra!r}")
    sys.exit(1)
rendered_lines = rendered_lines[:extra_start] + rendered_lines[extra_start + len(expected_extra) :]

mismatches = []
for i, (r, o) in enumerate(zip(rendered_lines, original_lines)):
    if r != o:
        if r.startswith("METHOD") and o.startswith("METHOD"):
            continue  # expected difference, v1 hardcodes METHOD 24
        if r.startswith("ANTENNA") and o.startswith("ANTENNA"):
            continue  # expected difference, voacapl needs slash+lowercase
        mismatches.append((i + 1, r, o))

if not mismatches and len(rendered_lines) == len(original_lines):
    print("MATCH (except expected METHOD/ANTENNA differences)")
    sys.exit(0)

for line_no, r, o in mismatches:
    print(f"line {line_no}:")
    print(f"  rendered: {r!r}")
    print(f"  original: {o!r}")
if len(rendered_lines) != len(original_lines):
    print(f"line count differs: rendered={len(rendered_lines)} original={len(original_lines)}")
sys.exit(1)
