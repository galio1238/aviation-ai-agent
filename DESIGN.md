# Design Notes

## 1. Scoring Methodology

The final score is a weighted sum of four components:

```
score = W_UTILIZATION × util + W_GROWTH × growth + W_UNMET_DEMAND × unmet − W_CAPEX_PENALTY × capex
```

All four weights are currently set to **0.25** (equal weighting). These are placeholder values — the weights need to be revisited and tuned once there is enough research to justify prioritizing one signal over another. A heavier weight on growth vs. utilization, for example, would favor airports in developing markets over already-busy ones.

Before the weighted sum is computed, each positive component (utilization, growth, unmet demand) is **min-max normalized across the airports being compared**. This means scores are relative: 1.0 is the best airport in that group, 0.0 is the worst. There is no absolute interpretation — the same airport could score differently in a different comparison set. The CAPEX penalty is binary (0 or 1) and is applied directly without normalization.

When a component is missing (no data), its weight is redistributed proportionally to the remaining components so the composite always stays in [0, 1].

---

## 2. Unmet Demand Proxy

The `unmet_demand_proxy` is trying to measure how much of an airport's demand is being absorbed or strained — essentially, how "in use" the airport already is relative to what it can handle.

True unmet demand (passengers who wanted to fly but couldn't get a seat or flight) does not exist in any public dataset. Instead, we use four indirect signals as a proxy:

- **Load factor** (passengers ÷ available seats): high seat utilization means demand is saturating supply
- **Average arrival delay** (BTS on-time): chronic delays signal congestion and capacity strain
- **Cancellation rate** (T-100 departures performed ÷ scheduled): used as a substitute when delay data is unavailable — high cancellations reflect the same operational pressure
- **Capacity-capped flag**: whether the airport is already operating at or above its rated runway capacity

The intuition is simple: the busier and more strained an airport is, the more pent-up demand it signals. Each signal is independently scaled to [0, 1] and averaged over whichever are available.

What this misses: it doesn't measure people who gave up and didn't book. It also doesn't distinguish between an airport that's busy because it's well-run vs. one that's busy because it has no competition. It's a congestion signal, not a true demand gap.

---

## 3. Live API + Cache Tradeoff

The project uses two live APIs with free-tier rate limits (OpenSky, OpenAIP). Without caching, every agent run would make repeated API calls and quickly burn through the free quota.

Cache TTLs chosen:

| Data type | TTL | Reason |
|---|---|---|
| Live flight data (OpenSky) | 30 minutes | Data changes frequently but re-fetching within a session wastes calls |
| Airport facility data (OpenAIP) | 1 week (168 hrs) | Runway counts and physical airport data rarely changes |

The cache is JSON files on disk in the `cache/` directory. There is no eviction beyond TTL expiry — old files are deleted on the next read attempt once expired. The tradeoff accepted here is slightly stale live data in exchange for not hitting rate limits or incurring latency on every turn.

---

## 4. LLM Role vs. Deterministic Logic

All scoring is deterministic. The model does not participate in calculating scores, weights, or any numeric result — those come exclusively from `scoring.py` and the `compare_airports` tool.

**What the model is allowed to do:** receive tool results and build a coherent answer from them. It can decide which airports to compare, which tools to call, how to frame the analysis, and what to highlight for the user based on the data.

**What the model must never do:** invent numbers. Every passenger count, delay figure, load factor, score, or capacity value in a response must come from a tool call. If the data isn't there, the model should say so — not fill the gap with a plausible-sounding estimate.

The line is: the model reasons over real data, it does not manufacture data.

---

## 5. Known Data Gaps

**FAA capacity proxy**

There is no single authoritative public source for airport capacity (in terms of annual operations). The FAA does publish capacity studies for major airports, but they are not available via API, are airport-specific, and require manual lookup.

Instead, we estimate capacity using runway count from OpenAIP:

```
rated_capacity_proxy = runway_count × 30 ops/hr × 16 hrs/day × 365 days/year
```

30 ops/runway/hour is a conservative IFR mixed-operations assumption. The result is a rough upper-bound — it will overestimate capacity at airports with short runways, poor IFR equipment, or single-runway constraints, and underestimate it at highly optimized hubs. Callers should treat this as an order-of-magnitude estimate only.

**OpenSky Network coverage**

OpenSky is community-sourced ADS-B receiver data, not an official FAA feed. Coverage gaps exist:

- Remote and rural airports (parts of Alaska, smaller western states) have sparse ground station coverage
- The free tier has rate limits — sustained querying across many airports in a single session will hit the 429 limit
- Historical data depth on the free tier is limited; older windows may return incomplete results
- Alaska and Hawaii airports use non-K-prefix ICAO codes (e.g. PANC, PHNL) and require override mappings
