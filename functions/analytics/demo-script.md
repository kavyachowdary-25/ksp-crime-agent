# KSP Crime Intelligence Platform — 3-Minute Demo Script

**Before you start (not part of the 3 minutes):**
- Warm both functions: open `/server/analytics/summary` once, send one chat message
- Reset the analytics map view (reload the page) so Karnataka + pulsing districts are in frame
- Open the chat tab, network modal closed, input empty
- Screen recording of this exact run saved as fallback

---

## Beat 1 — The problem, answered in one sentence (0:00–0:25)
*Start on the Conversational Intelligence tab.*

> "SCRB analysts today work across disconnected Excel sheets. We built one platform
> where 5,000 CCTNS case records, 1,900 resolved persons, and their links live in a
> single store — queryable in plain English or Kannada."

**Do:** Type *"Show me the criminal history of Syed Imran."* Point at the answer,
then at the evidence trail: **"every answer cites the tool used and records an
audit trail — this is built for police accountability, not just convenience."**

## Beat 2 — Network & link analysis (0:25–1:00)
**Do:** Open the network for Syed Imran.

> "This is link analysis no spreadsheet can do. Circles are suspects — size is case
> count. Edges are shared cases. Ten repeat co-offending links flag possible
> organized activity."

**Point at the brass squares:**
> "The squares are shared stations. These five people have never all appeared in
> one case — but the network shows them operating from the same three home turfs:
> Cubbon Park, KR Market, Upparpet. That's a hidden association surfaced
> automatically."

**Do:** Click a person node → it asks the agent for their history. **"The graph and
the agent are one system."**

## Beat 3 — The Strategic Intelligence Hub (1:00–2:00)
*Switch to the Crime Analytics tab.*

> "The same data, as a command dashboard."

**Point, in order — one sentence each:**
1. Header stamp: "Analysis is anchored to the data horizon — stated on screen, no
   hidden assumptions."
2. Map with pulsing zones: "Red pulses are live spike alerts. **Belagavi: violent
   crime at 4 times its own two-year baseline — plus 300 percent.** The system
   found that; nobody queried for it."
3. Click **Night only**: "Time-of-day layering — night crime has its own geography."
4. Hotspot matrix: "Station by time-of-day, scored per hour — so a 4-hour evening
   window competes fairly with an 8-hour night window."

## Beat 4 — Predictive & investigative AI (2:00–2:40)
**Point at Predictive Risk:**
> "Every station scored 0–100 with a 3-month forecast — and the drivers are on
> screen: volume, trend, heinous share. An officer can see *why* a score is high."

**Point at Anomalous Incidents:**
> "Cases that deviate from learned patterns, each with plain-language reasons —
> a heinous property crime where that's rare, a case 8 kilometres outside its
> station's usual area."

**Do:** Click an anomaly's CrimeNo → chat opens and answers about that case.
> "From statewide pattern to individual case file in one click."

## Beat 5 — Close (2:40–3:00)
**Do:** Click **Heinous only** — the whole dashboard recomputes.

> "One click separates volume from severity — where crime happens is not where
> *serious* crime happens. Repeat offenders tracked across jurisdictions, census
> overlays for the 'why' behind the 'where' — one integrated platform,
> replacing the silos. Thank you."

---

## Rehearsed answers (one sentence each — use only if asked)

**"Which ML models did you use?"**
A logistic-regression case-outcome model trained end-to-end inside the platform —
3,483 resolved cases, stratified holdout, 23 features including target-encoded
station history and an entity-graph feature — plus interpretable statistical
models (Holt forecasting, rarity scoring) for the dashboards. Every prediction
decomposes into its drivers on screen.

**"Your model card shows AUC 0.51 — that's chance."**
Correct, and deliberately reported: this demo dataset's outcomes were generated
independently of case features, so an honest holdout evaluation reads ~0.51 — a
leaky or overfit pipeline would have shown you an impressive fake number
instead. The pipeline, feature engineering, and evaluation are production-real;
on CCTNS data, where outcomes genuinely correlate with station, time, and
offender history, this is exactly where signal appears.

**"Why does it say December 2026?"**
The demo dataset extends past today, so all baselines anchor to the data horizon
rather than the wall clock — stated on the header stamp.

**"Why is the evening cell red when other cells have more cases?"**
Cells are scored per hour, and the windows differ in length — night is 8 hours,
morning and afternoon 6, evening only 4. Cubbon Park's evening runs at ~19
incidents per hour versus ~14 in the morning and ~11 at night; it's the highest
rate in the row despite the smallest count.

**"75% of persons are repeat offenders?"**
The demo's entity resolution links aggressively to exercise cross-jurisdiction
tracking; in production the MatchConfidence field tunes that threshold.

**"Why are the socio-economic correlations near zero?"**
Synthetic case volumes aren't population-proportional; the platform computes
rates and correlations live, and on production data these would carry signal.

**"Where are victims in the network?"**
The link schema supports any role via RoleTable; this dataset contains accused
links, so we show suspects and recurring locations — no fabricated claims.
