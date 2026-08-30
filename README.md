# KSP Crime Intelligence — AI-Driven Crime Analytics \& Visualization Platform

A live, event-driven crime analytics dashboard for the Karnataka State Police (SCRB), built on Zoho Catalyst. It turns siloed case records into a 360-degree intelligence view — spatial, temporal, relational, and predictive — with in-function trained machine-learning models, bilingual UI (English / ಕನ್ನಡ), and near-real-time data freshness.

Companion track: a conversational crime-intelligence agent (English/Kannada, cited answers, audit trail) served from the same Catalyst project; the two surfaces deep-link into each other.

\---

## Contents

* [Features](#features)
* [Architecture](#architecture)
* [Repository structure](#repository-structure)
* [Prerequisites](#prerequisites)
* [Setup](#setup)
* [Deployment](#deployment)
* [One-time data operations](#one-time-data-operations)
* [Event-driven freshness (Signals)](#event-driven-freshness-signals)
* [Running \& verifying](#running--verifying)
* [API reference](#api-reference)
* [Machine learning](#machine-learning)
* [Localization](#localization)
* [Benchmarking](#benchmarking)
* [Design notes \& honest limits](#design-notes--honest-limits)

\---

## Features

### Geospatial intelligence

* **District choropleth** — all 30 Karnataka districts (Census 2011 boundaries, slimmed GeoJSON \~104 KB) shaded by case volume;
* **Station markers** — proportional circles (area = case volume), click-to-drill
* **Incident heatmap** and **night-only layer** 
* **Spike pulses** — animated red halos on districts with active statistical spikes, zoom-interpolated radius
* **Time scrubber with playback** — slider animating both the heatmap and the choropleth month-by-month; 

### Alerting \& statistics

* **Emerging trend alerts** — district × crime-head spikes at ≥ 2σ versus up to 12 trailing 90-day baselines of the same region, with z-score and % change
* **Spatiotemporal hotspots** — station × time-of-day matrix, z-scored on incidents/hour
* **Anomaly detection** — per-case rarity scoring (−log₂ probability across learned frequency models + spatial deviation) with plain-language reasons; 2-per-pattern diversity cap
* **Patrol deployment recommendations** — per-station ranked patrol windows (rate/hour) derived from the hotspot matrix, critical/elevated tags, **printable duty chart**

### Prediction \& ML

* **Predictive risk scores** — 0–100 per station (45% volume, 35% Holt trend, 20% heinous share) with 3-month forecasts
* **Emerging crime typologies** — per-crime-head Holt forecasts over 24 months; growth ranking with sparklines (history + dashed forecast); names the fastest-growing type
* **Trained case-outcome models (champion/challenger)** — logistic regression and gradient-boosted trees trained in-function on resolved cases, honest holdout metrics on screen, model dropdown; see [Machine learning](#machine-learning)
* **Case triage** — open (Under Investigation) cases ranked by P(goes undetected), per-case driver chips, diversity-capped
* **What-If Risk Explorer** — interactive panel computing live probabilities client-side from the linear model's coefficients; diverging contribution bars (raises/lowers risk)

### Relational intelligence

* **Criminal network graph** (chat client, Cytoscape) — suspects (circles), **recurring locations** (brass squares, stations shared by ≥ 2 people), **victims** (cyan diamonds) with co-offending, frequents, and victim-of edges; tap a suspect to pull their history from the agent
* **Repeat offender profiles** — cross-jurisdiction, true minor-head **MO badges** ("Chain Snatching ×11"), expandable case timelines
* **MO linkage** — open cases matching a repeat offender's dominant MO (≥ 3 same-MO cases) across jurisdictions, with same-turf tags
* **Case detail drawer** — click any CrimeNo anywhere: slide-in panel with full record, BriefFacts, linked persons with roles (accused/victim), show-on-map fly-to, ask-the-agent handoff

### Outcomes \& reporting

* **Case outcome funnel** — Registered → Police disposed → Charge sheeted → Court disposed → Convicted; conviction rate (of court-disposed), chargesheet rate (of police-disposed), pendency; per-district and per-head tables with threshold coloring
* **Socio-economic overlay** — Census 2011 (population, urbanization, density, literacy), cases per lakh, live Pearson correlations per crime head
* **Intelligence Brief** — one click composes a formal report (scope, spikes, risk, typologies, triage, funnel, anomalies, methods) as a print/Save-as-PDF document
* **Shareable URL state** — every filter, the language, and the model choice serialize to the querystring; a URL is a complete analytical view

### Platform

* **Full filter bar** — district, crime head, MO (minor head, dependent on head), station, date range, Heinous-only; composes across every panel including the scrubber and ML triage
* **Event-driven freshness** — Data Store change → Catalyst **Signals** → version bump in Catalyst Cache → analytics background reload (≤ 5 s server-side) → dashboard poll (90 s) with **wake-catchup** on tab focus; count-check fallback (60 s) if Signals is unconfigured; models retrain on every data change
* **Bilingual UI** — English / ಕನ್ನಡ toggle, persisted in the URL
* **Bidirectional deep links** — dashboard CrimeNos → agent conversation; agent graph nodes → history queries
* **Fail-loud client** — endpoint failures name the exact route; drawer degrades to direct chat links

\---

## Architecture

```
Zoho Catalyst project (EU) — "Project-Rainfall"
│
├─ Data Store
│   ├─ CaseFlat            \\\~5,000 cases (station, district, head, minor head,
│   │                      gravity, status, hourly timestamps, lat/lng, BriefFacts)
│   ├─ ResolvedPerson      entity-resolved identities (accused + victims)
│   ├─ PersonCaseLink      person↔case links with RoleTable (Accused | Victim)
│   ├─ Accused             raw accused rows (input to entity resolution)
│   └─ Auditlog            agent query audit trail (excluded from Signals)
│
├─ Functions
│   ├─ analytics    (Node 18, Advanced I/O)  — all dashboard APIs + ML training
│   ├─ crime-agent  (Python 3.13, Advanced I/O) — conversational agent (GLM via QuickML)
│   ├─ data-events  (Node 18, Event)         — Signals target: bumps cache version key
│   ├─ seeder       (Python 3.13, Cron)      — synthetic data generation
│   └─ er-nightly   (Python 3.13, Cron)      — entity resolution maintenance
│
├─ Signals (event bus)
│   └─ datastore-pub → row rules (CaseFlat, PersonCaseLink, ResolvedPerson) → data-events
│
├─ Cache
│   └─ segment key "rainfall\\\_data\\\_version" (bumped by data-events, polled by analytics)
│
└─ Web client
    ├─ index.html                  — agent console (chat, network graph, map, PDF export)
    ├─ analytics.html              — the dashboard
    └─ karnataka-districts.geojson — district polygons (must sit next to analytics.html)
```

Data pattern: the analytics function loads the full working set into memory via paginated ZCQL (300 rows/page), serves all analytical endpoints from that in-memory representation (zero per-request DB reads), and reloads in the background on version bump / count change / explicit `/refresh` — never blocking a request.

**All analytics anchor to the data horizon** (`MAX(IncidentFromDate)`), not the wall clock, so future-dated synthetic data behaves correctly.

## Repository structure

```
ksp-catalyst/
├─ catalyst.json               # project config — targets MUST list all functions
├─ functions/
│  ├─ analytics/
│  │  ├─ index.js              # entire analytics API + ML (single file, no build step)
│  │  ├─ package.json          # express + zcatalyst-sdk-node
│  │  └─ catalyst-config.json
│  ├─ data-events/
│  │  ├─ index.js              # Signals target: version bump
│  │  ├─ package.json
│  │  └─ catalyst-config.json  # type: "event"
│  ├─ crime-agent/ …           # Python agent (companion track)
│  ├─ seeder/ …                # Python cron
│  └─ er-nightly/ …            # Python cron
└─ client/
   ├─ index.html
   ├─ analytics.html
   └─ karnataka-districts.geojson
```

## Prerequisites

* Node.js ≥ 18 and npm
* Zoho Catalyst CLI: `npm i -g zcatalyst-cli` (verify: `catalyst --version`)
* A Catalyst account with a project created (this build uses the **EU** DC)
* Data Store tables created with the schema above (column names are referenced verbatim in code: `CaseMasterID`, `CrimeNo`, `PoliceStation`, `District`, `CaseCategory`, `Gravity`, `CrimeMajorHead`, `CrimeMinorHead`, `CaseStatus`, `Court`, `IncidentFromDate`, `CrimeRegisteredDate`, `latitude`, `longitude`, `BriefFacts`, `FinalReportType`; link table: `ResolvedPersonID`, `CaseMasterID`, `RoleTable`, `RoleRowID`, `MatchConfidence`)

## Setup

```bash
git clone https://github.com/kavyachowdary-25/ksp-crime-agent​



​> ksp-catalyst
cd ksp-catalyst

catalyst login                 # authenticate the CLI
# associate with your Catalyst project if not already: catalyst init / catalyst project:use

# function dependencies
cd functions/analytics \\\&\\\& npm install \\\&\\\& cd ../..
cd functions/data-events \\\&\\\& npm install \\\&\\\& cd ../..
```

Confirm `catalyst.json` lists **every** function target (`analytics`, `crime-agent`, `data-events`, `seeder`, `er-nightly`) and the client. A missing target deploys silently without that function — the most common failure mode in this project's history.

Seed data: run the `seeder` cron once (or load your own CSVs into the Data Store). The analytics layer makes no assumptions beyond the schema; counts, districts, and heads are all discovered at load time.

## Deployment

```bash
# backend
catalyst deploy --only functions
# read the output: it must list DEPLOYMENT SUCCESSFUL for EVERY function, including analytics

# frontend (analytics.html + index.html + karnataka-districts.geojson)
catalyst deploy --only client
```

Golden verification rule (learned the hard way): after any functions deploy, **curl a field that only the new code returns** before trusting the browser:

```bash
BASE="https://project-rainfall-20116559418.development.catalystserverless.eu/app/analytics.html>/server/analytics"
curl -s "$BASE/health"                       # row count + cache age
curl -s "$BASE/heatmap?monthly=1" | head -c 60   # {"total":...,"monthly":true → new code live
```

URLs (development environment):

|Surface|Path|
|-|-|
|Dashboard|'<https://project-rainfall-20116559418.development.catalystserverless.eu/app/analytics.html`|
|Agent console|`https://project-rainfall-20116559418.development.catalystserverless.eu/app/index.html`|
|Analytics API|`https://project-rainfall-20116559418.development.catalystserverless.eu/server/analytics/\\\*`|

## One-time data operations

**Victim backfill** — generates victim identities and `RoleTable='Victim'` links (all Crimes Against Women/Body cases, \~40% Property, \~25% others, \~6% repeat victimization). Paced and resumable against Catalyst rate limits; call in a loop until `done:true`:

```bash
while :; do
  R=$(curl -s "$BASE/admin/seed-victims?confirm=yes"); echo "$R"
  echo "$R" | grep -q '"done":true' \\\&\\\& break; sleep 3
done
```

**Dedupe** (safety net for interrupted backfills — keeps lowest ROWID per person ID):

```bash
curl -s "$BASE/admin/dedupe-victims?confirm=yes"    # loop the same way if needed
```

## Event-driven freshness (Signals)

One-time console configuration (Catalyst console → **Signals**):

1. **Publishers → Create Publisher** → CloudScale **Data Store** (predefined events); name e.g. `datastore-pub`
2. **Rules → Add Rule** → event **Row Insert** → tables `CaseFlat`, `PersonCaseLink`, `ResolvedPerson` → target: **Function → data-events**, dispatch **Instant**
3. Add sibling rules for **Row Update** and **Row Delete**
4. **Exclude `Auditlog`** — the agent writes an audit row per chat query; including it would loop cache reloads and model retraining

Verify the chain: `INSERT` a row via ZCQL console (do **not** call `/refresh`), then watch `curl $BASE/health` — the row count ticks within \~10 s (Signals path) or \~60 s (count-check fallback). Signals → Logs shows each rule firing.

Without any Signals configuration the platform still works: the 60-second count-check fallback covers freshness end-to-end.

## Running \& verifying

Open the dashboard, then walk this checklist:

1. KPI strip and `DATA HORIZON … RECORDS · live` stamp populate; stamp re-checks every 90 s and immediately on tab focus
2. Click a district polygon → every panel drills; click again to clear
3. Press ▶ on the time scrubber → 6-year playback across heatmap + choropleth
4. Filters (district / head / MO / station / dates / Heinous) compose everywhere; the URL updates — paste it into a new tab to restore the exact view
5. Click any CrimeNo → case drawer with BriefFacts and linked persons (roles tagged); *Show on map* flies to the incident
6. Case Triage: switch the model dropdown LR ↔ GBT → metrics strip and ranking change
7. What-If Explorer: drag the hour slider past 22:00 → "night incident" bar appears, probability moves
8. ಕನ್ನಡ toggle → full UI + district/head names switch; filters still work (English values preserved)
9. *Intelligence brief* → print preview renders the formal report for the current scope
10. Insert a test row via ZCQL → dashboard reflects it within \~90 s hands-off

## API reference

All endpoints `GET`, all return JSON, all accept the common filters
`district`, `majorHead`, `minorHead`, `station`, `gravity`, `from`, `to` unless noted.

|Endpoint|Purpose|
|-|-|
|`/summary`|totals, horizon, districts, major/minor heads (with parent), statuses|
|`/by-district`, `/by-station`|drill-down aggregates|
|`/timeseries`|monthly counts|
|`/heatmap`|incident points; `?monthly=1` → `\\\[lat,lng,monthKey,district]` for the scrubber; `hourFrom/hourTo` for the night layer|
|`/hotspots`|station × time-bucket cells, z-scored rates|
|`/spikes`|district × head spikes vs trailing baselines (z ≥ 2, min 5)|
|`/anomalies`|rarity-scored incidents with reasons|
|`/risk`|station risk scores + Holt forecasts|
|`/typology`|per-head Holt forecasts, growth ranking, `fastestGrowing`|
|`/offenders`|repeat/cross-jurisdiction profiles with minor-head MO|
|`/mo-links`|open cases matching repeat offenders' dominant MO|
|`/network?name=`|Cytoscape elements: persons, stations, victims + edges; recurring locations|
|`/socio`|Census overlay + correlations|
|`/funnel`|outcome funnel + conviction/chargesheet/pendency rates, by district/head|
|`/case?crimeNo=` (or `?id=`)|full case detail incl. on-demand BriefFacts + linked persons with roles|
|`/ml?model=lr\|gbt`|model card with holdout metrics|
|`/ml/compare`|both model cards|
|`/ml/model`|linear-model internals for the client-side explorer|
|`/ml/triage?model=\\\&limit=`|ranked open cases with per-case drivers|
|`/admin/seed-victims?confirm=yes`|resumable victim backfill (see above)|
|`/admin/dedupe-victims?confirm=yes`|duplicate-identity cleanup|
|`/refresh`|clear caches; next request reloads from the Data Store|
|`/health`|row count, cache age, uptime|

## Machine learning

Two models trained **inside the analytics function at runtime**, on the identical stratified 80/20 split (seeded, reproducible) over resolved cases; target = P(case ends "Closed - Undetected"); open cases are scored for triage.

||Champion: logistic regression|Challenger: gradient-boosted trees|
|-|-|-|
|Training|500-epoch full-batch gradient descent, L2|60 trees, depth 2, η 0.15, XGBoost-style regularized Newton leaves, gain splits (implemented natively — no production XGBoost exists for Node)|
|Explanations|coefficient contributions (w·x)|per-feature occlusion deltas|
|Powers|What-If Explorer, driver chips|driver chips, comparison|

23 features including **target-encoded station/head historical rates computed on the training split only** (no leakage), an **entity-graph feature** (case linked to a repeat offender via the resolution tables — accused links only), night/heinous flags, quarter seasonality. Models retrain automatically on any data refresh.

Honesty note: on this synthetic dataset both models report holdout AUC ≈ 0.51 because case outcomes were generated independently of features — two model families agreeing there is no signal. The pipeline, split, and evaluation are production-real; on data with genuine outcome patterns (e.g., CCTNS), the same harness measures which model earns deployment.

## Localization

The dashboard UI is bilingual (English / ಕನ್ನಡ) via the toggle in the controls row, persisted as `l=kn` in the URL. Panel titles, labels, KPI captions, legends, funnel stages, patrol windows, drawer fields, and ML driver names translate; **district, crime-head, and gravity display names** render in Kannada while underlying values (and therefore all API calls, filters, and shared URLs) remain English. Station names, source-data text (BriefFacts), backend-generated anomaly reasons, and the Intelligence Brief intentionally stay English — official records remain in the system of record. The agent console is independently bilingual end-to-end.

## Benchmarking

`benchmark.sh <base-url>` measures cold start (full data load), first-`/ml` call (model training time), and warm p50/p95/max across every endpoint; results populate `performance-report.md`. Warm requests are pure in-memory computation.

## Design notes \& honest limits

* **Development-tier scale**: the working set lives in function memory — right for \~10× current volume; production CCTNS scale would push aggregation into the query layer behind the same API contract (no client changes)
* **Catalyst rate limits**: bulk Data Store writes hit component concurrency caps — hence the paced, resumable admin endpoints; treat them as the template for any future bulk writer
* **Spike alerts are district-level by design** — station-level 90-day windows are too sparse for stable 2σ baselines at this volume
* **Deploy hygiene**: keep exactly one working checkout; after every functions deploy, curl a new-code-only field before debugging anything in the browser; a stale browser tab is not a broken pipeline (`/health` is ground truth)
* **Predictive-policing caution**: risk and triage scores lean on station history, which can encode reporting practice as much as crime — the platform surfaces every driver on screen precisely so a reviewing officer can question that provenance rather than inherit it blindly

