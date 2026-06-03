# agent.py — Phase 3
from __future__ import annotations

import json
import logging
from dataclasses import asdict

import anthropic

from api_clients import (
    available_t100_years,
    fetch_airport_info,
    fetch_bts_ontime,
    fetch_bts_passenger_trend,
    fetch_bts_t100,
    fetch_live_flights,
    fetch_route_stats,
)
from scoring import AirportMetrics, score_airports

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 3.2  System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an aviation investment analyst assistant for a private investment firm \
evaluating US airport modernization and expansion opportunities. You have access to BTS T-100 \
segment data, BTS on-time performance data, OpenSky Network live flight data, and OpenAIP \
airport facility data. You also have a deterministic scoring engine that ranks airports by \
investment attractiveness.

## Strict data rules

- **Never invent numbers.** Every statistic, percentage, count, or ranking you state must come \
directly from a tool call in this conversation. If you have not called a tool for a number, do \
not say it.
- Call tools before drawing conclusions. Do not speculate about an airport's utilization, growth, \
or congestion without first fetching the relevant data.
- If a tool returns null or an error, say so explicitly. Do not fill the gap with estimates or \
general knowledge.

## Reasoning approach

1. Restate what the user is asking and what data you need.
2. Call the necessary tools (use `list_airports` then `compare_airports` for ranking questions).
3. Summarize the raw results briefly before interpreting them.
4. Explain which components drive each airport's score and why.
5. State your conclusion with explicit caveats.

## Proxies and uncertainty

Several inputs are proxy measurements, not direct observations. Flag them every time you cite them:

- **rated_capacity_proxy** (from `fetch_airport_info`): estimated from runway count and a \
conservative 30 ops/runway/hour assumption. Not an FAA-certified capacity figure. Always \
surface the `capacity_proxy_note` field when quoting this number.
- **unmet_demand_score**: derived from average delay, cancellation rate, load factor, and a \
capacity-capped flag. This is an indirect signal — it does not measure passengers who tried \
to book and could not. Label it "unmet demand proxy" in your responses, never "unmet demand."
- **cancellation_rate** (from on-time data): used as a congestion signal when delay data is \
limited. A high rate may also reflect weather or carrier issues unrelated to airport capacity.
- **confidence field**: when a scored airport shows confidence = "medium" or "low", tell the \
user which data was missing and what it means for the reliability of the ranking.

## Scoring methodology (explain this when asked)

The scorer ranks airports on four normalized components, each weighted equally by default \
(weights can be overridden via `compare_airports`):

| Component | Weight | What it measures |
|---|---|---|
| Utilization | 25 % | ops ÷ rated_capacity_proxy — how close the airport is to its ceiling |
| Passenger growth | 25 % | 3-year CAGR of total passengers — demand trajectory |
| Unmet demand proxy | 25 % | Average of: delay signal, cancellation signal, load factor, capacity-capped flag |
| CAPEX penalty | 25 % | Deducted if a major infrastructure project was completed in the last 5 years |

All components except the CAPEX penalty are min-max normalized across the comparison set \
before weighting, so scores reflect relative standing within the group, not absolute quality. \
A score of 1.0 means best in the group on the weighted formula; 0.0 means worst.

## Scope

You can answer questions about:
- US airport investment attractiveness and capacity
- Passenger trends, congestion signals, and delay patterns
- Route-level data from BTS T-100 (carriers, destinations, seat/passenger counts)
- Real-time flight activity (via OpenSky — subject to data availability)
- Comparisons across any set of US airports

You cannot answer questions about:
- Non-US airports (data sources are US-only)
- Future airline schedules or planned routes
- Financial valuations, bond ratings, or airport authority debt
- Specific terminal, gate, or airfield engineering specifications
- Any number not derivable from the available tools

If a question falls outside scope, say so in one sentence and suggest what the tools can help \
with instead.
"""


# ---------------------------------------------------------------------------
# 3.1.3  Airport roster — IATA codes by US state / region
# ---------------------------------------------------------------------------

_AIRPORTS_BY_STATE: dict[str, list[str]] = {
    "massachusetts":  ["BOS", "ORH", "HYA", "ACK", "MVY"],
    "rhode island":   ["PVD"],
    "connecticut":    ["BDL", "HVN", "GON"],
    "new hampshire":  ["MHT", "PSM"],
    "vermont":        ["BTV", "MPV", "RUT"],
    "maine":          ["PWM", "BGR", "BHB", "RKD"],
    "new york":       ["JFK", "LGA", "EWR", "BUF", "SYR", "ALB", "ROC", "SWF"],
    "new jersey":     ["EWR", "ACY"],
    "pennsylvania":   ["PHL", "PIT", "ABE", "MDT", "AVP"],
    "maryland":       ["BWI"],
    "virginia":       ["IAD", "DCA", "ORF", "RIC", "ROA"],
    "florida":        ["MIA", "MCO", "TPA", "FLL", "JAX", "RSW", "SRQ", "PNS", "GNV"],
    "georgia":        ["ATL", "SAV", "AGS"],
    "north carolina": ["CLT", "RDU", "GSO", "ILM", "AVL"],
    "south carolina": ["CHS", "CAE", "GSP", "MYR"],
    "tennessee":      ["BNA", "MEM", "TYS", "CHA"],
    "illinois":       ["ORD", "MDW", "BMI", "MLI", "PIA"],
    "ohio":           ["CMH", "CLE", "CVG", "DAY", "CAK"],
    "michigan":       ["DTW", "GRR", "LAN", "AZO", "FNT"],
    "minnesota":      ["MSP", "DLH", "RST"],
    "wisconsin":      ["MKE", "MSN", "GRB", "CWA"],
    "indiana":        ["IND", "SBN", "FWA", "EVV"],
    "missouri":       ["STL", "MCI", "SGF"],
    "texas":          ["DFW", "IAH", "HOU", "AUS", "SAT", "ELP", "LBB", "MAF"],
    "louisiana":      ["MSY", "BTR", "SHV"],
    "oklahoma":       ["OKC", "TUL"],
    "arkansas":       ["LIT", "XNA"],
    "mississippi":    ["JAN", "GPT"],
    "alabama":        ["BHM", "HSV", "MOB"],
    "colorado":       ["DEN", "COS", "GJT", "ASE", "EGE"],
    "utah":           ["SLC", "PVU"],
    "arizona":        ["PHX", "TUS", "FLG"],
    "nevada":         ["LAS", "RNO"],
    "new mexico":     ["ABQ", "SAF"],
    "montana":        ["BIL", "MSO", "BZN", "GTF", "HLN"],
    "idaho":          ["BOI", "TWF", "IDA", "PIH"],
    "wyoming":        ["CPR", "JAC", "CYS"],
    "california":     ["LAX", "SFO", "SAN", "SJC", "OAK", "BUR", "LGB", "ONT", "SMF", "SNA", "FAT"],
    "oregon":         ["PDX", "EUG", "MFR", "RDM"],
    "washington":     ["SEA", "GEG", "BLI"],
    "alaska":         ["ANC", "FAI", "JNU", "KTN", "SIT", "BET", "ADQ", "OME", "BRW"],
    "hawaii":         ["HNL", "OGG", "KOA", "ITO", "LIH"],
    "puerto rico":    ["SJU", "BQN", "PSE"],
    "guam":           ["GUM"],
}

_STATE_ALIASES: dict[str, str] = {
    "ma": "massachusetts", "ri": "rhode island", "ct": "connecticut",
    "nh": "new hampshire", "vt": "vermont",      "me": "maine",
    "ny": "new york",      "nj": "new jersey",   "pa": "pennsylvania",
    "md": "maryland",      "va": "virginia",
    "fl": "florida",       "ga": "georgia",      "nc": "north carolina",
    "sc": "south carolina","tn": "tennessee",
    "il": "illinois",      "oh": "ohio",          "mi": "michigan",
    "mn": "minnesota",     "wi": "wisconsin",     "in": "indiana",
    "mo": "missouri",
    "tx": "texas",         "la": "louisiana",     "ok": "oklahoma",
    "ar": "arkansas",      "ms": "mississippi",   "al": "alabama",
    "co": "colorado",      "ut": "utah",          "az": "arizona",
    "nv": "nevada",        "nm": "new mexico",    "mt": "montana",
    "id": "idaho",         "wy": "wyoming",
    "ca": "california",    "or": "oregon",        "wa": "washington",
    "ak": "alaska",        "hi": "hawaii",        "pr": "puerto rico",
}

_NEW_ENGLAND_STATES = [
    "massachusetts", "rhode island", "connecticut",
    "new hampshire", "vermont", "maine",
]


# ---------------------------------------------------------------------------
# 3.1.3  list_airports — implementation
# ---------------------------------------------------------------------------

def list_airports(state: str) -> dict:
    """Return IATA codes for commercial airports in a US state or region.

    Accepts full state names, 2-letter abbreviations, or "new england".
    """
    key = state.strip().lower()
    key = _STATE_ALIASES.get(key, key)

    if key == "new england":
        codes: list[str] = []
        for s in _NEW_ENGLAND_STATES:
            codes.extend(_AIRPORTS_BY_STATE.get(s, []))
        return {"region": "New England", "airports": sorted(set(codes))}

    codes = _AIRPORTS_BY_STATE.get(key)
    if codes is None:
        return {
            "error": f"Unknown state or region: {state!r}. "
                     "Use a full US state name, 2-letter abbreviation, or 'new england'."
        }
    return {"state": state.title(), "airports": codes}


# ---------------------------------------------------------------------------
# 3.1.4  compare_airports — implementation
# ---------------------------------------------------------------------------

def compare_airports(
    codes: list[str],
    w_utilization: float | None = None,
    w_growth: float | None = None,
    w_unmet_demand: float | None = None,
    w_capex_penalty: float | None = None,
) -> dict:
    """Fetch live data for each airport, assemble AirportMetrics, and return scored ranking.

    Uses the most recent available T-100 year for ops/load_factor and the previous
    3 years for the passenger trend. On-time data comes from cache/ontime.csv.

    Optional weight overrides temporarily replace the module-level constants for
    this call only. All four must be provided together if any are overridden;
    otherwise the call returns an error.
    """
    import scoring as _scoring

    # Apply optional weight overrides
    weight_override = any(w is not None for w in [w_utilization, w_growth, w_unmet_demand, w_capex_penalty])
    if weight_override:
        if not all(w is not None for w in [w_utilization, w_growth, w_unmet_demand, w_capex_penalty]):
            return {"error": "Provide all four weights together or none at all."}
        total = w_utilization + w_growth + w_unmet_demand + w_capex_penalty
        if abs(total - 1.0) > 0.01:
            return {"error": f"Weights must sum to 1.0 (got {total:.3f})."}
        orig = (_scoring.W_UTILIZATION, _scoring.W_GROWTH, _scoring.W_UNMET_DEMAND, _scoring.W_CAPEX_PENALTY)
        _scoring.W_UTILIZATION  = w_utilization
        _scoring.W_GROWTH       = w_growth
        _scoring.W_UNMET_DEMAND = w_unmet_demand
        _scoring.W_CAPEX_PENALTY = w_capex_penalty

    try:
        years = available_t100_years()
        if not years:
            return {"error": "No T-100 data available in cache/."}

        latest_year = max(years)
        trend_start = max(min(years), latest_year - 3)

        metrics_list: list[AirportMetrics] = []
        fetch_log: dict[str, dict] = {}

        for raw_code in codes:
            code = raw_code.strip().upper()

            # T-100: ops + load factor
            t100 = fetch_bts_t100(code, latest_year)
            if t100 is not None and not t100.empty:
                ops         = float(t100["ops"].sum())
                total_seats = t100["SEATS"].sum()
                total_pax   = t100["PASSENGERS"].sum()
                load_factor = float(total_pax / total_seats) if total_seats > 0 else None
            else:
                ops = load_factor = None

            # Airport facility: rated capacity
            info = fetch_airport_info(code)
            rated_capacity = float(info["rated_capacity_proxy"]) if info else None

            # Passenger trend
            trend = fetch_bts_passenger_trend(code, trend_start, latest_year)
            yearly_totals = [int(v) for v in trend.values()] if trend else []

            # On-time / delay
            ontime = fetch_bts_ontime(code, latest_year)
            avg_delay        = float(ontime["avg_delay_minutes"])  if ontime else None
            cancellation_rate = float(ontime["cancellation_rate"]) if ontime else None

            capacity_capped = (
                ops is not None and rated_capacity is not None and ops >= rated_capacity
            )

            fetch_log[code] = {
                "ops": ops,
                "rated_capacity": rated_capacity,
                "load_factor": round(load_factor, 4) if load_factor else None,
                "avg_delay_minutes": round(avg_delay, 2) if avg_delay else None,
                "cancellation_rate": round(cancellation_rate, 4) if cancellation_rate else None,
                "yearly_passenger_totals": yearly_totals,
                "capacity_capped": capacity_capped,
            }

            metrics_list.append(AirportMetrics(
                iata_code=code,
                ops=ops,
                rated_capacity=rated_capacity,
                yearly_passenger_totals=yearly_totals,
                avg_delay_minutes=avg_delay,
                load_factor=load_factor,
                capacity_capped_flag=capacity_capped,
                cancellation_rate=cancellation_rate,
            ))

        scored = score_airports(metrics_list)

        return {
            "data_year": latest_year,
            "trend_years": f"{trend_start}–{latest_year}",
            "ranking": [
                {
                    "rank": i + 1,
                    "iata_code": s.iata_code,
                    "score": s.score,
                    "confidence": s.confidence,
                    "components": {
                        "utilization": s.utilization_score,
                        "growth":      s.growth_score,
                        "unmet_demand": s.unmet_demand_score,
                        "capex_penalty": s.capex_penalty,
                    },
                }
                for i, s in enumerate(scored)
            ],
            "raw_inputs": fetch_log,
        }

    finally:
        # Restore original weights if we overrode them
        if weight_override:
            _scoring.W_UTILIZATION, _scoring.W_GROWTH, _scoring.W_UNMET_DEMAND, _scoring.W_CAPEX_PENALTY = orig


# ---------------------------------------------------------------------------
# 3.1.1 + 3.1.2  Anthropic tool schemas
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "fetch_bts_t100",
        "description": (
            "Fetch BTS T-100 segment data for a single airport and year from the local CSV cache. "
            "Returns per-route departure counts, seats, passengers, distance, and carrier. "
            "Use this for detailed route-level analysis or to compute ops and load factor manually."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport_code": {
                    "type": "string",
                    "description": "IATA airport code (e.g. 'BOS').",
                },
                "year": {
                    "type": "integer",
                    "description": "Calendar year (e.g. 2024). Must have a local t100_{year}.csv file.",
                },
            },
            "required": ["airport_code", "year"],
        },
    },
    {
        "name": "fetch_bts_ontime",
        "description": (
            "Return on-time performance summary for an airport and year from cache/ontime.csv. "
            "Provides avg_delay_minutes, cancellation_rate, total_flights, and months_covered. "
            "Returns null if the airport/year has no rows in the CSV."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport_code": {
                    "type": "string",
                    "description": "IATA airport code (e.g. 'BOS').",
                },
                "year": {
                    "type": "integer",
                    "description": "Calendar year to query.",
                },
            },
            "required": ["airport_code", "year"],
        },
    },
    {
        "name": "fetch_bts_passenger_trend",
        "description": (
            "Return year-over-year total passenger counts for an airport across a range of years. "
            "Data comes from the local T-100 CSV files. Years with no CSV are silently skipped. "
            "Use this to assess growth trends before running the scorer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport_code": {
                    "type": "string",
                    "description": "IATA airport code (e.g. 'BOS').",
                },
                "start_year": {
                    "type": "integer",
                    "description": "First year of the trend window (inclusive).",
                },
                "end_year": {
                    "type": "integer",
                    "description": "Last year of the trend window (inclusive).",
                },
            },
            "required": ["airport_code", "start_year", "end_year"],
        },
    },
    {
        "name": "fetch_route_stats",
        "description": (
            "Return pre-aggregated route statistics for all departures from an airport in a given year. "
            "Use this — not fetch_bts_t100 — for any question involving percentages, counts, or "
            "breakdowns across the full route network (e.g. 'what % of flights are long-haul?', "
            "'how many international routes?', 'top destinations'). "
            "Returns distance buckets (short/medium/long/ultra-long), domestic vs. international split, "
            "load factor, and top 10 destinations by ops. Never truncates data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport_code": {
                    "type": "string",
                    "description": "IATA airport code (e.g. 'ANC').",
                },
                "year": {
                    "type": "integer",
                    "description": "Calendar year (e.g. 2024). Must have a local t100_{year}.csv file.",
                },
            },
            "required": ["airport_code", "year"],
        },
    },
    {
        "name": "fetch_live_flights",
        "description": (
            "Fetch live arrival and departure counts from the OpenSky Network for the last N hours. "
            "Useful for real-time congestion spot-checks. Results are cached for 30 minutes. "
            "Requires OPENSKY_CLIENT_ID and OPENSKY_SECRET_KEY env vars."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport_iata": {
                    "type": "string",
                    "description": "IATA airport code (e.g. 'BOS'). Converted to ICAO internally.",
                },
                "hours_back": {
                    "type": "number",
                    "description": "Lookback window in hours (default 4, max 168).",
                },
            },
            "required": ["airport_iata"],
        },
    },
    {
        "name": "fetch_airport_info",
        "description": (
            "Fetch airport facility data from OpenAIP: runway count, max runway length, and an "
            "estimated annual operations capacity (rated_capacity_proxy). "
            "IMPORTANT: rated_capacity_proxy is a rough estimate, not an FAA-certified value. "
            "Always surface the capacity_proxy_note field when citing this number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport_iata": {
                    "type": "string",
                    "description": "IATA airport code (e.g. 'BOS').",
                },
            },
            "required": ["airport_iata"],
        },
    },
    {
        "name": "list_airports",
        "description": (
            "Return the list of IATA codes for commercial airports in a US state or region. "
            "Accepts full state names (e.g. 'Massachusetts'), 2-letter abbreviations (e.g. 'MA'), "
            "or 'new england' for all six New England states combined."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "description": "State name, 2-letter abbreviation, or 'new england'.",
                },
            },
            "required": ["state"],
        },
    },
    {
        "name": "compare_airports",
        "description": (
            "Fetch all available data for each airport in the list, run the scoring engine, "
            "and return a ranked table with per-component score breakdown and confidence level. "
            "This is the primary tool for investment ranking questions. "
            "Optional weight overrides let you re-run the scorer with different priorities — "
            "all four weights must be provided together and must sum to 1.0."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of IATA airport codes to compare (e.g. ['BOS', 'PVD', 'MHT']).",
                },
                "w_utilization": {
                    "type": "number",
                    "description": "Weight for utilization component (0–1). Omit to use defaults.",
                },
                "w_growth": {
                    "type": "number",
                    "description": "Weight for passenger growth component (0–1).",
                },
                "w_unmet_demand": {
                    "type": "number",
                    "description": "Weight for unmet demand proxy component (0–1).",
                },
                "w_capex_penalty": {
                    "type": "number",
                    "description": "Weight for CAPEX penalty deduction (0–1).",
                },
            },
            "required": ["codes"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher — called by the agent loop (3.3)
# ---------------------------------------------------------------------------

def dispatch_tool(name: str, args: dict) -> str:
    """Execute a tool by name and return a JSON string result.

    DataFrames are converted to record-oriented dicts. All other return
    values are serialised with json.dumps. Returns a JSON error string
    on unknown tool name or unhandled exception.
    """
    try:
        if name == "fetch_bts_t100":
            df = fetch_bts_t100(**args)
            if df is None:
                result = {"data": None, "note": "No T-100 data for this airport/year."}
            else:
                result = {
                    "rows": len(df),
                    "data": df.head(50).to_dict(orient="records"),  # cap at 50 rows for context size
                    "note": f"{len(df)} routes total; showing first 50." if len(df) > 50 else None,
                }

        elif name == "fetch_bts_ontime":
            result = fetch_bts_ontime(**args) or {"data": None, "note": "No on-time data found."}

        elif name == "fetch_bts_passenger_trend":
            result = fetch_bts_passenger_trend(**args) or {"data": None, "note": "No trend data found."}

        elif name == "fetch_route_stats":
            result = fetch_route_stats(**args) or {"data": None, "note": "No T-100 data for this airport/year."}

        elif name == "fetch_live_flights":
            result = fetch_live_flights(**args) or {"data": None, "note": "Live flight fetch failed."}

        elif name == "fetch_airport_info":
            result = fetch_airport_info(**args) or {"data": None, "note": "Airport info not available."}

        elif name == "list_airports":
            result = list_airports(**args)

        elif name == "compare_airports":
            result = compare_airports(**args)

        else:
            result = {"error": f"Unknown tool: {name!r}"}

    except Exception as exc:
        logger.error("dispatch_tool(%s) error: %s", name, exc)
        result = {"error": str(exc)}

    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# 3.3  Agent loop
# ---------------------------------------------------------------------------

_CLIENT = anthropic.Anthropic()


def run_agent(
    messages: list[dict],
    user_input: str,
) -> tuple[list[dict], str, list[dict]]:
    """Run one user turn through the agent and return the updated conversation.

    Args:
        messages: Full conversation history from previous turns. Never mutated.
        user_input: The new user message text.

    Returns:
        (updated_messages, assistant_text, tool_calls_metadata)
        - updated_messages: new list including this turn's user + assistant messages
        - assistant_text: concatenated text blocks from the final assistant response
        - tool_calls_metadata: list of {tool_name, tool_input} for every tool called
    """
    # Build the new history without mutating the caller's list
    history = list(messages) + [{"role": "user", "content": user_input}]

    tool_calls_made: list[dict] = []
    assistant_text = ""

    while True:
        response = _CLIENT.messages.create(
            model="claude-haiku-4-5",
            max_tokens=8192,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # cached across turns
            }],
            tools=TOOLS,
            messages=history,
        )

        # Convert SDK content block objects → plain dicts so history stays
        # JSON-serializable (needed for Streamlit session_state storage).
        content_dicts = [block.model_dump() for block in response.content]
        history = history + [{"role": "assistant", "content": content_dicts}]

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    assistant_text += block.text
            break

        if response.stop_reason == "tool_use":
            # Capture any inline text the model wrote before the tool calls
            for block in response.content:
                if block.type == "text":
                    assistant_text += block.text

            # Dispatch every tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_calls_made.append({
                        "tool_name": block.name,
                        "tool_input": block.input,
                    })
                    result_str = dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            history = history + [{"role": "user", "content": tool_results}]

        else:
            logger.warning("run_agent: unexpected stop_reason=%s", response.stop_reason)
            for block in response.content:
                if block.type == "text":
                    assistant_text += block.text
            break

    return history, assistant_text, tool_calls_made


def run_agent_streaming(messages: list[dict], user_input: str):
    """Generator version of run_agent; yields events as the agent runs.

    Events (event_type, data):
        "tool_call"  — {"tool_name": str, "tool_input": dict, "result": str}
        "text_chunk" — str  (final assistant text; may arrive as one block)
        "done"       — {"history": list, "tool_calls": list, "assistant_text": str}
    """
    history = list(messages) + [{"role": "user", "content": user_input}]
    tool_calls_made: list[dict] = []
    assistant_text = ""

    while True:
        response = _CLIENT.messages.create(
            model="claude-haiku-4-5",
            max_tokens=8192,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=TOOLS,
            messages=history,
        )

        content_dicts = [block.model_dump() for block in response.content]
        history = history + [{"role": "assistant", "content": content_dicts}]

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    assistant_text += block.text
                    yield ("text_chunk", block.text)
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_str = dispatch_tool(block.name, block.input)
                    tool_info = {
                        "tool_name": block.name,
                        "tool_input": block.input,
                        "result": result_str,
                    }
                    tool_calls_made.append(tool_info)
                    yield ("tool_call", tool_info)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })
            history = history + [{"role": "user", "content": tool_results}]
        else:
            logger.warning("run_agent_streaming: unexpected stop_reason=%s", response.stop_reason)
            break

    yield ("done", {
        "history": history,
        "tool_calls": tool_calls_made,
        "assistant_text": assistant_text,
    })
