"use strict";
/**
 * Parses a voacapl point-to-point .out file, which (per lib/deck.js's
 * two-pass tail) contains two tables back to back:
 *
 * 1. "FREQUENCY / RELIABILITY" (METHOD 24), e.g.:
 *
 *      GMT  LMT  MUF    6.1  7.2  9.7 11.9 13.7 15.4 17.7 21.6 25.9   -    -   MUF
 *
 *      1.0 18.8  9.0   1.00 1.00 0.93 0.57 0.09 0.00 0.00 0.00 0.01   -    -   0.97
 *      ...
 *
 * 2. A plain GMT/LMT/FOT/HPF/ESMUF/MUF/LUF table (METHOD 26), e.g.:
 *
 *      GMT   LMT    FOT    HPF  ESMUF    MUF    LUF
 *
 *      1.0  18.8   7.73  10.34   0.00   8.99   2.00
 *      ...
 *
 *    Added only to get real FOT values for the result page's chart - VOACAP
 *    computes FOT statistically (the frequency the monthly-median MUF
 *    exceeds ~90% of days), not as a fixed percentage below MUF, so there's
 *    no way to derive it from table 1's MUF column alone.
 *
 * Ported field-for-field from the verified Python parser.py.
 */

// Python's str.split() with no args: trim, then split on whitespace runs,
// dropping empty strings (unlike a plain JS .split(/\s+/), which leaves a
// leading "" for an already-trimmed empty string).
function pysplit(s) {
  const t = s.trim();
  return t === "" ? [] : t.split(/\s+/);
}

function parseOutput(text) {
  const lines = text.split(/\r?\n/);
  let headerIdx = null;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes("FREQUENCY / RELIABILITY")) {
      headerIdx = i;
      break;
    }
  }
  if (headerIdx === null) {
    throw new Error("no 'FREQUENCY / RELIABILITY' table found in output");
  }

  // the actual column header line ("GMT  LMT  MUF  6.1  7.2 ...") is the
  // next non-blank line after the section title
  let colLine = null;
  let j = headerIdx + 1;
  while (j < lines.length) {
    if (lines[j].trim()) {
      colLine = lines[j];
      break;
    }
    j += 1;
  }
  if (colLine === null) {
    throw new Error("no column header line found after FREQUENCY / RELIABILITY");
  }

  const tokens = pysplit(colLine);
  if (tokens[0] !== "GMT" || tokens[1] !== "LMT" || tokens[2] !== "MUF") {
    throw new Error(`unexpected column header: ${JSON.stringify(colLine)}`);
  }
  // tokens after GMT/LMT/MUF are frequency values, "-" placeholders, then
  // a trailing "MUF" label - keep only the real numeric frequency values
  const freqTokens = tokens.slice(3, -1); // drop trailing "MUF" label
  const frequencies = freqTokens.filter((t) => t !== "-").map(Number);
  const numFreqCols = freqTokens.length; // includes "-" placeholders, for column alignment

  const rows = [];
  let k = j + 1;
  while (k < lines.length) {
    const line = lines[k];
    const stripped = line.trim();
    if (!stripped) {
      k += 1;
      continue;
    }
    if (line.includes("END OF RUN") || !/[0-9]/.test(stripped[0])) {
      break;
    }
    const parts = pysplit(stripped);
    if (parts.length < 3 + numFreqCols + 1) {
      break;
    }
    const gmt = Number(parts[0]);
    const lmt = Number(parts[1]);
    const muf = Number(parts[2]);
    const relTokens = parts.slice(3, 3 + numFreqCols);
    const reliabilities = relTokens.filter((t) => t !== "-").map(Number);
    rows.push({ gmt, lmt, muf, reliabilities, fot: null });
    k += 1;
  }

  const fotByHour = parseFotByHour(lines.slice(k));
  for (const row of rows) {
    row.fot = fotByHour.has(row.gmt) ? fotByHour.get(row.gmt) : null;
  }

  return {
    frequenciesMhz: frequencies,
    rows,
    headerLines: parseHeaderBlock(lines),
    rawOutput: text,
  };
}

/**
 * Extracts the engine's own run-parameter header block - the summary the
 * original program prints above its charts - verbatim from the output, e.g.:
 *
 *   Jan    2026          SSN = 100.                Minimum Angle= 3.000 degrees
 *   CHICAGO             NEW YORK              AZIMUTHS          N. MI.      KM
 *   41.88 N   87.63 W - 40.72 N   74.00 W     91.92  280.94     618.0   1144.5
 *   XMTR  2-30 IONCAP #23[samples/sample.23    ] Az= 92.0 OFFaz=359.9   0.150kW
 *   RCVR  2-30 IONCAP #23[samples/sample.23    ] Az=281.0 OFFaz=359.9
 *   3 MHz NOISE = -145.0 dBW     REQ. REL = 45%    REQ. SNR = 27.0 dB
 *
 * Taken straight from the engine rather than rebuilt from our own inputs, so
 * the figures shown are provably the ones the engine actually used. That
 * distinction is not academic - a reconstruction differed from the engine on
 * real values: the engine reports 618.0 N.MI./1144.5 KM where a haversine
 * from the same coordinates gives 618.3/1145.0, and it computes a real
 * OFFaz (0.0/0.1/359.9/360.0 seen in testing) rather than a flat 0.0,
 * because the antenna bearing on the ANTENNA card is rounded to 1 decimal
 * while the azimuth it's compared against is not.
 *
 * The block sits just after the "~METHOD nn" page banner, and is terminated
 * by the first blank line. Only the METHOD 24 pass emits one (the METHOD 26
 * pass appended by lib/deck.js does not), so the first block found is the
 * only one. Note a multipath-tolerance line does NOT appear here even though
 * we do send those tolerances on the SYSTEM card - METHOD 24's header simply
 * doesn't echo them (verified: zero MULTIPATH lines in real output).
 *
 * Returns [] if no banner is found, so a header-less output degrades to
 * simply not rendering the block rather than failing the whole prediction.
 */
function parseHeaderBlock(lines) {
  let bannerIdx = null;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes("~METHOD")) {
      bannerIdx = i;
      break;
    }
  }
  if (bannerIdx === null) return [];

  const block = [];
  for (let i = bannerIdx + 1; i < lines.length; i++) {
    // strip the form-feed the engine uses for page breaks, then treat the
    // first blank line after the block's content as its terminator (leading
    // blanks between banner and content are skipped)
    const line = lines[i].replace(/\f/g, "");
    if (!line.trim()) {
      if (block.length === 0) continue;
      break;
    }
    block.push(line.replace(/\s+$/, ""));
  }
  return block;
}

// Parses the METHOD 26 GMT/LMT/FOT/HPF/ESMUF/MUF/LUF table (searched for
// only in the lines after the REL table, since its own column header also
// starts with "GMT" - the two tables would otherwise be ambiguous).
// Returns an empty Map if that table isn't present in this slice.
function parseFotByHour(lines) {
  const expected = ["GMT", "LMT", "FOT", "HPF", "ESMUF", "MUF", "LUF"];
  let headerIdx = null;
  for (let i = 0; i < lines.length; i++) {
    const tokens = pysplit(lines[i]).slice(0, 7);
    if (tokens.length === expected.length && tokens.every((t, idx) => t === expected[idx])) {
      headerIdx = i;
      break;
    }
  }
  const fotByHour = new Map();
  if (headerIdx === null) {
    return fotByHour;
  }

  for (const line of lines.slice(headerIdx + 1)) {
    const stripped = line.trim();
    if (!stripped) continue;
    if (!/[0-9]/.test(stripped[0])) break;
    const parts = pysplit(stripped);
    if (parts.length < 6) break;
    const gmt = Number(parts[0]);
    const fot = Number(parts[2]);
    fotByHour.set(gmt, fot);
  }
  return fotByHour;
}

module.exports = { parseOutput };
