# app.py — Phase 4
import json

import pandas as pd
import streamlit as st

from agent import run_agent_streaming

st.set_page_config(page_title="Airport Investment Intelligence", layout="wide")

# ---------------------------------------------------------------------------
# Helpers — 4.2.2 tool labels, 4.2.3 scoring table
# ---------------------------------------------------------------------------

_TOOL_LABEL_FNS = {
    "list_airports":             lambda a: f"Listed airports — {a.get('state', '').title()}",
    "compare_airports":          lambda a: f"Scored {len(a.get('codes', []))} airports",
    "fetch_bts_t100":            lambda a: f"T-100 data — {a.get('airport_code', '')} {a.get('year', '')}",
    "fetch_route_stats":         lambda a: f"Route stats — {a.get('airport_code', '')} {a.get('year', '')}",
    "fetch_bts_ontime":          lambda a: f"On-time data — {a.get('airport_code', '')} {a.get('year', '')}",
    "fetch_bts_passenger_trend": lambda a: f"Passenger trend — {a.get('airport_code', '')} {a.get('start_year', '')}–{a.get('end_year', '')}",
    "fetch_live_flights":        lambda a: f"Live flights — {a.get('airport_iata', '')}",
    "fetch_airport_info":        lambda a: f"Airport info — {a.get('airport_iata', '')}",
}


def _tool_label(name: str, args: dict) -> str:
    fn = _TOOL_LABEL_FNS.get(name)
    return fn(args) if fn else name


def _render_tool_expanders(tool_calls: list[dict]) -> None:
    for tc in tool_calls:
        label = _tool_label(tc["tool_name"], tc["tool_input"])
        with st.expander(f"⚙ {label}", expanded=False):
            st.json(tc["tool_input"])


def _render_scoring_table(tool_calls: list[dict]) -> None:
    """Render a scored-airport DataFrame for any compare_airports results."""
    for tc in tool_calls:
        if tc["tool_name"] != "compare_airports":
            continue
        try:
            result = json.loads(tc["result"])
            ranking = result.get("ranking")
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if not ranking:
            continue

        rows = [
            {
                "Rank":          r["rank"],
                "Airport":       r["iata_code"],
                "Score":         round(r["score"], 3),
                "Confidence":    r["confidence"].capitalize(),
                "Utilization":   round(r["components"]["utilization"], 3),
                "Growth":        round(r["components"]["growth"], 3),
                "Unmet Demand":  round(r["components"]["unmet_demand"], 3),
                "CAPEX Penalty": round(r["components"]["capex_penalty"], 3),
            }
            for r in ranking
        ]
        year   = result.get("data_year", "")
        trend  = result.get("trend_years", "")
        st.caption(
            f"Scoring table — {year} data, trend {trend}  "
            f"(scores are relative within this group; 1.0 = best)"
        )
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []   # raw agent history (passed to run_agent_streaming)

if "turns" not in st.session_state:
    st.session_state.turns = []      # list of {user_text, assistant_text, tool_calls}

# ---------------------------------------------------------------------------
# 4.1.1 + 4.2.2 + 4.2.3  History replay
# ---------------------------------------------------------------------------

for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["user_text"])
    with st.chat_message("assistant"):
        _render_tool_expanders(turn["tool_calls"])
        st.markdown(turn["assistant_text"])
        _render_scoring_table(turn["tool_calls"])

# ---------------------------------------------------------------------------
# 4.1.2 + 4.2.1 + 4.2.2 + 4.2.3  Live turn
# ---------------------------------------------------------------------------

if prompt := st.chat_input("Ask about US airport investment opportunities…"):
    with st.chat_message("user"):
        st.markdown(prompt)

    tool_calls_this_turn: list[dict] = []
    accumulated_text = ""
    text_area = None
    updated_messages = st.session_state.messages

    with st.chat_message("assistant"):
        for event_type, data in run_agent_streaming(st.session_state.messages, prompt):

            if event_type == "tool_call":
                tool_calls_this_turn.append(data)
                label = _tool_label(data["tool_name"], data["tool_input"])
                with st.expander(f"⚙ {label}", expanded=False):
                    st.json(data["tool_input"])

            elif event_type == "text_chunk":
                if text_area is None:
                    text_area = st.empty()
                accumulated_text += data
                text_area.markdown(accumulated_text)

            elif event_type == "done":
                updated_messages = data["history"]
                _render_scoring_table(tool_calls_this_turn)

    st.session_state.turns.append({
        "user_text":      prompt,
        "assistant_text": accumulated_text,
        "tool_calls":     tool_calls_this_turn,
    })
    st.session_state.messages = updated_messages
