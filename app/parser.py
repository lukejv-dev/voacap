"""
Parses a voacapl point-to-point .out file, which (per deck.py's two-pass
tail) contains two tables back to back:

1. "FREQUENCY / RELIABILITY" (METHOD 24), e.g.:

     GMT  LMT  MUF    6.1  7.2  9.7 11.9 13.7 15.4 17.7 21.6 25.9   -    -   MUF

     1.0 18.8  9.0   1.00 1.00 0.93 0.57 0.09 0.00 0.00 0.00 0.01   -    -   0.97
     ...

2. A plain GMT/LMT/FOT/HPF/ESMUF/MUF/LUF table (METHOD 26), e.g.:

     GMT   LMT    FOT    HPF  ESMUF    MUF    LUF

     1.0  18.8   7.73  10.34   0.00   8.99   2.00
     ...

   Added only to get real FOT values for the result page's chart - VOACAP
   computes FOT statistically (the frequency the monthly-median MUF
   exceeds ~90% of days), not as a fixed percentage below MUF, so there's
   no way to derive it from table 1's MUF column alone.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HourRow:
    gmt: float
    lmt: float
    muf: float
    reliabilities: list  # one per requested frequency, aligned to header order
    fot: float = None  # from the METHOD 26 table; None if that table is missing


@dataclass
class PredictionResult:
    frequencies_mhz: list
    rows: list  # list[HourRow]
    raw_output: str


def parse_output(text: str) -> PredictionResult:
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "FREQUENCY / RELIABILITY" in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("no 'FREQUENCY / RELIABILITY' table found in output")

    # the actual column header line ("GMT  LMT  MUF  6.1  7.2 ...") is the
    # next non-blank line after the section title
    col_line = None
    j = header_idx + 1
    while j < len(lines):
        if lines[j].strip():
            col_line = lines[j]
            break
        j += 1
    if col_line is None:
        raise ValueError("no column header line found after FREQUENCY / RELIABILITY")

    tokens = col_line.split()
    if tokens[:3] != ["GMT", "LMT", "MUF"]:
        raise ValueError(f"unexpected column header: {col_line!r}")
    # tokens after GMT/LMT/MUF are frequency values, "-" placeholders, then
    # a trailing "MUF" label - keep only the real numeric frequency values
    freq_tokens = tokens[3:-1]  # drop trailing "MUF" label
    frequencies = [float(t) for t in freq_tokens if t != "-"]
    num_freq_cols = len(freq_tokens)  # includes "-" placeholders, for column alignment

    rows: list[HourRow] = []
    k = j + 1
    while k < len(lines):
        line = lines[k]
        stripped = line.strip()
        if not stripped:
            k += 1
            continue
        if "END OF RUN" in line or not stripped[0].isdigit():
            break
        parts = stripped.split()
        # gmt, lmt, muf, <num_freq_cols reliability/placeholder values>, trailing muf
        if len(parts) < 3 + num_freq_cols + 1:
            break
        gmt, lmt, muf = float(parts[0]), float(parts[1]), float(parts[2])
        rel_tokens = parts[3 : 3 + num_freq_cols]
        reliabilities = [float(t) for t in rel_tokens if t != "-"]
        rows.append(HourRow(gmt=gmt, lmt=lmt, muf=muf, reliabilities=reliabilities))
        k += 1

    fot_by_hour = _parse_fot_by_hour(lines[k:])
    for row in rows:
        row.fot = fot_by_hour.get(row.gmt)

    return PredictionResult(frequencies_mhz=frequencies, rows=rows, raw_output=text)


def _parse_fot_by_hour(lines: list) -> dict:
    """Parses the METHOD 26 GMT/LMT/FOT/HPF/ESMUF/MUF/LUF table (searched
    for only in the lines after the REL table, since its own column header
    also starts with "GMT" - the two tables would otherwise be
    ambiguous). Returns {} if that table isn't present in this slice."""
    header_idx = None
    for i, line in enumerate(lines):
        tokens = line.split()
        if tokens[:7] == ["GMT", "LMT", "FOT", "HPF", "ESMUF", "MUF", "LUF"]:
            header_idx = i
            break
    if header_idx is None:
        return {}

    fot_by_hour = {}
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped[0].isdigit():
            break
        parts = stripped.split()
        if len(parts) < 6:
            break
        gmt, fot = float(parts[0]), float(parts[2])
        fot_by_hour[gmt] = fot
    return fot_by_hour
