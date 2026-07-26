"""
Runs a list of independent P2P circuits concurrently, reusing the existing,
already-working engine.run_prediction() per circuit rather than inventing a
new VOACAP input format - a batch is just "run N CircuitRequests, collect N
results". Built generically enough that a later parameter-sweep mode (same
TX/RX, varying month/SSN/antenna) can reuse this runner too: it just needs
a different function to build the list of CircuitRequests.

Job state is an in-memory dict - acceptable given the voacap Deployment is
pinned to replicas: 1 (ansible/roles/voacap/templates/deployment.yaml.j2);
a pod restart loses in-flight jobs. Not persisted to a PVC on purpose - add
that later only if it's actually a problem in practice, not speculatively.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from deck import Antenna, CircuitRequest
from engine import EngineError, run_prediction
from geo import initial_bearing_deg
from parser import PredictionResult

# Concurrency cap, not a count-of-circuits cap - each concurrent circuit is
# its own voacapl subprocess. Sized to the pod's CPU limit (see
# ansible/roles/voacap/templates/deployment.yaml.j2); tune both together.
MAX_CONCURRENT_CIRCUITS = 4


@dataclass
class BatchCircuit:
    label_tx: str
    label_rx: str
    tx_lat: float
    tx_lon: float
    rx_lat: float
    rx_lon: float


@dataclass
class BatchCircuitResult:
    circuit: BatchCircuit
    tx_bearing: float
    rx_bearing: float
    result: PredictionResult | None = None
    error: str | None = None


@dataclass
class BatchJob:
    job_id: str
    total: int
    required_reliability_pct: float
    submitted: dict = field(default_factory=dict)  # raw form values, for the result page's "Edit inputs" link
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed: list = field(default_factory=list)  # list[BatchCircuitResult]
    done: bool = False


# In-memory job store - see module docstring for why this is fine for v1.
_JOBS: dict[str, BatchJob] = {}


def get_job(job_id: str) -> BatchJob | None:
    return _JOBS.get(job_id)


def _shared_circuit_request(
    circuit: BatchCircuit,
    *,
    month: int,
    year: int,
    sunspot_number: float,
    tx_antenna: str,
    rx_antenna: str,
    tx_power_kw: float,
    frequencies_mhz: list,
    noise_dbw: float,
    min_takeoff_angle_deg: float,
    required_reliability_pct: float,
    required_snr_db: float,
    multipath_power_tolerance_db: float,
    multipath_delay_tolerance_ms: float,
) -> tuple[CircuitRequest, float, float]:
    tx_bearing = initial_bearing_deg(circuit.tx_lat, circuit.tx_lon, circuit.rx_lat, circuit.rx_lon)
    rx_bearing = initial_bearing_deg(circuit.rx_lat, circuit.rx_lon, circuit.tx_lat, circuit.tx_lon)
    req = CircuitRequest(
        label_tx=circuit.label_tx,
        label_rx=circuit.label_rx,
        tx_lat=circuit.tx_lat,
        tx_lon=circuit.tx_lon,
        rx_lat=circuit.rx_lat,
        rx_lon=circuit.rx_lon,
        month=month,
        year=year,
        sunspot_number=sunspot_number,
        tx_antenna=Antenna(sample_file=tx_antenna, bearing_deg=tx_bearing, power_kw=tx_power_kw),
        rx_antenna=Antenna(sample_file=rx_antenna, bearing_deg=rx_bearing, power_kw=0.0),
        frequencies_mhz=frequencies_mhz,
        noise_dbw=noise_dbw,
        min_takeoff_angle_deg=min_takeoff_angle_deg,
        required_reliability_pct=required_reliability_pct,
        required_snr_db=required_snr_db,
        multipath_power_tolerance_db=multipath_power_tolerance_db,
        multipath_delay_tolerance_ms=multipath_delay_tolerance_ms,
    )
    return req, tx_bearing, rx_bearing


async def _run_one(circuit: BatchCircuit, semaphore: asyncio.Semaphore, **shared_params) -> BatchCircuitResult:
    req, tx_bearing, rx_bearing = _shared_circuit_request(circuit, **shared_params)
    async with semaphore:
        try:
            result = await run_prediction(req)
        except EngineError as e:
            return BatchCircuitResult(circuit=circuit, tx_bearing=tx_bearing, rx_bearing=rx_bearing, error=str(e))
    return BatchCircuitResult(circuit=circuit, tx_bearing=tx_bearing, rx_bearing=rx_bearing, result=result)


def summarize(result: PredictionResult, required_reliability_pct: float) -> dict:
    """Picks the frequency meeting the reliability threshold for the most
    hours, for the batch summary table's "best frequency" column."""
    threshold = required_reliability_pct / 100.0
    best_freq = None
    best_hours = -1
    for i, freq in enumerate(result.frequencies_mhz):
        hours_meeting = sum(1 for row in result.rows if i < len(row.reliabilities) and row.reliabilities[i] >= threshold)
        if hours_meeting > best_hours:
            best_hours = hours_meeting
            best_freq = freq
    return {"best_frequency_mhz": best_freq, "hours_meeting_threshold": max(best_hours, 0)}


async def run_batch(circuits: list, *, submitted: dict, **shared_params) -> str:
    """Starts a batch job in the background and returns its job_id immediately."""
    job_id = uuid.uuid4().hex[:12]
    job = BatchJob(
        job_id=job_id,
        total=len(circuits),
        required_reliability_pct=shared_params["required_reliability_pct"],
        submitted=submitted,
    )
    _JOBS[job_id] = job

    async def worker():
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CIRCUITS)
        tasks = [_run_one(c, semaphore, **shared_params) for c in circuits]
        for coro in asyncio.as_completed(tasks):
            job.completed.append(await coro)
        job.done = True

    asyncio.create_task(worker())
    return job_id
