# api_clients.py — Phase 1
#
# Data sources:
#
# --- BTS T-100 Segment (All Carriers) — local CSV files ---
# Downloaded from https://transtats.bts.gov/Fields.asp?gnoyr_VQ=FMG
# Files: cache/t100_{year}.csv  (2024 pending, 2025 + 2026 available)
# No API key required — static annual downloads.
#
# --- OpenSky Network REST API (https://opensky-network.org/api) ---
# Live states       : GET /states/all
# Arrivals          : GET /flights/arrival?airport={ICAO}&begin={ts}&end={ts}
# Departures        : GET /flights/departure?airport={ICAO}&begin={ts}&end={ts}
# Auth: OAuth2 (client_id + client_secret). Register at https://opensky-network.org
# Docs: https://openskynetwork.github.io/opensky-api/rest.html
#
# --- Airport Facility Data — OpenAIP ---
# GET https://api.core.openaip.net/api/airports?apiKey=YOUR_KEY
# Free API key — register at https://www.openaip.net
# Docs: https://docs.openaip.net/

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / "cache"
_CACHE_DIR.mkdir(exist_ok=True)

# Columns we actually need from the T-100 CSV (avoids loading ~50 columns)
_T100_COLS = [
    "ORIGIN", "DEST", "YEAR", "MONTH",
    "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS",
    "DISTANCE", "AIR_TIME",
    "UNIQUE_CARRIER_NAME", "CLASS", "REGION",
]

# In-memory cache: year -> full DataFrame (loaded once per process)
_T100_FRAMES: dict[int, pd.DataFrame] = {}


# ---------------------------------------------------------------------------
# JSON cache helpers  (used by OpenSky / OpenAIP live calls)
# ---------------------------------------------------------------------------

def _cache_key_to_path(key: str) -> Path:
    safe = key.replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_")
    return _CACHE_DIR / f"{safe}.json"


def _cache_get(key: str, ttl_hours: float = 24) -> dict | list | None:
    path = _cache_key_to_path(key)
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > ttl_hours:
        path.unlink(missing_ok=True)
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Cache read failed for %s: %s", key, exc)
        return None


def _cache_set(key: str, data: dict | list) -> None:
    path = _cache_key_to_path(key)
    try:
        path.write_text(json.dumps(data))
    except Exception as exc:
        logger.warning("Cache write failed for %s: %s", key, exc)


# ---------------------------------------------------------------------------
# T-100 CSV loader
# ---------------------------------------------------------------------------

def _load_t100_year(year: int) -> pd.DataFrame | None:
    """Load and memory-cache the T-100 CSV for a given year.

    Reads only the columns in _T100_COLS to keep memory manageable.
    Returns None (with a warning) if the file is not present.
    """
    if year in _T100_FRAMES:
        return _T100_FRAMES[year]

    path = _CACHE_DIR / f"t100_{year}.csv"
    if not path.exists():
        logger.warning("T-100 file not found for year %d — expected %s", year, path)
        return None

    logger.info("Loading T-100 CSV for %d …", year)
    df = pd.read_csv(path, usecols=_T100_COLS, low_memory=False)

    df["ORIGIN"] = df["ORIGIN"].str.strip().str.upper()
    df["DEST"] = df["DEST"].str.strip().str.upper()
    for col in ("DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "DISTANCE", "AIR_TIME", "MONTH", "YEAR"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    _T100_FRAMES[year] = df
    logger.info("Loaded %d rows for year %d", len(df), year)
    return df


def available_t100_years() -> list[int]:
    """Return sorted list of years that have a local t100_{year}.csv file."""
    return sorted(
        int(p.stem.split("_")[1])
        for p in _CACHE_DIR.glob("t100_*.csv")
    )


# ---------------------------------------------------------------------------
# BTS T-100 client
# ---------------------------------------------------------------------------

def fetch_bts_t100(airport_code: str, year: int) -> pd.DataFrame | None:
    """Return T-100 segment rows for departures from airport_code in year.

    Columns returned:
        ORIGIN, DEST, YEAR, MONTH, ops, SEATS, PASSENGERS,
        distance_miles, AIR_TIME, carrier_name, CLASS, REGION

    CLASS values: F = scheduled service, G = non-scheduled passenger,
                  L = charter all-cargo, P = non-scheduled passenger only.
    REGION: D = domestic segment, I = international segment.

    Returns None if the CSV for that year is missing.
    """
    code = airport_code.upper()
    df = _load_t100_year(year)
    if df is None:
        return None

    result = df[df["ORIGIN"] == code].copy()
    result = result.rename(columns={
        "DEPARTURES_PERFORMED": "ops",
        "UNIQUE_CARRIER_NAME": "carrier_name",
        "DISTANCE": "distance_miles",
    })

    if result.empty:
        logger.warning("fetch_bts_t100: no rows for %s in %d", code, year)

    return result.reset_index(drop=True)


def fetch_bts_ontime(airport_code: str, year: int) -> dict | None:
    """Return delay and cancellation summary for airport_code in the given year.

    Reads from cache/ontime.csv (BTS on-time performance, monthly by carrier).
    Aggregates across all carriers and months for the requested airport + year.

    Returns:
        {
            "avg_delay_minutes": float,   # arr_delay_total / arr_flights_total
            "cancellation_rate": float,   # arr_cancelled_total / arr_flights_total
            "total_flights": int,
            "months_covered": int,
        }
        or None if the airport/year combination has no rows.
    """
    csv_path = _CACHE_DIR / "ontime.csv"
    if not csv_path.exists():
        logger.warning("fetch_bts_ontime: cache/ontime.csv not found")
        return None

    try:
        df = pd.read_csv(csv_path, dtype={"airport": str})
        df.columns = df.columns.str.strip()
        subset = df[(df["airport"].str.upper() == airport_code.upper()) & (df["year"] == year)]
        if subset.empty:
            logger.info("fetch_bts_ontime: no rows for %s year=%d", airport_code, year)
            return None

        total_flights = subset["arr_flights"].sum()
        if total_flights == 0:
            return None

        return {
            "avg_delay_minutes": subset["arr_delay"].sum() / total_flights,
            "cancellation_rate": subset["arr_cancelled"].sum() / total_flights,
            "total_flights": int(total_flights),
            "months_covered": subset["month"].nunique(),
        }
    except Exception as exc:
        logger.error("fetch_bts_ontime error: %s", exc)
        return None


def fetch_bts_passenger_trend(
    airport_code: str, start_year: int, end_year: int
) -> dict | None:
    """Return {year: total_passengers} for departures from airport_code.

    Aggregates across all available t100_{year}.csv files that fall within
    [start_year, end_year]. Years with no CSV are silently skipped.

    Returns None only if no data was found for any year in the range.
    """
    code = airport_code.upper()
    result: dict[int, int] = {}

    for year in range(start_year, end_year + 1):
        df = _load_t100_year(year)
        if df is None:
            continue
        subset = df[df["ORIGIN"] == code]["PASSENGERS"]
        if not subset.empty:
            result[year] = int(subset.sum())

    if not result:
        logger.warning("fetch_bts_passenger_trend: no data for %s %d-%d", code, start_year, end_year)
        return None

    return dict(sorted(result.items()))


# ---------------------------------------------------------------------------
# OpenSky Network client
# ---------------------------------------------------------------------------

_OPENSKY_BASE = "https://opensky-network.org/api"
_OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)
_OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
_OPENSKY_SECRET_KEY = os.getenv("OPENSKY_SECRET_KEY")

# In-memory token cache — refreshed automatically before expiry
_opensky_token: dict = {}

# Common non-K-prefix ICAO codes for US airports
_IATA_TO_ICAO_OVERRIDES: dict[str, str] = {
    "ANC": "PANC", "FAI": "PAFA", "JNU": "PAJN", "KTN": "PAKT",
    "SIT": "PASI", "YAK": "PAYA", "BET": "PABE", "ADQ": "PADQ",
    "HNL": "PHNL", "OGG": "PHOG", "KOA": "PHKO", "ITO": "PHTO",
    "LIH": "PHLI", "GUM": "PGUM", "SJU": "TJSJ", "STT": "TIST",
}


def _iata_to_icao(iata: str) -> str:
    """Convert IATA airport code to ICAO. US airports default to K+IATA."""
    code = iata.upper()
    return _IATA_TO_ICAO_OVERRIDES.get(code, f"K{code}")


def _get_opensky_token() -> str | None:
    """Return a valid Bearer token, fetching a new one via client_credentials if needed."""
    if _opensky_token and time.time() < _opensky_token.get("expires_at", 0) - 30:
        return _opensky_token["access_token"]

    if not _OPENSKY_CLIENT_ID or not _OPENSKY_SECRET_KEY:
        logger.warning("OpenSky credentials missing — set OPENSKY_CLIENT_ID and OPENSKY_SECRET_KEY")
        return None

    try:
        resp = requests.post(
            _OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": _OPENSKY_CLIENT_ID,
                "client_secret": _OPENSKY_SECRET_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _opensky_token["access_token"] = data["access_token"]
        _opensky_token["expires_at"] = time.time() + data.get("expires_in", 3600)
        logger.info("OpenSky token obtained (expires in %ds)", data.get("expires_in", 3600))
        return _opensky_token["access_token"]
    except Exception as exc:
        logger.error("OpenSky token fetch failed: %s", exc)
        return None


def _opensky_get(path: str, params: dict, headers: dict, timeout: int = 15) -> list | dict | None:
    """Single OpenSky GET request. Returns parsed JSON or None on any error."""
    try:
        resp = requests.get(
            f"{_OPENSKY_BASE}{path}",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code == 429:
            retry = resp.headers.get("X-Rate-Limit-Retry-After-Seconds", "unknown")
            logger.warning("OpenSky rate limit — retry after %s seconds", retry)
            return None
        if resp.status_code == 404:
            # OpenSky returns 404 when no flights exist in the queried window
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("OpenSky request failed (%s): %s", path, exc)
        return None


def fetch_live_flights(airport_iata: str, hours_back: float = 4.0) -> dict | None:
    """Fetch recent arrival and departure counts from OpenSky Network.

    Converts IATA → ICAO automatically (K-prefix for CONUS; overrides for AK/HI).

    Args:
        airport_iata: IATA code (e.g. "BOS") — converted to ICAO internally.
        hours_back: size of the lookback window (default 4 h). Max 7 days.

    Returns:
        {
          "airport_iata": str,
          "airport_icao": str,
          "arrivals":   int | None,   # None if that endpoint failed
          "departures": int | None,
          "window_hours": float,
        }
        or None if both endpoints failed.
    """
    iata = airport_iata.upper()
    icao = _iata_to_icao(iata)
    cache_key = f"opensky_{icao}_{hours_back}"
    cached = _cache_get(cache_key, ttl_hours=0.5)  # 30-min cache for live data
    if cached is not None:
        return cached

    token = _get_opensky_token()
    if token is None:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    end_ts = int(time.time())
    begin_ts = int(end_ts - hours_back * 3600)
    params = {"airport": icao, "begin": begin_ts, "end": end_ts}

    arr_data = _opensky_get("/flights/arrival", params, headers)
    dep_data = _opensky_get("/flights/departure", params, headers)

    if arr_data is None and dep_data is None:
        return None

    result = {
        "airport_iata": iata,
        "airport_icao": icao,
        "arrivals": len(arr_data) if isinstance(arr_data, list) else None,
        "departures": len(dep_data) if isinstance(dep_data, list) else None,
        "window_hours": hours_back,
    }
    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Airport facility client — OpenAIP
# ---------------------------------------------------------------------------

_OPENAIP_BASE = "https://api.core.openaip.net/api/airports"
_OPENAIP_KEY = os.getenv("OPENAIP_KEY")

# Ops/runway/hour assumption used in rated_capacity_proxy.
# Conservative IFR mixed-ops figure; real FAA capacity studies are airport-specific.
_OPS_PER_RUNWAY_PER_HOUR = 30
_OPERATING_HOURS_PER_DAY = 16


def fetch_airport_info(airport_iata: str) -> dict | None:
    """Fetch airport facility data from OpenAIP and return a normalised dict.

    Capacity note — rated_capacity_proxy is an estimate, NOT an FAA-certified value:
        rated_capacity_proxy (annual ops) = physical_runways
                                            × 30 ops/hr/runway  (conservative IFR)
                                            × 16 operating hours/day
                                            × 365 days/year
    Gate count is not available in any single authoritative public field;
    it is omitted here. Callers should treat this as a rough upper-bound proxy.

    Returns a dict with keys:
        name, iata, icao, country, lat, lon, elevation_ft,
        runway_count,          # physical runways (directional ends ÷ 2)
        max_runway_length_m,   # longest runway in metres
        has_long_runway,       # True if any runway ≥ 2 500 m (wide-body capable)
        rated_capacity_proxy,  # annual ops estimate (see formula above)
        capacity_proxy_note    # disclaimer string
    or None on failure.
    """
    iata = airport_iata.upper()
    icao = _iata_to_icao(iata)
    cache_key = f"openaip_{icao}"
    cached = _cache_get(cache_key, ttl_hours=168)  # 1 week — facility data is stable
    if cached is not None:
        return cached

    if not _OPENAIP_KEY:
        logger.warning("OPENAIP_KEY not set — cannot fetch airport facility data")
        return None

    try:
        resp = requests.get(
            _OPENAIP_BASE,
            params={"apiKey": _OPENAIP_KEY, "search": icao},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:
        logger.error("OpenAIP request failed for %s: %s", icao, exc)
        return None

    # search can return partial matches — pick the one whose icaoCode matches exactly
    airport = next((a for a in items if a.get("icaoCode") == icao), None)
    if airport is None:
        logger.warning("OpenAIP: no exact ICAO match for %s (got %d results)", icao, len(items))
        return None

    runways = airport.get("runways", [])
    # Each physical runway has two directional ends (e.g. 04L + 22R).
    physical_runways = max(len(runways) // 2, 1) if runways else 1

    lengths = [
        rwy.get("dimension", {}).get("length", {}).get("value", 0) or 0
        for rwy in runways
    ]
    max_len_m = max(lengths) if lengths else 0

    rated_capacity = (
        physical_runways
        * _OPS_PER_RUNWAY_PER_HOUR
        * _OPERATING_HOURS_PER_DAY
        * 365
    )

    coords = airport.get("geometry", {}).get("coordinates", [None, None])
    elev = airport.get("elevation", {}).get("value")
    # OpenAIP elevation unit 0 = feet
    elev_ft = round(elev) if elev is not None else None

    result = {
        "name": airport.get("name", ""),
        "iata": iata,
        "icao": icao,
        "country": airport.get("country", ""),
        "lat": coords[1],
        "lon": coords[0],
        "elevation_ft": elev_ft,
        "runway_count": physical_runways,
        "max_runway_length_m": max_len_m,
        "has_long_runway": max_len_m >= 2500,
        "rated_capacity_proxy": rated_capacity,
        "capacity_proxy_note": (
            f"Estimate: {physical_runways} runway(s) × {_OPS_PER_RUNWAY_PER_HOUR} ops/hr "
            f"× {_OPERATING_HOURS_PER_DAY} hrs/day × 365. Not an FAA-certified figure."
        ),
    }
    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Route statistics (aggregate — avoids row-cap issue in fetch_bts_t100)
# ---------------------------------------------------------------------------

# Standard haul thresholds (miles) used by fetch_route_stats.
# These align with common industry definitions:
#   short   < 500 mi  (~<1 h domestic hops)
#   medium  500–1999 mi  (transcontinental edge cases included)
#   long    2000–3499 mi  (cross-country / shorter transoceanic)
#   ultra   ≥ 3500 mi  (transpacific / transatlantic)
_HAUL_THRESHOLDS = [
    ("short_haul_under_500mi",  0,    500),
    ("medium_haul_500_1999mi",  500,  2000),
    ("long_haul_2000_3499mi",   2000, 3500),
    ("ultra_long_3500mi_plus",  3500, None),
]


def fetch_route_stats(airport_code: str, year: int) -> dict | None:
    """Return pre-aggregated route statistics for departures from airport_code in year.

    Unlike fetch_bts_t100, this never returns raw rows, so it works correctly
    even when the airport has thousands of route-month records.

    Returns a dict with:
        airport, year,
        total_ops, total_passengers, total_seats, load_factor,
        unique_destinations,
        distance_buckets: {bucket_name: {ops, pct}} for four standard haul bands,
        region_split: {domestic: {ops, pct}, international: {ops, pct}},
        top_routes_by_ops: list of {dest, ops, distance_miles} (top 10)

    Returns None if the airport has no data for the given year.
    """
    df = fetch_bts_t100(airport_code, year)
    if df is None or df.empty:
        return None

    total_ops = float(df["ops"].sum())
    if total_ops == 0:
        return None

    def _bucket(min_mi: float, max_mi: float | None) -> dict:
        mask = df["distance_miles"] >= min_mi
        if max_mi is not None:
            mask &= df["distance_miles"] < max_mi
        ops = float(df.loc[mask, "ops"].sum())
        return {"ops": int(ops), "pct": round(100 * ops / total_ops, 1)}

    distance_buckets = {name: _bucket(lo, hi) for name, lo, hi in _HAUL_THRESHOLDS}

    top = (
        df.groupby("DEST", as_index=False)
        .agg(ops=("ops", "sum"), distance_miles=("distance_miles", "median"))
        .sort_values("ops", ascending=False)
        .head(10)
    )
    top["distance_miles"] = top["distance_miles"].round().astype(int)

    domestic = float(df[df["REGION"] == "D"]["ops"].sum())
    intl     = float(df[df["REGION"] == "I"]["ops"].sum())

    total_seats = float(df["SEATS"].sum())
    total_pax   = float(df["PASSENGERS"].sum())

    return {
        "airport": airport_code.upper(),
        "year": year,
        "total_ops": int(total_ops),
        "total_passengers": int(total_pax),
        "total_seats": int(total_seats),
        "load_factor": round(total_pax / total_seats, 4) if total_seats > 0 else None,
        "unique_destinations": int(df["DEST"].nunique()),
        "distance_buckets": distance_buckets,
        "region_split": {
            "domestic":      {"ops": int(domestic), "pct": round(100 * domestic / total_ops, 1)},
            "international": {"ops": int(intl),     "pct": round(100 * intl / total_ops, 1)},
        },
        "top_routes_by_ops": top.rename(columns={"DEST": "dest"}).to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print(f"Available T-100 years: {available_t100_years()}")

    print("\n--- fetch_bts_t100 BOS 2025 ---")
    df = fetch_bts_t100("BOS", 2025)
    if df is not None:
        print(f"Rows: {len(df)}  Cols: {list(df.columns)}")
        print(df[["ORIGIN", "DEST", "MONTH", "ops", "PASSENGERS", "distance_miles", "carrier_name"]].head(5).to_string(index=False))
        print(f"Total ops: {df['ops'].sum():,.0f}  Total pax: {df['PASSENGERS'].sum():,.0f}")

    print("\n--- fetch_bts_passenger_trend BOS 2025-2026 ---")
    trend = fetch_bts_passenger_trend("BOS", 2025, 2026)
    print(trend)

    print("\n--- fetch_bts_t100 ANC 2025 (long-haul check) ---")
    anc = fetch_bts_t100("ANC", 2025)
    if anc is not None:
        longhaul = anc[anc["distance_miles"] >= 2000]
        total_ops = anc["ops"].sum()
        lh_ops = longhaul["ops"].sum()
        pct = 100 * lh_ops / total_ops if total_ops else 0
        print(f"Total ops: {total_ops:,.0f}  Long-haul ops (>=2000mi): {lh_ops:,.0f}  ({pct:.1f}%)")

    print("\n--- fetch_live_flights BOS (OpenSky) ---")
    live = fetch_live_flights("BOS")
    print(live)

    print("\n--- fetch_airport_info (OpenAIP) ---")
    for code in ["BOS", "SNA", "ANC", "SFO"]:
        info = fetch_airport_info(code)
        if info:
            print(f"  {code}: runways={info['runway_count']}  "
                  f"max_len={info['max_runway_length_m']}m  "
                  f"long_runway={info['has_long_runway']}  "
                  f"capacity_proxy={info['rated_capacity_proxy']:,}")
        else:
            print(f"  {code}: FAILED")
