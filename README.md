# Airport Investment Intelligence Agent

AI-powered conversational agent for identifying US airport modernization and expansion opportunities.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — ANTHROPIC_API_KEY is required; the rest unlock live data sources
```

## Run

```bash
streamlit run app.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key — powers the Claude Haiku agent |
| `OPENSKY_CLIENT_ID` | No | OpenSky Network OAuth2 client ID — enables live flight data |
| `OPENSKY_SECRET_KEY` | No | OpenSky Network OAuth2 client secret |
| `OPENAIP_KEY` | No | OpenAIP API key — enables airport facility data (runway count, capacity proxy) |

Without `OPENSKY_CLIENT_ID` / `OPENSKY_SECRET_KEY`, live flight queries will fail silently. Without `OPENAIP_KEY`, the utilization score will have no capacity data to work with. BTS T-100 and on-time data comes from local CSV files in `cache/` and needs no API key.

## Design & Architecture

### How AI is used

The agent is [Claude Haiku](https://www.anthropic.com/claude) (`claude-haiku-4-5`) running in a tool-use loop. On each user turn it decides which data tools to call, calls them, then synthesizes the results into a response. **The model does not invent numbers** — every statistic in a response must come from a tool call in that conversation. Scoring is fully deterministic; the model interprets results, it does not compute them.

### Scoring methodology

Airports are ranked by a weighted sum of four components, each normalized to [0, 1] within the comparison group (so 1.0 = best in that set, not an absolute quality score):

| Component | Default weight | What it measures |
|---|---|---|
| Utilization | 25% | Annual ops ÷ rated capacity proxy — how close the airport is to its ceiling |
| Passenger growth | 25% | 3-year CAGR from BTS T-100 passenger totals |
| Unmet demand proxy | 25% | Average of: delay signal, cancellation rate, load factor, capacity-capped flag |
| CAPEX penalty | 25% | Deducted if a major infrastructure project was completed in the last 5 years |

Weights are equal placeholders and should be tuned after further research. They can also be overridden per query via the `compare_airports` tool.

The **unmet demand proxy** is the most indirect component. It uses congestion signals (delays, cancellations, high load factor) as a stand-in for true unmet demand, which is not available in public datasets. The intuition: a busier, more strained airport is likely suppressing demand it could otherwise serve.

### Key tradeoffs

**Capacity is a proxy, not a real measurement.** There is no public API for FAA-certified airport capacity. Instead, capacity is estimated from runway count using a conservative 30 ops/runway/hour assumption. This will over- or underestimate for specific airports and should be treated as order-of-magnitude only.

**Live data is cached aggressively to protect free-tier API quotas.** OpenSky flight data is cached for 30 minutes; OpenAIP facility data is cached for 1 week. This means live flight counts may be slightly stale within a session.

**Scores are relative, not absolute.** The same airport can score differently depending on who it's being compared against. A score of 0.8 means "strong within this group" — it says nothing about the airport's absolute investment quality.

**OpenSky coverage has gaps.** OpenSky is community-sourced ADS-B data. Remote airports (parts of Alaska, rural areas) have patchy coverage, and the free tier has rate limits that can cause queries to fail silently.

## Example Questions

- Which airports in New England are strong candidates for terminal expansion?
- Compare LA and Santa Ana airport congestion levels.
- What is the percentage of long-haul flights out of Anchorage airport?
- What is the unmet flight demand at SFO and why?

## Future Improvements

### Richer demand signals
- **State / city GDP and population growth** — correlate airport traffic trends against regional economic growth to distinguish airports serving expanding vs. stagnant markets
- **Tourism and hospitality data** — hotel occupancy rates and visitor counts as a demand signal for leisure-heavy airports
- **Corporate headquarters density** — proxy for business travel demand in the airport's catchment area

### Better capacity data
- **Airport apron / ramp area** — physical aircraft parking space as an additional capacity ceiling alongside runway count; a large apron with few runways signals different constraints than the reverse
- **Gate count** — terminal gate data (currently unavailable via public API) would replace the runway-based capacity proxy with a tighter operational limit
- **FAA ATADS operations data** — official tower-counted ops for towered airports, replacing the estimated runway throughput figure

### Scoring improvements
- **Weight calibration** — validate the four scoring weights against historical investment outcomes to move beyond the current equal-weight placeholder
- **Peer group normalization** — compare airports against similar-size peers (hub vs. regional vs. general aviation) instead of mixing all sizes in one group
- **Multi-year scoring trends** — show how an airport's score has moved over time, not just a snapshot

### Data coverage
- **FAA Airport Master Record (Form 5010)** — adds gate count, terminal square footage, and parking capacity from official FAA records
- **Census commuter flow data** — identify airports in high-growth metro areas where population is outpacing infrastructure
- **Airline route announcements** — planned new routes as a leading indicator of demand growth before it shows up in T-100 data

## Known Limitations

- Capacity figures are estimates based on runway count — not FAA-certified values
- Unmet demand is a proxy signal (congestion), not direct measurement of booking demand
- Scoring weights are equal placeholders; they have not been validated against real investment outcomes
- OpenSky live flight data is unavailable without credentials and has patchy rural coverage
- BTS data lags reality by months; the most recent year in `cache/` may be incomplete
