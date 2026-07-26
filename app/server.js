"use strict";

const path = require("path");
const express = require("express");
const nunjucks = require("nunjucks");

const { ANTENNA_CATALOG, DEFAULT_ANTENNA } = require("./lib/antennas");
const { EngineError, runPrediction } = require("./lib/engine");
const { initialBearingDeg } = require("./lib/geo");
const { CITIES } = require("./lib/geoCities");

const app = express();
app.use("/static", express.static(path.join(__dirname, "static")));
app.use(express.urlencoded({ extended: false }));

nunjucks.configure(path.join(__dirname, "views"), {
  autoescape: true,
  express: app,
});

// Reshaped from [file, desc] tuples to {file, desc} objects so the
// template can use a plain `{% for antenna in antennas %}` loop.
const ANTENNA_OPTIONS = ANTENNA_CATALOG.map(([file, desc]) => ({ file, desc }));

// Loaded once at startup (see lib/geoCities.js) - passed to the P2P form as
// JSON for the TX/RX "pick a city" convenience datalist. Not filtered by
// the |lon| >= 100 limitation below (unlike the old circuit-preset list it
// replaced): with ~4000+ cities worldwide, trimming everything past that
// limit would silently hide entire continents rather than a handful of
// entries - the existing validation error already covers picks that don't
// fit, same as it does for manual entry.
const CITIES_JSON = JSON.stringify(CITIES);

// REL-vs-hour (+MUF, +FOT) chart data for result.njk - reuses the
// rows/reliabilities lib/parser.js already produces.
function chartData(result) {
  return JSON.stringify({
    hours: result.rows.map((r) => r.gmt),
    muf: result.rows.map((r) => r.muf),
    fot: result.rows.map((r) => r.fot),
    frequencies: result.frequenciesMhz,
    series: result.frequenciesMhz.map((_, i) =>
      result.rows.map((r) => (i < r.reliabilities.length ? r.reliabilities[i] : null))
    ),
  });
}

app.get("/healthz", (req, res) => {
  res.json({ status: "ok" });
});

function formDefaults() {
  return {
    label_tx: "",
    label_rx: "",
    tx_lat: "",
    tx_lon: "",
    rx_lat: "",
    rx_lon: "",
    month: 1,
    year: 2026,
    sunspot_number: 100,
    tx_antenna: DEFAULT_ANTENNA,
    rx_antenna: DEFAULT_ANTENNA,
    tx_power_kw: 1.0,
    // Classic VOACAP 9-frequency test set - each sits inside a standard
    // shortwave broadcast band (49m/41m/31m/25m/22m/19m/16m/13m/11m).
    frequencies_mhz: "6.07 7.20 9.70 11.85 13.70 15.35 17.73 21.65 25.89",
    noise_dbw: 145.0,
    min_takeoff_angle_deg: 3.0,
    required_reliability_pct: 45.0,
    required_snr_db: 27.0,
    multipath_power_tolerance_db: 3.0,
    multipath_delay_tolerance_ms: 0.1,
  };
}

app.get("/", (req, res) => {
  // Query params let a result page's "Edit inputs" link bring the user
  // back here with their last submission prefilled, instead of blank
  // defaults - see the hidden GET form in result.njk.
  const values = formDefaults();
  for (const key of Object.keys(values)) {
    if (req.query[key] !== undefined) values[key] = req.query[key];
  }
  res.render("index.njk", {
    antennas: ANTENNA_OPTIONS,
    cities_json: CITIES_JSON,
    ...values,
    error: null,
  });
});

// A form value the user can leave blank; falls back to `fallback` (mirrors
// FastAPI's Form(default) semantics for optional fields).
function numOr(value, fallback) {
  if (value === undefined || value === "") return fallback;
  return Number(value);
}

app.post("/predict", async (req, res) => {
  const b = req.body;
  const submitted = {
    label_tx: b.label_tx,
    label_rx: b.label_rx,
    tx_lat: Number(b.tx_lat),
    tx_lon: Number(b.tx_lon),
    rx_lat: Number(b.rx_lat),
    rx_lon: Number(b.rx_lon),
    month: parseInt(b.month, 10),
    year: parseInt(b.year, 10),
    sunspot_number: Number(b.sunspot_number),
    tx_antenna: b.tx_antenna,
    rx_antenna: b.rx_antenna,
    tx_power_kw: Number(b.tx_power_kw),
    frequencies_mhz: b.frequencies_mhz,
    noise_dbw: numOr(b.noise_dbw, 145.0),
    min_takeoff_angle_deg: numOr(b.min_takeoff_angle_deg, 3.0),
    required_reliability_pct: numOr(b.required_reliability_pct, 45.0),
    required_snr_db: numOr(b.required_snr_db, 27.0),
    multipath_power_tolerance_db: numOr(b.multipath_power_tolerance_db, 3.0),
    multipath_delay_tolerance_ms: numOr(b.multipath_delay_tolerance_ms, 0.1),
  };

  const errorPage = (message) => {
    res.render("index.njk", {
      antennas: ANTENNA_OPTIONS,
      cities_json: CITIES_JSON,
      ...submitted,
      error: message,
    });
  };

  if (!(submitted.tx_lat >= -90 && submitted.tx_lat <= 90) || !(submitted.rx_lat >= -90 && submitted.rx_lat <= 90)) {
    return errorPage("Latitude must be between -90 and 90.");
  }
  if (Math.abs(submitted.tx_lon) >= 100 || Math.abs(submitted.rx_lon) >= 100) {
    return errorPage(
      "Longitude magnitude must be under 100 (a known limitation of the " +
        "fixed-column input format used here - see lib/deck.js)."
    );
  }
  if (!(submitted.month >= 1 && submitted.month <= 12)) {
    return errorPage("Month must be between 1 and 12.");
  }

  const freqTokens = submitted.frequencies_mhz.replace(/,/g, " ").trim().split(/\s+/).filter(Boolean);
  const freqs = freqTokens.map(Number);
  if (freqTokens.length === 0 || freqs.some((f) => Number.isNaN(f))) {
    return errorPage("Frequencies must be a list of numbers (MHz), space or comma separated.");
  }
  if (freqs.length === 0 || freqs.length > 9) {
    return errorPage("Provide between 1 and 9 frequencies (MHz).");
  }

  const txBearing = initialBearingDeg(submitted.tx_lat, submitted.tx_lon, submitted.rx_lat, submitted.rx_lon);
  const rxBearing = initialBearingDeg(submitted.rx_lat, submitted.rx_lon, submitted.tx_lat, submitted.tx_lon);

  const circuitReq = {
    labelTx: submitted.label_tx,
    labelRx: submitted.label_rx,
    txLat: submitted.tx_lat,
    txLon: submitted.tx_lon,
    rxLat: submitted.rx_lat,
    rxLon: submitted.rx_lon,
    month: submitted.month,
    year: submitted.year,
    sunspotNumber: submitted.sunspot_number,
    txAntenna: { sampleFile: submitted.tx_antenna, bearingDeg: txBearing, powerKw: submitted.tx_power_kw },
    rxAntenna: { sampleFile: submitted.rx_antenna, bearingDeg: rxBearing, powerKw: 0.0 },
    frequenciesMhz: freqs,
    noiseDbw: submitted.noise_dbw,
    minTakeoffAngleDeg: submitted.min_takeoff_angle_deg,
    requiredReliabilityPct: submitted.required_reliability_pct,
    requiredSnrDb: submitted.required_snr_db,
    multipathPowerToleranceDb: submitted.multipath_power_tolerance_db,
    multipathDelayToleranceMs: submitted.multipath_delay_tolerance_ms,
  };

  let result;
  try {
    result = await runPrediction(circuitReq);
  } catch (e) {
    if (e instanceof EngineError) {
      return errorPage(`Prediction engine error: ${e.message}`);
    }
    throw e;
  }

  res.render("result.njk", {
    label_tx: submitted.label_tx,
    label_rx: submitted.label_rx,
    tx_bearing: Math.round(txBearing * 10) / 10,
    rx_bearing: Math.round(rxBearing * 10) / 10,
    chart_json: chartData(result),
    submitted,
  });
});

const port = process.env.PORT || 8000;
app.listen(port, "0.0.0.0", () => {
  console.log(`voacap web listening on ${port}`);
});
