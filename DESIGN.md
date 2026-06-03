# Design Notes

## 6. Related Work — Academic MCDM Methods for Airport Investment

> Reference: Caetano et al. (2022), *Criteria prioritization for investment policies in General Aviation aerodromes*, Regional Science Policy & Practice, 14(6), 211–233.
> DOI / full text: https://www.sciencedirect.com/science/article/pii/S1757780223002494

This section places this project's scoring approach in the context of established academic literature.

---

### 6.1 The Caetano et al. (2022) Model

The closest published precedent to this project. They prioritize GA aerodromes in Brazil using a hybrid TOPSIS + EATWOS method with both quantitative and binary (dummy) variables.

**Their pipeline:**

**1. Min-max normalization** — same as this project's `normalize_min_max`:
```
x'ij = (xij - xij_min) / (xij_max - xij_min)
```

**2. Distance from worst** (how far each airport is from the lowest performer):
```
dij = 1 - (xij'_min - x'ij)
```

**3. Entropy-based weights** — the key difference from this project's equal 0.25 weights. Weights are derived from the data using Shannon entropy, so variables that discriminate more between airports automatically receive higher weight:
```
x''ij = xij / Σ xij          (column-normalize)
ej    = -k · Σ [x''ij · ln(x''ij)]   (entropy; k = 1/ln(n))
wj    = (1 - ej) / Σ (1 - ej)         (weight)
```
In their results, formal jobs (JOB) received the highest weight (33%), GDP second (11%). Variables with little spread across airports get low weight automatically.

**4. Partial score:**
```
Spi = Σ (wj · dij)
```

**5. Dummy variable multiplier** — qualitative factors (noise zoning, IFR capability, tourism classification, Amazon region flag) are applied multiplicatively rather than additively:
```
Sfi = Spi · (1 + Σ ri · φr)
```
This scales the qualitative bonus proportionally to how strong the airport already scores, rather than adding a flat amount.

**6. Relative priority** — output is a budget allocation percentage, not just a rank:
```
Pri = Sfi / Σ Sfi        (sums to 100% across the group)
```
Top airport in their 100-aerodrome sample received 2.22% of available budget.

---

### 6.2 Variables Compared

| Variable | Caetano et al. | This project |
|---|---|---|
| Population / GDP / formal jobs | ✅ | ✗ — would need Census/BEA API |
| Cities served (Voronoi diagram) | ✅ | ✗ — requires geospatial analysis |
| Runway length / width | ✅ (shorter = higher priority) | ✅ (used for capacity proxy) |
| Annual ops / passengers | ✅ (GA proxy — data scarce) | ✅ (BTS T-100 direct) |
| Delay / load factor | ✗ | ✅ (BTS on-time) |
| Noise zoning / IFR capability | ✅ (dummy) | ✗ |
| Recent CAPEX project | ✗ | ✅ (stub — always False) |

Their model is richer on socioeconomic and spatial variables. This project is richer on operational aviation data (delays, load factor, route stats, live flights).

---

### 6.2b Supporting Literature — Unmet Demand and Utilization Components


> Transportation Research Part E: Logistics and Transportation Review, 2019–2020.
> Full text: https://www.sciencedirect.com/science/article/abs/pii/S0969699719304442

This paper provides direct academic grounding for two of this project's four scoring components.

**Support for `unmet_demand`:**
The paper's central premise is that airports operating at or near capacity limit create the case for investment. It explicitly models demand vs. capacity as movements per year, and treats the gap between actual demand and available capacity as the primary investment trigger — structurally identical to this project's `unmet_demand_proxy`. The paper notes that "many European airports are already operating near or at their capacity limit" and frames investment urgency in terms of that gap.

**Support for `utilization`:**
The paper constructs a detailed delay-cost function to quantify the harm caused by high utilization. Using Cook and Tanner (2015), it models delay costs for a representative aircraft (B737-800) ranging from €40 at 5 minutes to €70,000 at 300 minutes — covering passenger compensation, re-routing, and staff costs. This validates the intuition behind the utilization component: congestion at high-utilization airports has measurable, non-linear costs that grow sharply as capacity is approached.

**What this paper does not cover:**
It uses real options theory (optimal investment timing and sizing for a single airport under demand uncertainty), not a multi-criteria scoring framework. It does not address how to weight utilization against other signals or how to rank a portfolio of airports — that gap is filled by Caetano et al. (2022) and the MCDM literature in §6.3.

---

### 6.2c Supporting Literature — Utilization Measurement and Capacity Gap Classification

> *Airport capacity constraints and air traffic demand in China*,
> Transportation Research Part E: Logistics and Transportation Review, 2022.
> Full text: https://www.sciencedirect.com/science/article/abs/pii/S0969699722000710

This paper provides methodological grounding for how utilization and unmet demand should be measured and classified, directly paralleling this project's proxy approach.

**Support for `utilization`:**
The paper introduces a **Congestion Status Index (CSI)** — the fraction of hours in a year during which an airport's actual movements exceed its declared capacity. This is a direct, empirically validated operationalization of the utilization concept: Beijing Capital (PEK) reaches CSI = 0.576, Shenzhen (SZX) 0.555, Xi'an (XIY) 0.506 — meaning more than half of all hours are overloaded at these airports. The paper confirms that utilization-based congestion metrics expose operationally meaningful and large differences across airports.

**Support for `unmet_demand`:**
The paper introduces an **Available Capacity Index (ACI)** measuring spare capacity relative to potential, and combines CSI + ACI into a **classification matrix** to identify the most severely capacity-constrained airports. This supply–demand gap framing is structurally identical to this project's `unmet_demand_proxy`: both measure the distance between what an airport is currently handling and what it can handle. The paper's finding that only 34 of 239 airports (14.2%) experience any overload validates that capacity constraint is a sparse, right-skewed signal — consistent with most airports scoring near zero on unmet demand in any comparison set.

**Additional relevance:**
The paper uses Monte Carlo simulation to forecast capacity utilization under demand uncertainty through 2025 and 2035, confirming that growth forecasts are a necessary complement to current utilization — supporting the inclusion of the `growth` component alongside utilization and unmet demand in the scoring formula.

**What this paper does not cover:**
It is descriptive and China-specific. It does not provide a multi-criteria scoring framework or address how to combine capacity signals with other investment criteria. The MCDM framework remains sourced from Caetano et al. (2022).

---

### 6.3 Other MCDM Methods in the Literature

**AHP (Analytic Hierarchy Process)** — criteria are compared pairwise by an expert panel to derive weights. More transparent than entropy-weighting but introduces subjectivity. This project's equal-weight setup is the degenerate (no-opinion) case of AHP.

**TOPSIS** — ranks alternatives by their Euclidean distance from the ideal best *and* ideal worst simultaneously. More robust than pure weighted sum when outliers are present. This project's normalization + weighted sum is a simpler variant.

**ELECTRE-TRI** — classifies airports into ordered tiers (high / medium / low priority) instead of a continuous score. Useful when the output is investment tiers rather than a ranked list. This project's `confidence` field is a rough analogue.

**MARCOS** — scores alternatives by their ratio of utility relative to both ideal and anti-ideal bounds. Produces scores more stable when the comparison set changes — partially addressing the "relative scoring" problem of min-max normalization.

---

### 6.4 What This Project Would Gain from the Academic Approach

1. **Entropy weights** — data-driven weights instead of equal 0.25; automatically upweights the variables that actually differentiate airports in each comparison.
2. **Socioeconomic variables** — GDP, population, and employment in the airport's catchment area; the paper shows these dominate the weight distribution.
3. **Multiplicative dummy terms** — more expressive than a flat CAPEX deduction.
4. **Budget allocation output** — expressing results as "X% of available budget" is more actionable for an investment firm than a 0–1 score.

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
