import asyncio
import json

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from antennas import ANTENNA_CATALOG, DEFAULT_ANTENNA
from area_deck import AreaRequest
from area_engine import AreaEngineError, run_area_prediction
from batch import BatchCircuit, get_job, run_batch, summarize
from deck import Antenna, CircuitRequest
from engine import EngineError, run_prediction
from geo import initial_bearing_deg
from geo_cities import CITIES

app = FastAPI(title="VOACAP HF Propagation Prediction")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# v1 caps - no real timing data yet (see the plan's Testing/Verification
# section), kept conservative on purpose: each grid point is a full voacapl
# area-mode point, and each frequency is a whole separate voacapl invocation.
MAX_AREA_GRID_POINTS = 40
MAX_AREA_FREQUENCIES = 4

# Loaded once at startup (see geo_cities.py) - passed to the P2P form as
# JSON for the TX/RX "pick a city" convenience datalist. Not filtered by
# the |lon| >= 100 limitation below (unlike the old circuit-preset list it
# replaces): with ~4000+ cities worldwide, trimming everything past that
# limit would silently hide entire continents rather than a handful of
# entries - the existing validation error already covers picks that don't
# fit, same as it does for manual entry.
CITIES_JSON = json.dumps(CITIES)


def _chart_data(result) -> str:
    """REL-vs-hour (+MUF, +FOT) chart data for result.html - reuses the
    rows/reliabilities parser.py already produces. Shared by /predict and
    the batch per-circuit drill-down, since both render result.html."""
    return json.dumps(
        {
            "hours": [row.gmt for row in result.rows],
            "muf": [row.muf for row in result.rows],
            "fot": [row.fot for row in result.rows],
            "frequencies": result.frequencies_mhz,
            "series": [
                [row.reliabilities[i] if i < len(row.reliabilities) else None for row in result.rows]
                for i in range(len(result.frequencies_mhz))
            ],
        }
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _form_defaults() -> dict:
    return {
        "label_tx": "",
        "label_rx": "",
        "tx_lat": "",
        "tx_lon": "",
        "rx_lat": "",
        "rx_lon": "",
        "month": 1,
        "year": 2026,
        "sunspot_number": 100,
        "tx_antenna": DEFAULT_ANTENNA,
        "rx_antenna": DEFAULT_ANTENNA,
        "tx_power_kw": 1.0,
        # Classic VOACAP 9-frequency test set - each sits inside a standard
        # shortwave broadcast band (49m/41m/31m/25m/22m/19m/16m/13m/11m).
        "frequencies_mhz": "6.07 7.20 9.70 11.85 13.70 15.35 17.73 21.65 25.89",
        "noise_dbw": 145.0,
        "min_takeoff_angle_deg": 3.0,
        "required_reliability_pct": 90.0,
        "required_snr_db": 27.0,
        "multipath_power_tolerance_db": 3.0,
        "multipath_delay_tolerance_ms": 0.10,
    }


@app.get("/", response_class=HTMLResponse)
def form(request: Request):
    # Query params let a result page's "Edit inputs" link bring the user
    # back here with their last submission prefilled, instead of blank
    # defaults - see the hidden GET form in result.html.
    values = _form_defaults()
    values.update({k: v for k, v in request.query_params.items() if k in values})
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "antennas": ANTENNA_CATALOG,
            "cities_json": CITIES_JSON,
            **values,
            "error": None,
        },
    )


@app.post("/predict", response_class=HTMLResponse)
async def predict(
    request: Request,
    label_tx: str = Form(...),
    label_rx: str = Form(...),
    tx_lat: float = Form(...),
    tx_lon: float = Form(...),
    rx_lat: float = Form(...),
    rx_lon: float = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    sunspot_number: float = Form(...),
    tx_antenna: str = Form(...),
    rx_antenna: str = Form(...),
    tx_power_kw: float = Form(...),
    frequencies_mhz: str = Form(...),
    noise_dbw: float = Form(145.0),
    min_takeoff_angle_deg: float = Form(3.0),
    required_reliability_pct: float = Form(90.0),
    required_snr_db: float = Form(27.0),
    multipath_power_tolerance_db: float = Form(3.0),
    multipath_delay_tolerance_ms: float = Form(0.10),
):
    submitted = {
        "label_tx": label_tx,
        "label_rx": label_rx,
        "tx_lat": tx_lat,
        "tx_lon": tx_lon,
        "rx_lat": rx_lat,
        "rx_lon": rx_lon,
        "month": month,
        "year": year,
        "sunspot_number": sunspot_number,
        "tx_antenna": tx_antenna,
        "rx_antenna": rx_antenna,
        "tx_power_kw": tx_power_kw,
        "frequencies_mhz": frequencies_mhz,
        "noise_dbw": noise_dbw,
        "min_takeoff_angle_deg": min_takeoff_angle_deg,
        "required_reliability_pct": required_reliability_pct,
        "required_snr_db": required_snr_db,
        "multipath_power_tolerance_db": multipath_power_tolerance_db,
        "multipath_delay_tolerance_ms": multipath_delay_tolerance_ms,
    }

    def error_page(message: str):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "antennas": ANTENNA_CATALOG,
                "cities_json": CITIES_JSON,
                **submitted,
                "error": message,
            },
        )

    if not (-90 <= tx_lat <= 90) or not (-90 <= rx_lat <= 90):
        return error_page("Latitude must be between -90 and 90.")
    if abs(tx_lon) >= 100 or abs(rx_lon) >= 100:
        return error_page(
            "Longitude magnitude must be under 100 (a known limitation of the "
            "fixed-column input format used here - see deck.py)."
        )
    if not (1 <= month <= 12):
        return error_page("Month must be between 1 and 12.")

    try:
        freqs = [float(f) for f in frequencies_mhz.replace(",", " ").split()]
    except ValueError:
        return error_page("Frequencies must be a list of numbers (MHz), space or comma separated.")
    if not freqs or len(freqs) > 9:
        return error_page("Provide between 1 and 9 frequencies (MHz).")

    tx_bearing = initial_bearing_deg(tx_lat, tx_lon, rx_lat, rx_lon)
    rx_bearing = initial_bearing_deg(rx_lat, rx_lon, tx_lat, tx_lon)

    req = CircuitRequest(
        label_tx=label_tx,
        label_rx=label_rx,
        tx_lat=tx_lat,
        tx_lon=tx_lon,
        rx_lat=rx_lat,
        rx_lon=rx_lon,
        month=month,
        year=year,
        sunspot_number=sunspot_number,
        tx_antenna=Antenna(sample_file=tx_antenna, bearing_deg=tx_bearing, power_kw=tx_power_kw),
        rx_antenna=Antenna(sample_file=rx_antenna, bearing_deg=rx_bearing, power_kw=0.0),
        frequencies_mhz=freqs,
        noise_dbw=noise_dbw,
        min_takeoff_angle_deg=min_takeoff_angle_deg,
        required_reliability_pct=required_reliability_pct,
        required_snr_db=required_snr_db,
        multipath_power_tolerance_db=multipath_power_tolerance_db,
        multipath_delay_tolerance_ms=multipath_delay_tolerance_ms,
    )

    try:
        result = await run_prediction(req)
    except EngineError as e:
        return error_page(f"Prediction engine error: {e}")

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "label_tx": label_tx,
            "label_rx": label_rx,
            "tx_bearing": round(tx_bearing, 1),
            "rx_bearing": round(rx_bearing, 1),
            "result": result,
            "chart_json": _chart_data(result),
            "submitted": submitted,
        },
    )


def _area_form_defaults() -> dict:
    return {
        "label": "",
        "center_lat": "",
        "center_lon": "",
        "radius_km": 3000.0,
        "step_km": 300.0,
        "hour_gmt": 12,
        "month": 1,
        "year": 2026,
        "sunspot_number": 100,
        "tx_antenna": DEFAULT_ANTENNA,
        "tx_bearing_deg": 0.0,
        "tx_power_kw": 1.0,
        "rx_antenna": DEFAULT_ANTENNA,
        "frequencies_mhz": "",
        "noise_dbw": 145.0,
        "min_takeoff_angle_deg": 3.0,
        "required_reliability_pct": 90.0,
        "required_snr_db": 27.0,
        "multipath_power_tolerance_db": 3.0,
        "multipath_delay_tolerance_ms": 0.10,
    }


@app.get("/area", response_class=HTMLResponse)
def area_form(request: Request):
    values = _area_form_defaults()
    values.update({k: v for k, v in request.query_params.items() if k in values})
    return templates.TemplateResponse(
        "area_form.html",
        {"request": request, "antennas": ANTENNA_CATALOG, **values, "error": None},
    )


@app.post("/predict-area", response_class=HTMLResponse)
async def predict_area(
    request: Request,
    label: str = Form(...),
    center_lat: float = Form(...),
    center_lon: float = Form(...),
    radius_km: float = Form(...),
    step_km: float = Form(...),
    hour_gmt: int = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    sunspot_number: float = Form(...),
    tx_antenna: str = Form(...),
    tx_bearing_deg: float = Form(0.0),
    tx_power_kw: float = Form(...),
    rx_antenna: str = Form(...),
    frequencies_mhz: str = Form(...),
    noise_dbw: float = Form(145.0),
    min_takeoff_angle_deg: float = Form(3.0),
    required_reliability_pct: float = Form(90.0),
    required_snr_db: float = Form(27.0),
    multipath_power_tolerance_db: float = Form(3.0),
    multipath_delay_tolerance_ms: float = Form(0.10),
):
    submitted = {
        "label": label,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "radius_km": radius_km,
        "step_km": step_km,
        "hour_gmt": hour_gmt,
        "month": month,
        "year": year,
        "sunspot_number": sunspot_number,
        "tx_antenna": tx_antenna,
        "tx_bearing_deg": tx_bearing_deg,
        "tx_power_kw": tx_power_kw,
        "rx_antenna": rx_antenna,
        "frequencies_mhz": frequencies_mhz,
        "noise_dbw": noise_dbw,
        "min_takeoff_angle_deg": min_takeoff_angle_deg,
        "required_reliability_pct": required_reliability_pct,
        "required_snr_db": required_snr_db,
        "multipath_power_tolerance_db": multipath_power_tolerance_db,
        "multipath_delay_tolerance_ms": multipath_delay_tolerance_ms,
    }

    def error_page(message: str):
        return templates.TemplateResponse(
            "area_form.html",
            {"request": request, "antennas": ANTENNA_CATALOG, **submitted, "error": message},
        )

    if not (-90 <= center_lat <= 90):
        return error_page("Center latitude must be between -90 and 90.")
    if abs(center_lon) >= 100:
        # AREA's own coordinate field handles the full range, but the deck
        # still needs a dummy CIRCUIT card (TX=RX=center) that doesn't -
        # see area_deck.py's module docstring.
        return error_page(
            "Center longitude magnitude must be under 100 for now (the deck's "
            "required dummy CIRCUIT card shares the P2P form's limitation - "
            "see area_deck.py)."
        )
    if not (1 <= month <= 12):
        return error_page("Month must be between 1 and 12.")
    if not (0 <= hour_gmt <= 23):
        return error_page("Hour (GMT) must be between 0 and 23.")
    if radius_km <= 0 or step_km <= 0:
        return error_page("Radius and step size must be positive.")

    try:
        freqs = [float(f) for f in frequencies_mhz.replace(",", " ").split()]
    except ValueError:
        return error_page("Frequencies must be a list of numbers (MHz), space or comma separated.")
    if not freqs or len(freqs) > MAX_AREA_FREQUENCIES:
        return error_page(f"Provide between 1 and {MAX_AREA_FREQUENCIES} frequencies (MHz).")

    grid_points = min(MAX_AREA_GRID_POINTS, max(3, round(2 * radius_km / step_km) + 1))

    def make_request(freq: float) -> AreaRequest:
        return AreaRequest(
            label=label,
            center_lat=center_lat,
            center_lon=center_lon,
            radius_km=radius_km,
            grid_points=grid_points,
            hour_gmt=hour_gmt,
            month=month,
            year=year,
            sunspot_number=sunspot_number,
            tx_antenna=Antenna(sample_file=tx_antenna, bearing_deg=tx_bearing_deg, power_kw=tx_power_kw),
            rx_antenna=Antenna(sample_file=rx_antenna, bearing_deg=0.0, power_kw=0.0),
            frequency_mhz=freq,
            noise_dbw=noise_dbw,
            min_takeoff_angle_deg=min_takeoff_angle_deg,
            required_reliability_pct=required_reliability_pct,
            required_snr_db=required_snr_db,
            multipath_power_tolerance_db=multipath_power_tolerance_db,
            multipath_delay_tolerance_ms=multipath_delay_tolerance_ms,
        )

    try:
        results = await asyncio.gather(*(run_area_prediction(make_request(f)) for f in freqs))
    except AreaEngineError as e:
        return error_page(f"Area prediction engine error: {e}")

    # One Leaflet layer per frequency: [[lat, lon, reliability], ...].
    grids_by_freq = {
        freq: [[p.lat, p.lon, p.reliability] for p in result.points]
        for freq, result in zip(freqs, results)
    }

    return templates.TemplateResponse(
        "area_result.html",
        {
            "request": request,
            "label": label,
            "center_lat": center_lat,
            "center_lon": center_lon,
            "frequencies_mhz": freqs,
            "grids_json": json.dumps(grids_by_freq),
            "submitted": submitted,
        },
    )


def _batch_form_defaults() -> dict:
    return {
        "circuits_csv": "",
        "month": 1,
        "year": 2026,
        "sunspot_number": 100,
        "tx_antenna": DEFAULT_ANTENNA,
        "rx_antenna": DEFAULT_ANTENNA,
        "tx_power_kw": 1.0,
        "frequencies_mhz": "",
        "noise_dbw": 145.0,
        "min_takeoff_angle_deg": 3.0,
        "required_reliability_pct": 90.0,
        "required_snr_db": 27.0,
        "multipath_power_tolerance_db": 3.0,
        "multipath_delay_tolerance_ms": 0.10,
    }


@app.get("/batch", response_class=HTMLResponse)
def batch_form(request: Request):
    values = _batch_form_defaults()
    values.update({k: v for k, v in request.query_params.items() if k in values})
    return templates.TemplateResponse(
        "batch_form.html",
        {"request": request, "antennas": ANTENNA_CATALOG, **values, "error": None},
    )


@app.post("/batch")
async def submit_batch(
    request: Request,
    circuits_csv: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    sunspot_number: float = Form(...),
    tx_antenna: str = Form(...),
    rx_antenna: str = Form(...),
    tx_power_kw: float = Form(...),
    frequencies_mhz: str = Form(...),
    noise_dbw: float = Form(145.0),
    min_takeoff_angle_deg: float = Form(3.0),
    required_reliability_pct: float = Form(90.0),
    required_snr_db: float = Form(27.0),
    multipath_power_tolerance_db: float = Form(3.0),
    multipath_delay_tolerance_ms: float = Form(0.10),
):
    submitted = {
        "circuits_csv": circuits_csv,
        "month": month,
        "year": year,
        "sunspot_number": sunspot_number,
        "tx_antenna": tx_antenna,
        "rx_antenna": rx_antenna,
        "tx_power_kw": tx_power_kw,
        "frequencies_mhz": frequencies_mhz,
        "noise_dbw": noise_dbw,
        "min_takeoff_angle_deg": min_takeoff_angle_deg,
        "required_reliability_pct": required_reliability_pct,
        "required_snr_db": required_snr_db,
        "multipath_power_tolerance_db": multipath_power_tolerance_db,
        "multipath_delay_tolerance_ms": multipath_delay_tolerance_ms,
    }

    def error_page(message: str):
        return templates.TemplateResponse(
            "batch_form.html",
            {"request": request, "antennas": ANTENNA_CATALOG, **submitted, "error": message},
        )

    circuits = []
    for line_no, line in enumerate(circuits_csv.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6:
            return error_page(
                f"Row {line_no}: expected 6 comma-separated values "
                f"(label_tx,label_rx,tx_lat,tx_lon,rx_lat,rx_lon), got {len(parts)}."
            )
        label_tx, label_rx, tx_lat, tx_lon, rx_lat, rx_lon = parts
        try:
            tx_lat, tx_lon, rx_lat, rx_lon = float(tx_lat), float(tx_lon), float(rx_lat), float(rx_lon)
        except ValueError:
            return error_page(f"Row {line_no}: latitude/longitude must be numbers.")
        if not (-90 <= tx_lat <= 90) or not (-90 <= rx_lat <= 90):
            return error_page(f"Row {line_no}: latitude must be between -90 and 90.")
        if abs(tx_lon) >= 100 or abs(rx_lon) >= 100:
            return error_page(
                f"Row {line_no}: longitude magnitude must be under 100 "
                "(same limitation as the point-to-point form - see deck.py)."
            )
        circuits.append(BatchCircuit(label_tx, label_rx, tx_lat, tx_lon, rx_lat, rx_lon))

    if not circuits:
        return error_page("Provide at least one circuit row.")
    if not (1 <= month <= 12):
        return error_page("Month must be between 1 and 12.")

    try:
        freqs = [float(f) for f in frequencies_mhz.replace(",", " ").split()]
    except ValueError:
        return error_page("Frequencies must be a list of numbers (MHz), space or comma separated.")
    if not freqs or len(freqs) > 9:
        return error_page("Provide between 1 and 9 frequencies (MHz).")

    job_id = await run_batch(
        circuits,
        submitted=submitted,
        month=month,
        year=year,
        sunspot_number=sunspot_number,
        tx_antenna=tx_antenna,
        rx_antenna=rx_antenna,
        tx_power_kw=tx_power_kw,
        frequencies_mhz=freqs,
        noise_dbw=noise_dbw,
        min_takeoff_angle_deg=min_takeoff_angle_deg,
        required_reliability_pct=required_reliability_pct,
        required_snr_db=required_snr_db,
        multipath_power_tolerance_db=multipath_power_tolerance_db,
        multipath_delay_tolerance_ms=multipath_delay_tolerance_ms,
    )
    return RedirectResponse(f"/batch/{job_id}", status_code=303)


@app.get("/batch/{job_id}", response_class=HTMLResponse)
def batch_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            "batch_result.html",
            {"request": request, "job": None, "job_id": job_id, "summaries": []},
            status_code=404,
        )
    summaries = []
    for i, cr in enumerate(job.completed):
        row = {
            "index": i,
            "label_tx": cr.circuit.label_tx,
            "label_rx": cr.circuit.label_rx,
            "error": cr.error,
        }
        if cr.result is not None:
            row.update(summarize(cr.result, required_reliability_pct=job.required_reliability_pct))
        summaries.append(row)

    return templates.TemplateResponse(
        "batch_result.html",
        {"request": request, "job": job, "job_id": job_id, "summaries": summaries, "submitted": job.submitted},
    )


@app.get("/batch/{job_id}/circuit/{index}", response_class=HTMLResponse)
def batch_circuit_detail(request: Request, job_id: str, index: int):
    job = get_job(job_id)
    if job is None or index < 0 or index >= len(job.completed):
        return HTMLResponse("Not found", status_code=404)
    cr = job.completed[index]
    if cr.result is None:
        return HTMLResponse(f"Circuit failed: {cr.error}", status_code=200)
    # "Edit inputs" on this page sends the user to the P2P form (the closest
    # single-circuit editor) prefilled with this circuit's TX/RX plus the
    # batch's shared settings - job.submitted also has a circuits_csv key
    # the P2P form doesn't recognize, harmless since form() only picks out
    # keys it knows about.
    submitted = {
        **job.submitted,
        "label_tx": cr.circuit.label_tx,
        "label_rx": cr.circuit.label_rx,
        "tx_lat": cr.circuit.tx_lat,
        "tx_lon": cr.circuit.tx_lon,
        "rx_lat": cr.circuit.rx_lat,
        "rx_lon": cr.circuit.rx_lon,
    }
    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "label_tx": cr.circuit.label_tx,
            "label_rx": cr.circuit.label_rx,
            "tx_bearing": round(cr.tx_bearing, 1),
            "rx_bearing": round(cr.rx_bearing, 1),
            "result": cr.result,
            "chart_json": _chart_data(cr.result),
            "submitted": submitted,
        },
    )
