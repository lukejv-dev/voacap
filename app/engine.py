"""Runs the voacapl engine for one prediction request."""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

from deck import CircuitRequest, render_deck
from parser import PredictionResult, parse_output

ITSHFBC_DIR = Path(os.environ.get("ITSHFBC_DIR", "/opt/itshfbc"))
VOACAPL_BIN = os.environ.get("VOACAPL_BIN", "/usr/local/bin/voacapl")
RUN_DIR = ITSHFBC_DIR / "run"

# voacapl always resolves input/output filenames against <ITSHFBC_DIR>/run/
# directly (no subdirectory support confirmed) - concurrent requests use
# unique per-request filenames in that same directory instead.


class EngineError(RuntimeError):
    pass


def run_prediction_sync(req: CircuitRequest) -> PredictionResult:
    job_id = uuid.uuid4().hex[:12]
    input_name = f"job_{job_id}.dat"
    output_name = f"job_{job_id}.out"
    input_path = RUN_DIR / input_name
    output_path = RUN_DIR / output_name

    input_path.write_text(render_deck(req), newline="")
    try:
        proc = subprocess.run(
            [VOACAPL_BIN, str(ITSHFBC_DIR), input_name, output_name],
            cwd=str(ITSHFBC_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if not output_path.exists():
            raise EngineError(
                f"voacapl did not produce output (rc={proc.returncode}): "
                f"{proc.stdout}\n{proc.stderr}"
            )
        output_text = output_path.read_text()
        if "END OF RUN" not in output_text:
            raise EngineError(f"voacapl output looks incomplete:\n{output_text}")
        return parse_output(output_text)
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


async def run_prediction(req: CircuitRequest) -> PredictionResult:
    return await asyncio.to_thread(run_prediction_sync, req)
