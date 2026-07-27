"use strict";
/**
 * Renders a VOACAP "card deck" input file (the fixed-column text format the
 * voacapl engine reads) from simple JS values.
 *
 * Column layout was reverse-engineered from the known-good sample decks
 * shipped with the original Windows ITSHFBC install (see the Python
 * version's test_deck_roundtrip.py/test_deck_roundtrip2.py, which
 * byte-diffed a re-rendered voacapx.dat/voacapd.dat against the originals
 * as the source of truth for this format) - ported field-for-field from
 * that verified Python implementation, not re-derived from scratch.
 *
 * We run the engine via voacapl (github.com/jawatson/voacapl), a native
 * Linux Fortran port, rather than the original Win32 binaries under Wine -
 * voacapl reads the same card-deck format, with two Linux-specific
 * differences from the Windows original: antenna paths use a forward
 * slash instead of backslash, and antenna filenames are lowercase (Linux
 * filesystems are case-sensitive; Windows' aren't) - see antennaCard below.
 *
 * Known limitation (v1): latitude/longitude fields are 5-char widths that
 * comfortably fit 2-digit degrees (0-99.99). 3-digit longitudes (100-180)
 * would overflow the field and misalign every column after it - reject
 * those in the route handler rather than silently emitting a malformed deck.
 *
 * Expected CircuitRequest shape:
 *   { labelTx, labelRx, txLat, txLon, rxLat, rxLon, month, year,
 *     sunspotNumber, txAntenna: {sampleFile, bearingDeg, powerKw},
 *     rxAntenna: {sampleFile, bearingDeg, powerKw}, frequenciesMhz,
 *     noiseDbw, minTakeoffAngleDeg, requiredReliabilityPct, requiredSnrDb,
 *     multipathPowerToleranceDb, multipathDelayToleranceMs }
 */

const CRLF = "\r\n";

// Fixed-width formatting matching Python's f"{value:{width}.{decimals}f}"
// (right-justified, space-padded, never truncated if the content is
// already >= width - same as Python's behavior for oversized values).
function fixed(value, decimals, width) {
  const s = Number(value).toFixed(decimals);
  return width ? s.padStart(width, " ") : s;
}

// Formats a signed degree value as VOACAP's 5-char '%5.2f' + hemisphere
// letter field, e.g. 31.13 -> '31.13N', -93.27 -> '93.27W'.
function deg(value, positiveLetter, negativeLetter) {
  if (Math.abs(value) >= 100) {
    throw new Error(`degree magnitude ${value} >= 100 is not supported (see module docstring)`);
  }
  const letter = value >= 0 ? positiveLetter : negativeLetter;
  return fixed(Math.abs(value), 2, 5) + letter;
}

// Cards that never vary for v1 - copied verbatim from the known-good
// voacapx.dat sample so their exact column layout is guaranteed correct.
const BOILERPLATE_HEAD =
  "COMMENT    Any VOACAP default cards may be placed in the file: VOACAP.DEF" +
  CRLF +
  "LINEMAX      55       number of lines-per-page" +
  CRLF +
  "COEFFS    CCIR" +
  CRLF +
  "TIME          1   24    1    1" +
  CRLF;

const BOILERPLATE_TAIL =
  "METHOD       24    0" +
  CRLF +
  "EXECUTE" +
  CRLF +
  // Second pass, same circuit/frequencies: METHOD 26's GMT/LMT/FOT/HPF/
  // ESMUF/MUF/LUF table is the only way to get VOACAP's real FOT (the
  // statistically-derived "frequency of optimum transmission" - varies
  // ~13-19% below MUF depending on hour in testing, NOT a fixed 15%
  // below MUF as sometimes assumed) for the result page's chart, without
  // a second voacapl invocation. See lib/parser.js's parseFotByHour.
  "METHOD       26    0" +
  CRLF +
  "EXECUTE" +
  CRLF +
  "QUIT" +
  CRLF;

function monthCard(month, year) {
  // "MONTH" + 6 spaces + YYYY + %5.2f month-number, e.g. for May 2026:
  // "MONTH      2026 5.00" (the second value is the calendar month, NOT
  // the sunspot number - that's the separate SUNSPOT card below)
  return "MONTH      " + String(year).padStart(4, " ") + fixed(month, 2, 5) + CRLF;
}

function sunspotCard(ssn) {
  // kept as a separate SUNSPOT card too - some samples set both; VOACAP
  // reads whichever it expects, having both is harmless and matches
  // voacapx.dat exactly ("SUNSPOT    101.")
  return "SUNSPOT    " + fixed(ssn, 0, 3) + "." + CRLF;
}

function labelCard(tx, rx) {
  // "LABEL     " (10 chars) + tx left-justified in 20 + rx left-justified
  //
  // Both labels are truncated to the 20-char field width, matching what the
  // original program does (its header shows e.g. "NAVAL AVIONICS CENTE").
  // This is a correctness fix, not just cosmetic: verified against the real
  // engine, a tx label longer than 20 chars runs straight into the rx label
  // with no separator ("...INDIANAPOLISFORT GORDON...") and the engine then
  // echoes that single mashed-together string as the circuit name.
  return "LABEL     " + tx.slice(0, 20).padEnd(20, " ") + rx.slice(0, 20) + CRLF;
}

function circuitCard(req) {
  const txLat = deg(req.txLat, "N", "S");
  const txLon = deg(req.txLon, "E", "W");
  const rxLat = deg(req.rxLat, "N", "S");
  const rxLon = deg(req.rxLon, "E", "W");
  return `CIRCUIT   ${txLat}    ${txLon}    ${rxLat}    ${rxLon}  S     0` + CRLF;
}

function systemCard(req) {
  return (
    "SYSTEM       1." +
    ` ${fixed(req.noiseDbw, 0, 3)}.` +
    ` ${fixed(req.minTakeoffAngleDeg, 2, 4)}` +
    `  ${fixed(req.requiredReliabilityPct, 0, 2)}.` +
    ` ${fixed(req.requiredSnrDb, 1, 4)}` +
    ` ${fixed(req.multipathPowerToleranceDb, 2, 4)}` +
    ` ${fixed(req.multipathDelayToleranceMs, 2, 4)}` +
    CRLF
  );
}

function fprobCard() {
  return "FPROB      1.00 1.00 1.00 0.00" + CRLF;
}

function antennaCard(slot, ant) {
  // ANTENNA <slot> <slot> 2 30 0.000[samples/sample.nn    ]<bearing>    <power>
  // voacapl (the Linux engine) needs a forward slash and lowercase filename -
  // the original Windows itshfbc used a backslash and uppercase (case
  // insensitive there; Linux filesystems are case-sensitive).
  const samplePadded = ant.sampleFile.toLowerCase().padEnd(13, " ");
  return (
    `ANTENNA       ${slot}    ${slot}    2   30     0.000[samples/${samplePadded}]` +
    `${fixed(ant.bearingDeg, 1, 5)}    ${Number(ant.powerKw).toFixed(4)}` +
    CRLF
  );
}

function frequencyCard(freqs) {
  if (freqs.length > 9) {
    throw new Error("at most 9 frequencies are supported per the sample card width");
  }
  const padded = freqs.concat(Array(9 - freqs.length).fill(0.0));
  const parts = padded.map((f) => fixed(f, 2, 5)).join("");
  return `FREQUENCY ${parts} 0.00 0.00` + CRLF;
}

// Renders a full VOACAP point-to-point circuit input deck.
function renderDeck(req) {
  return (
    BOILERPLATE_HEAD +
    monthCard(req.month, req.year) +
    sunspotCard(req.sunspotNumber) +
    labelCard(req.labelTx, req.labelRx) +
    circuitCard(req) +
    systemCard(req) +
    fprobCard() +
    antennaCard(1, req.txAntenna) +
    antennaCard(2, req.rxAntenna) +
    frequencyCard(req.frequenciesMhz) +
    BOILERPLATE_TAIL
  );
}

module.exports = { renderDeck };
