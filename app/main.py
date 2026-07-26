import json

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from antennas import ANTENNA_CATALOG, DEFAULT_ANTENNA
from deck import Antenna, CircuitRequest
from engine import EngineError, run_prediction
from geo import initial_bearing_deg
from geo_cities import CITIES

app = FastAPI(title="VOACAP HF Propagation Prediction")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

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
    rows/reliabilities parser.py already produces."""
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
        "required_reliability_pct": 45.0,
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
    required_reliability_pct: float = Form(45.0),
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
