"""
Runs the voacapl engine in area-coverage mode for one (request, frequency)
pair. Mirrors engine.py's subprocess pattern, with the differences below -
all found by actually running voacapl on the real cluster, not from the
manpage (see area_deck.py's module docstring for the full story):

  - Input lives at itshfbc/areadata/<subdir>/<name>.voa (a fresh subdir per
    request, so cleanup is one rmtree instead of tracking loose files).
  - Invocation: `voacapl <itshfbc_dir> area calc <subdir>/<name>.voa`.
  - voacapl translates that into a card deck at the SHARED, hardcoded path
    itshfbc/run/voaareax.da1 - not derived from our input filename, so two
    concurrent area calc runs would clobber each other's intermediate
    file. AREA_CALC_LOCK below serializes all area calc invocations
    cluster-pod-wide to avoid that - a real constraint, not caution for
    its own sake, since this was observed directly (see the plan's
    Testing/Verification notes for the run that revealed it).
  - Output lands at itshfbc/areadata/<subdir>/<name>.vg1 (slot 1, since
    AreaRequest always puts its one frequency in the first Freqs slot -
    see area_deck.py).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from area_deck import AreaRequest, render_area_voa
from area_parser import AreaResult, parse_area_output

ITSHFBC_DIR = Path(os.environ.get("ITSHFBC_DIR", "/opt/itshfbc"))
VOACAPL_BIN = os.environ.get("VOACAPL_BIN", "/usr/local/bin/voacapl")
AREADATA_DIR = ITSHFBC_DIR / "areadata"

# Area runs iterate a full grid instead of one circuit, so they take longer
# than a P2P prediction - separate, larger timeout from engine.py's 30s,
# to be tuned once real grid timings are measured at production grid sizes
# (see Testing/Verification - a 242x242 grid completed well within this).
AREA_TIMEOUT_SECONDS = 120

# Serializes voacapl area-calc invocations across the whole process - see
# module docstring re: the shared run/voaareax.da1 intermediate file.
AREA_CALC_LOCK = asyncio.Lock()


class AreaEngineError(RuntimeError):
    pass


def run_area_prediction_sync(req: AreaRequest) -> AreaResult:
    job_id = uuid.uuid4().hex[:12]
    subdir_name = f"area_{job_id}"
    job_dir = AREADATA_DIR / subdir_name
    job_dir.mkdir(parents=True, exist_ok=True)
    voa_name = "req"
    voa_path = job_dir / f"{voa_name}.voa"
    output_path = job_dir / f"{voa_name}.vg1"

    voa_path.write_text(render_area_voa(req), newline="")
    try:
        proc = subprocess.run(
            [VOACAPL_BIN, str(ITSHFBC_DIR), "area", "calc", f"{subdir_name}/{voa_name}.voa"],
            cwd=str(ITSHFBC_DIR),
            capture_output=True,
            text=True,
            timeout=AREA_TIMEOUT_SECONDS,
        )
        if not output_path.exists():
            raise AreaEngineError(
                f"voacapl area calc did not produce output (rc={proc.returncode}): "
                f"{proc.stdout}\n{proc.stderr}"
            )
        output_text = output_path.read_text()
        try:
            return parse_area_output(output_text)
        except ValueError as e:
            raise AreaEngineError(f"could not parse area output: {e}") from e
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


async def run_area_prediction(req: AreaRequest) -> AreaResult:
    async with AREA_CALC_LOCK:
        return await asyncio.to_thread(run_area_prediction_sync, req)
