"""
Renders a deck using the exact values that produced the known-good sample
itshfbc/run/voacapx.dat, then byte-diffs the result against that real file.
ANTENNA lines are expected to differ (forward slash + lowercase for
voacapl instead of the original Windows backslash + uppercase) - every
other card must match exactly. render_deck also appends a second
METHOD 26/EXECUTE pass before QUIT (for the chart's FOT line - see
deck.py/parser.py), which voacapx.dat doesn't have; those two extra lines
are checked directly and stripped out before the rest of the comparison.
Run directly: python test_deck_roundtrip.py
"""
import sys
from pathlib import Path

from deck import Antenna, CircuitRequest, render_deck

SAMPLE_PATH = Path("/mnt/c/Users/chris/Desktop/CLAUDE/VOCAP FILES/itshfbc/run/voacapx.dat")

req = CircuitRequest(
    label_tx="FT.POLK",
    label_rx="FT.POLK",
    tx_lat=31.13,
    tx_lon=-93.27,  # W
    rx_lat=31.13,
    rx_lon=-93.27,
    month=5,
    year=2026,
    sunspot_number=101.0,
    tx_antenna=Antenna(sample_file="SAMPLE.23", bearing_deg=306.6, power_kw=0.02),
    rx_antenna=Antenna(sample_file="SAMPLE.23", bearing_deg=99.6, power_kw=0.0),
    frequencies_mhz=[6.07, 7.20, 9.70, 11.85, 13.70, 15.35, 17.73, 21.65, 25.89],
    noise_dbw=145.0,
    min_takeoff_angle_deg=3.00,
    required_reliability_pct=90.0,
    required_snr_db=27.0,
    multipath_power_tolerance_db=3.00,
    multipath_delay_tolerance_ms=0.10,
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
    if r != o and not (r.startswith("ANTENNA") and o.startswith("ANTENNA")):
        mismatches.append((i + 1, r, o))

if not mismatches and len(rendered_lines) == len(original_lines):
    print("MATCH (except expected ANTENNA path/case difference)")
    sys.exit(0)

for line_no, r, o in mismatches:
    print(f"line {line_no}:")
    print(f"  rendered: {r!r}")
    print(f"  original: {o!r}")
if len(rendered_lines) != len(original_lines):
    print(f"line count differs: rendered={len(rendered_lines)} original={len(original_lines)}")
sys.exit(1)
