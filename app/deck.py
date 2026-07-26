"""
Renders a VOACAP "card deck" input file (the fixed-column text format the
voacapl engine reads) from simple Python values.

Column layout was reverse-engineered from the known-good sample decks
shipped with the original Windows ITSHFBC install (itshfbc/run/voacapx.dat,
voacapg.dat, voacapd.dat) - see test_deck_roundtrip.py/test_deck_roundtrip2.py,
which byte-diff a re-rendered voacapx.dat/voacapd.dat against the originals
as the source of truth for this format.

We run the engine via voacapl (github.com/jawatson/voacapl), a native Linux
Fortran port, rather than the original Win32 binaries under Wine - Wine hit
a genuine internal livelock (confirmed via strace: a busy-wait between two
threads exchanging the same message thousands of times/sec, never
resolving) with this specific Salford FTN95-compiled binary. voacapl reads
the same card-deck format, with two Linux-specific differences from the
Windows original: antenna paths use a forward slash instead of backslash,
and antenna filenames are lowercase (Linux filesystems are case-sensitive;
Windows' aren't) - see _antenna_card below.

Known limitation (v1): latitude/longitude fields are 5-char widths that
comfortably fit 2-digit degrees (0-99.99). 3-digit longitudes (100-180)
would overflow the field and misalign every column after it - reject
those in the form layer rather than silently emitting a malformed deck.
"""
from __future__ import annotations

from dataclasses import dataclass

CRLF = "\r\n"


def _deg(value: float, positive_letter: str, negative_letter: str) -> str:
    """Format a signed degree value as VOACAP's 5-char '%5.2f' + hemisphere
    letter field, e.g. 31.13 -> '31.13N', -93.27 -> '93.27W'."""
    if abs(value) >= 100:
        raise ValueError(
            f"degree magnitude {value} >= 100 is not supported (see module docstring)"
        )
    letter = positive_letter if value >= 0 else negative_letter
    return f"{abs(value):5.2f}{letter}"


@dataclass
class Antenna:
    sample_file: str  # e.g. "SAMPLE.23" (must exist under antennas/samples/)
    bearing_deg: float  # antenna bearing, degrees
    power_kw: float = 0.0  # 0.0 for the receive antenna


@dataclass
class CircuitRequest:
    label_tx: str
    label_rx: str
    tx_lat: float  # +N / -S
    tx_lon: float  # +E / -W (see _deg - note sign convention below)
    rx_lat: float
    rx_lon: float
    month: int
    year: int
    sunspot_number: float
    tx_antenna: Antenna
    rx_antenna: Antenna
    frequencies_mhz: list[float]  # up to 9 values, matches sample width
    noise_dbw: float = 145.0
    min_takeoff_angle_deg: float = 3.00
    required_reliability_pct: float = 90.0
    required_snr_db: float = 27.0
    multipath_power_tolerance_db: float = 3.00
    multipath_delay_tolerance_ms: float = 0.10


# Cards that never vary for v1 - copied verbatim from the known-good
# voacapx.dat sample so their exact column layout is guaranteed correct.
_BOILERPLATE_HEAD = (
    "COMMENT    Any VOACAP default cards may be placed in the file: VOACAP.DEF"
    + CRLF
    + "LINEMAX      55       number of lines-per-page"
    + CRLF
    + "COEFFS    CCIR"
    + CRLF
    + "TIME          1   24    1    1"
    + CRLF
)
_BOILERPLATE_TAIL = (
    "METHOD       24    0"
    + CRLF
    + "EXECUTE"
    + CRLF
    # Second pass, same circuit/frequencies: METHOD 26's GMT/LMT/FOT/HPF/
    # ESMUF/MUF/LUF table is the only way to get VOACAP's real FOT (the
    # statistically-derived "frequency of optimum transmission" - varies
    # ~13-19% below MUF depending on hour in testing, NOT a fixed 15%
    # below MUF as sometimes assumed) for the result page's chart, without
    # a second voacapl invocation. See parser.py's parse_fot_by_hour.
    + "METHOD       26    0"
    + CRLF
    + "EXECUTE"
    + CRLF
    + "QUIT"
    + CRLF
)


def _month_card(month: int, year: int) -> str:
    # "MONTH" + 6 spaces + YYYY + %5.2f month-number, e.g. for May 2026:
    # "MONTH      2026 5.00" (the second value is the calendar month, NOT
    # the sunspot number - that's the separate SUNSPOT card below)
    return f"MONTH      {year:4d}{float(month):5.2f}" + CRLF


def _sunspot_card(ssn: float) -> str:
    # kept as a separate SUNSPOT card too - some samples set both; VOACAP
    # reads whichever it expects, having both is harmless and matches
    # voacapx.dat exactly ("SUNSPOT    101.")
    return f"SUNSPOT    {ssn:3.0f}." + CRLF


def _label_card(tx: str, rx: str) -> str:
    # "LABEL     " (10 chars) + tx left-justified in 20 + rx left-justified
    return f"LABEL     {tx:<20}{rx}" + CRLF


def _circuit_card(req: CircuitRequest) -> str:
    tx_lat = _deg(req.tx_lat, "N", "S")
    tx_lon = _deg(req.tx_lon, "E", "W")
    rx_lat = _deg(req.rx_lat, "N", "S")
    rx_lon = _deg(req.rx_lon, "E", "W")
    return (
        f"CIRCUIT   {tx_lat}    {tx_lon}    {rx_lat}    {rx_lon}  S     0"
        + CRLF
    )


def _system_card(req: CircuitRequest) -> str:
    return (
        "SYSTEM       1."
        f" {req.noise_dbw:3.0f}."
        f" {req.min_takeoff_angle_deg:4.2f}"
        f"  {req.required_reliability_pct:2.0f}."
        f" {req.required_snr_db:4.1f}"
        f" {req.multipath_power_tolerance_db:4.2f}"
        f" {req.multipath_delay_tolerance_ms:4.2f}"
        + CRLF
    )


def _fprob_card() -> str:
    return "FPROB      1.00 1.00 1.00 0.00" + CRLF


def _antenna_card(slot: int, ant: Antenna) -> str:
    # ANTENNA <slot> <slot> 2 30 0.000[samples/sample.nn    ]<bearing>    <power>
    # voacapl (the Linux engine) needs a forward slash and lowercase filename -
    # the original Windows itshfbc used a backslash and uppercase (case
    # insensitive there; Linux filesystems are case-sensitive).
    sample_padded = f"{ant.sample_file.lower():<13}"
    return (
        f"ANTENNA       {slot}    {slot}    2   30     0.000[samples/{sample_padded}]"
        f"{ant.bearing_deg:5.1f}    {ant.power_kw:.4f}"
        + CRLF
    )


def _frequency_card(freqs: list[float]) -> str:
    if len(freqs) > 9:
        raise ValueError("at most 9 frequencies are supported per the sample card width")
    padded = list(freqs) + [0.0] * (9 - len(freqs))
    parts = "".join(f"{f:5.2f}" for f in padded)
    return f"FREQUENCY {parts} 0.00 0.00" + CRLF


def render_deck(req: CircuitRequest) -> str:
    """Render a full VOACAP point-to-point circuit input deck."""
    return (
        _BOILERPLATE_HEAD
        + _month_card(req.month, req.year)
        + _sunspot_card(req.sunspot_number)
        + _label_card(req.label_tx, req.label_rx)
        + _circuit_card(req)
        + _system_card(req)
        + _fprob_card()
        + _antenna_card(1, req.tx_antenna)
        + _antenna_card(2, req.rx_antenna)
        + _frequency_card(req.frequencies_mhz)
        + _BOILERPLATE_TAIL
    )
