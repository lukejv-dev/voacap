"use strict";
// Runs the voacapl engine for one prediction request.

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFile } = require("child_process");
const { renderDeck } = require("./deck");
const { parseOutput } = require("./parser");

const ITSHFBC_DIR = process.env.ITSHFBC_DIR || "/opt/itshfbc";
const VOACAPL_BIN = process.env.VOACAPL_BIN || "/usr/local/bin/voacapl";
const RUN_DIR = path.join(ITSHFBC_DIR, "run");

// voacapl always resolves input/output filenames against <ITSHFBC_DIR>/run/
// directly (no subdirectory support confirmed) - concurrent requests use
// unique per-request filenames in that same directory instead.

class EngineError extends Error {}

function execFileAsync(bin, args, options) {
  return new Promise((resolve) => {
    // voacapl's exit code isn't checked here (matches the Python original) -
    // success/failure is determined below by whether the output file
    // exists and contains "END OF RUN", not by return code.
    execFile(bin, args, options, (error, stdout, stderr) => {
      resolve({ code: error ? error.code : 0, stdout: stdout || "", stderr: stderr || "" });
    });
  });
}

async function runPrediction(req) {
  const jobId = crypto.randomBytes(6).toString("hex");
  const inputName = `job_${jobId}.dat`;
  const outputName = `job_${jobId}.out`;
  const inputPath = path.join(RUN_DIR, inputName);
  const outputPath = path.join(RUN_DIR, outputName);

  fs.writeFileSync(inputPath, renderDeck(req));
  try {
    const { code, stdout, stderr } = await execFileAsync(
      VOACAPL_BIN,
      [ITSHFBC_DIR, inputName, outputName],
      { cwd: ITSHFBC_DIR, timeout: 30000 }
    );
    if (!fs.existsSync(outputPath)) {
      throw new EngineError(`voacapl did not produce output (rc=${code}): ${stdout}\n${stderr}`);
    }
    const outputText = fs.readFileSync(outputPath, "utf8");
    if (!outputText.includes("END OF RUN")) {
      throw new EngineError(`voacapl output looks incomplete:\n${outputText}`);
    }
    return parseOutput(outputText);
  } finally {
    fs.rmSync(inputPath, { force: true });
    fs.rmSync(outputPath, { force: true });
  }
}

module.exports = { EngineError, runPrediction };
