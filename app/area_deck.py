"""
Renders a VOACAP area-coverage ".voa" project file for
`voacapl <itshfbc_dir> area calc <subdir>/<name>.voa`.

IMPORTANT, found only by actually running voacapl on a real cluster (the
manpage and the fixed-column card-deck format used by deck.py are both
red herrings for area mode - an earlier version of this module built a
CIRCUIT/AREA card deck like deck.py's, which looked plausible from
voacapl's own source but never actually worked):

  - The real user-facing input is this key:value ".voa" format (matching
    the Windows AREAWIN GUI's project files, e.g.
    VOCAP FILES/itshfbc/areadata/default/default.voa), NOT a fixed-column
    card deck.
  - It must live under `<itshfbc_dir>/areadata/<subdir>/<name>.voa` - a
    flat `areadata/<name>.voa` or an extension-less name is rejected.
  - Invocation is `voacapl <itshfbc_dir> area calc <subdir>/<name>.voa` -
    confirmed empirically: a bare name (with or without a subdirectory,
    with or without the .voa extension) is rejected as "not found";
    <subdir>/<name>.voa is the one combination that worked.
  - voacapl translates the .voa file into a card deck named exactly
    `run/voaareax.da1` - a SHARED, hardcoded intermediate file, not derived
    from the input filename. Concurrent area calc invocations race on this
    file - area_engine.py serializes them with a lock.
  - Output lands at `areadata/<subdir>/<name>.vg<N>`, one file per active
    frequency slot (N = 1-based slot index in the Freqs field below).

Column layout below is byte-measured against the real default.voa sample
for every clearly fixed-width numeric field (Transmit/Pcenter lat-lon,
Area, Gridsize, Months/Ssns/Hours/Freqs, System). The free-text label and
antenna-reference lines are close-but-not-byte-perfect reproductions of
that sample - correctness for those was confirmed the practical way, by
actually running this renderer's output through voacapl on the real
cluster and getting a valid .vg1 file back (see the plan's
Testing/Verification notes), not by further byte-diffing.

Latitude/longitude here use a wider, unlettered-magnitude-capped field
than deck.py's CIRCUIT-card _deg() (7 chars for lat, 10 for lon - see
_voa_deg below) and so, unlike the P2P form, DO support |lon| >= 100.
"""
from __future__ import annotations

from dataclasses import dataclass

from deck import Antenna

CRLF = "\r\n"


def _voa_deg(value: float, positive_letter: str, negative_letter: str, width: int) -> str:
    """.voa-format degree field: right-justified in `width`, unlike deck.py's
    _deg() this has no |value| < 100 cap - the field is wide enough for it."""
    letter = positive_letter if value >= 0 else negative_letter
    content = f"{abs(value):.2f}{letter}"
    return content.rjust(width)


@dataclass
class AreaRequest:
    label: str
    center_lat: float  # +N / -S
    center_lon: float  # +E / -W - no |value| < 100 cap, see module docstring
    radius_km: float  # symmetric box half-width in every direction
    grid_points: int  # grid is grid_points x grid_points
    hour_gmt: int  # area runs are a single-hour snapshot, not a 24h sweep
    month: int
    year: int
    sunspot_number: float
    tx_antenna: Antenna
    rx_antenna: Antenna
    frequency_mhz: float  # exactly one frequency per run - see area_engine.py
    noise_dbw: float = 145.0
    min_takeoff_angle_deg: float = 3.00
    required_reliability_pct: float = 90.0
    required_snr_db: float = 27.0
    multipath_power_tolerance_db: float = 3.00
    multipath_delay_tolerance_ms: float = 0.10


def _kv(label: str, value: str) -> str:
    return f"{label:<9}:{value}" + CRLF


def _nine_slot(first: float, width: int, decimals: int) -> str:
    values = [first] + [0.0] * 8
    return "".join(f"{v:{width}.{decimals}f}" for v in values)


def render_area_voa(req: AreaRequest) -> str:
    """Render a full VOACAP area-coverage .voa project file."""
    lat = _voa_deg(req.center_lat, "N", "S", 7)
    lon = _voa_deg(req.center_lon, "E", "W", 10)
    label = req.label[:20].ljust(21)

    lines = [
        _kv("Model", "VOACAP"),
        _kv("Colors", "Black    :Blue     :Ignore   :Ignore   :Red      :Black with shading"),
        _kv("Cities", "Receive.cty"),
        _kv("Nparms", f"{4:5d}"),
        _kv("Parameter", "MUF      0"),
        _kv("Parameter", "DBU      0"),
        _kv("Parameter", "SNRxx    0"),
        _kv("Parameter", "REL      0"),
        _kv("Transmit", f"{lat}{lon}   {label}Short"),
        _kv("Pcenter", f"{lat}{lon}   {req.label[:20]}"),
        _kv(
            "Area",
            f"{-req.radius_km:10.1f}{req.radius_km:10.1f}{-req.radius_km:10.1f}{req.radius_km:10.1f}",
        ),
        _kv("Gridsize", f"{req.grid_points:5d}{0:5d}"),
        _kv("Method", f"{30:5d}"),
        _kv("Coeffs", "CCIR"),
        _kv("Months", _nine_slot(float(req.month), 7, 2)),
        _kv("Ssns", _nine_slot(req.sunspot_number, 7, 0)),
        _kv("Hours", _nine_slot(float(req.hour_gmt), 7, 0)),
        _kv("Freqs", _nine_slot(req.frequency_mhz, 7, 3)),
        _kv(
            "System",
            f"{req.noise_dbw:5.0f}"
            f"{req.min_takeoff_angle_deg:10.3f}"
            f"{req.required_reliability_pct:5.0f}"
            f"{req.required_snr_db:5.0f}"
            f"{req.multipath_power_tolerance_db:10.3f}"
            f"{req.multipath_delay_tolerance_ms:10.3f}",
        ),
        _kv("Fprob", " 1.00 1.00 1.00 0.00"),
        _kv(
            "Rec Ants",
            f"[samples/{req.rx_antenna.sample_file.lower():<13}]  gain={0.0:5.1f}{0.0:6.1f}",
        ),
        _kv(
            "Tx Ants",
            f"[samples/{req.tx_antenna.sample_file.lower():<13}]{0.0:7.3f}"
            f"{req.tx_antenna.bearing_deg:6.1f}{req.tx_antenna.power_kw:10.4f}",
        ),
    ]
    return "".join(lines)
