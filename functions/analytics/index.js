const express = require("express");
const catalyst = require("zcatalyst-sdk-node");

const app = express();
app.use(express.json());

const PAGE_SIZE = 300;
const TABLE = "CaseFlat";
const COLUMNS = [
  "ROWID",
  "CaseMasterID",
  "CrimeNo",
  "PoliceStation",
  "District",
  "CaseCategory",
  "Gravity",
  "CrimeMajorHead",
  "CaseStatus",
  "FinalReportType",
  "IncidentFromDate",
  "latitude",
  "longitude"
];

// ---------------------------------------------------------------------------
// Data loading + cache
// Data is static for the hackathon: load once per instance, keep in memory.
// ---------------------------------------------------------------------------

let cache = null; // { rows, horizon, loadedAt }
let loadPromise = null;

function parseDate(s) {
  // "YYYY-MM-DD HH:mm:ss" — parse by parts, no timezone surprises
  if (!s) return null;
  const y = +s.slice(0, 4);
  const mo = +s.slice(5, 7);
  const d = +s.slice(8, 10);
  const h = +s.slice(11, 13) || 0;
  return { y, mo, d, h, ts: Date.UTC(y, mo - 1, d, h), monthKey: s.slice(0, 7), dateKey: s.slice(0, 10) };
}

async function fetchAllRows(capp) {
  const zcql = capp.zcql();
  const rows = [];
  let offset = 0;
  for (;;) {
    const q = `SELECT ${COLUMNS.join(", ")} FROM ${TABLE} LIMIT ${offset}, ${PAGE_SIZE}`;
    const res = await zcql.executeZCQLQuery(q);
    if (!res || res.length === 0) break;
    for (const r of res) {
      const rec = r[TABLE];
      const t = parseDate(rec.IncidentFromDate);
      if (!t) continue;
      rows.push({
        id: rec.ROWID,
        caseId: rec.CaseMasterID,
        crimeNo: rec.CrimeNo,
        station: rec.PoliceStation,
        district: rec.District,
        category: rec.CaseCategory,
        gravity: rec.Gravity,
        majorHead: rec.CrimeMajorHead,
        status: rec.CaseStatus,
        finalReport: rec.FinalReportType,
        lat: rec.latitude != null ? +rec.latitude : null,
        lng: rec.longitude != null ? +rec.longitude : null,
        t
      });
    }
    if (res.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return rows;
}

async function getData(req) {
  if (cache) return cache;
  if (!loadPromise) {
    const capp = catalyst.initialize(req);
    loadPromise = fetchAllRows(capp)
      .then((rows) => {
        // Anchor "now" to the data horizon, not the wall clock:
        // the synthetic dataset contains future-dated rows.
        let maxTs = 0;
        let minTs = Infinity;
        for (const r of rows) {
          if (r.t.ts > maxTs) maxTs = r.t.ts;
          if (r.t.ts < minTs) minTs = r.t.ts;
        }
        cache = { rows, horizon: { minTs, maxTs }, loadedAt: Date.now() };
        loadPromise = null;
        return cache;
      })
      .catch((err) => {
        loadPromise = null;
        throw err;
      });
  }
  return loadPromise;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DAY = 24 * 60 * 60 * 1000;

function isoDate(ts) {
  return new Date(ts).toISOString().slice(0, 10);
}

function applyFilters(rows, query) {
  const { district, station, category, majorHead, gravity, from, to } = query;
  const fromTs = from ? Date.UTC(+from.slice(0, 4), +from.slice(5, 7) - 1, +from.slice(8, 10)) : null;
  const toTs = to ? Date.UTC(+to.slice(0, 4), +to.slice(5, 7) - 1, +to.slice(8, 10)) + DAY : null;
  return rows.filter((r) => {
    if (district && r.district !== district) return false;
    if (station && r.station !== station) return false;
    if (category && r.category !== category) return false;
    if (majorHead && r.majorHead !== majorHead) return false;
    if (gravity && r.gravity !== gravity) return false;
    if (fromTs !== null && r.t.ts < fromTs) return false;
    if (toTs !== null && r.t.ts >= toTs) return false;
    return true;
  });
}

function countBy(rows, keyFn) {
  const m = new Map();
  for (const r of rows) {
    const k = keyFn(r);
    if (k == null) continue;
    m.set(k, (m.get(k) || 0) + 1);
  }
  return m;
}

function mapToSortedArray(m, keyName) {
  return [...m.entries()]
    .map(([k, count]) => ({ [keyName]: k, count }))
    .sort((a, b) => b.count - a.count);
}

function meanSd(values) {
  const n = values.length;
  if (n === 0) return { mean: 0, sd: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
  return { mean, sd: Math.sqrt(variance) };
}

function asyncRoute(fn) {
  return (req, res) => {
    fn(req, res).catch((err) => {
      console.error(err);
      res.status(500).json({ error: err.message || "internal error" });
    });
  };
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

// GET /summary
app.get("/summary", asyncRoute(async (req, res) => {
  const { rows, horizon, loadedAt } = await getData(req);
  res.json({
    totalCases: rows.length,
    horizon: { from: isoDate(horizon.minTs), to: isoDate(horizon.maxTs) },
    districts: mapToSortedArray(countBy(rows, (r) => r.district), "district"),
    categories: mapToSortedArray(countBy(rows, (r) => r.category), "category"),
    gravity: mapToSortedArray(countBy(rows, (r) => r.gravity), "gravity"),
    status: mapToSortedArray(countBy(rows, (r) => r.status), "status"),
    majorHeads: mapToSortedArray(countBy(rows, (r) => r.majorHead), "majorHead"),
    cache: { loadedAt: new Date(loadedAt).toISOString() }
  });
}));

// GET /by-district?category=&gravity=&from=&to=
app.get("/by-district", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  const filtered = applyFilters(rows, req.query);
  const byDistrict = countBy(filtered, (r) => r.district);
  const out = [...byDistrict.entries()].map(([district, count]) => {
    const dRows = filtered.filter((r) => r.district === district);
    return {
      district,
      count,
      topCategories: mapToSortedArray(countBy(dRows, (r) => r.category), "category").slice(0, 5),
      topMajorHeads: mapToSortedArray(countBy(dRows, (r) => r.majorHead), "majorHead").slice(0, 10),
      stations: mapToSortedArray(countBy(dRows, (r) => r.station), "station")
    };
  }).sort((a, b) => b.count - a.count);
  res.json({ total: filtered.length, districts: out });
}));

// GET /by-station?district=&category=&from=&to=
app.get("/by-station", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  const filtered = applyFilters(rows, req.query);
  const byStation = countBy(filtered, (r) => r.station);
  const out = [...byStation.entries()].map(([station, count]) => {
    const sRows = filtered.filter((r) => r.station === station);
    const centroidRows = sRows.filter((r) => r.lat != null && r.lng != null);
    const lat = centroidRows.reduce((a, r) => a + r.lat, 0) / (centroidRows.length || 1);
    const lng = centroidRows.reduce((a, r) => a + r.lng, 0) / (centroidRows.length || 1);
    return {
      station,
      district: sRows[0] ? sRows[0].district : null,
      count,
      centroid: centroidRows.length ? { lat, lng } : null,
      topCategories: mapToSortedArray(countBy(sRows, (r) => r.category), "category").slice(0, 5),
      topMajorHeads: mapToSortedArray(countBy(sRows, (r) => r.majorHead), "majorHead").slice(0, 5)
    };
  }).sort((a, b) => b.count - a.count);
  res.json({ total: filtered.length, stations: out });
}));

// GET /timeseries?granularity=month|week|day&district=&station=&category=&from=&to=
app.get("/timeseries", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  const filtered = applyFilters(rows, req.query);
  const granularity = req.query.granularity || "month";

  let keyFn;
  if (granularity === "day") {
    keyFn = (r) => r.t.dateKey;
  } else if (granularity === "week") {
    keyFn = (r) => isoDate(r.t.ts - ((Math.floor(r.t.ts / DAY) + 3) % 7) * DAY); // Monday of that week
  } else {
    keyFn = (r) => r.t.monthKey;
  }

  const series = [...countBy(filtered, keyFn).entries()]
    .map(([period, count]) => ({ period, count }))
    .sort((a, b) => (a.period < b.period ? -1 : 1));

  res.json({ granularity, total: filtered.length, series });
}));

// GET /heatmap?district=&category=&from=&to=&hourFrom=&hourTo=
app.get("/heatmap", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  let filtered = applyFilters(rows, req.query);
  const hf = req.query.hourFrom != null ? +req.query.hourFrom : null;
  const ht = req.query.hourTo != null ? +req.query.hourTo : null;
  if (hf != null && ht != null) {
    filtered = filtered.filter((r) =>
      hf <= ht ? r.t.h >= hf && r.t.h <= ht : r.t.h >= hf || r.t.h <= ht // wraps midnight
    );
  }
  const points = filtered
    .filter((r) => r.lat != null && r.lng != null)
    .map((r) => [r.lat, r.lng, 1]);
  res.json({ total: points.length, points });
}));

// GET /hotspots
// Station x time-of-day cells scored by z-score of incidents-per-hour rate.
const HOUR_BUCKETS = [
  { name: "night", hours: [22, 23, 0, 1, 2, 3, 4, 5] },
  { name: "morning", hours: [6, 7, 8, 9, 10, 11] },
  { name: "afternoon", hours: [12, 13, 14, 15, 16, 17] },
  { name: "evening", hours: [18, 19, 20, 21] }
];

app.get("/hotspots", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  const filtered = applyFilters(rows, req.query);

  const bucketOf = new Map();
  for (const b of HOUR_BUCKETS) for (const h of b.hours) bucketOf.set(h, b.name);
  const bucketHours = Object.fromEntries(HOUR_BUCKETS.map((b) => [b.name, b.hours.length]));

  const cellCounts = new Map(); // "station||bucket" -> count
  const stationMeta = new Map(); // station -> { district, latSum, lngSum, n }
  for (const r of filtered) {
    const bucket = bucketOf.get(r.t.h);
    const key = `${r.station}||${bucket}`;
    cellCounts.set(key, (cellCounts.get(key) || 0) + 1);
    let meta = stationMeta.get(r.station);
    if (!meta) {
      meta = { district: r.district, latSum: 0, lngSum: 0, n: 0 };
      stationMeta.set(r.station, meta);
    }
    if (r.lat != null && r.lng != null) {
      meta.latSum += r.lat;
      meta.lngSum += r.lng;
      meta.n += 1;
    }
  }

  const cells = [...cellCounts.entries()].map(([key, count]) => {
    const [station, bucket] = key.split("||");
    return { station, bucket, count, rate: count / bucketHours[bucket] };
  });

  const { mean, sd } = meanSd(cells.map((c) => c.rate));
  const meta = (s) => stationMeta.get(s);
  const out = cells
    .map((c) => {
      const m = meta(c.station);
      return {
        station: c.station,
        district: m.district,
        bucket: c.bucket,
        count: c.count,
        zScore: sd > 0 ? +((c.rate - mean) / sd).toFixed(2) : 0,
        centroid: m.n ? { lat: m.latSum / m.n, lng: m.lngSum / m.n } : null
      };
    })
    .sort((a, b) => b.zScore - a.zScore);

  res.json({
    method: "z-score of incidents-per-hour across station x time-of-day cells",
    hotspots: out.filter((c) => c.zScore >= 1.5),
    cells: out
  });
}));

// GET /spikes?windowDays=90
// Compares the most recent window (anchored to the data horizon, not the wall
// clock) against the trailing baseline windows, per district x category.
app.get("/spikes", asyncRoute(async (req, res) => {
  const { rows, horizon } = await getData(req);
  const windowDays = Math.max(14, Math.min(365, +(req.query.windowDays || 90)));
  const windowMs = windowDays * DAY;
  const anchor = horizon.maxTs + 1;

  const numBaselineWindows = Math.min(
    12,
    Math.floor((anchor - horizon.minTs) / windowMs) - 1
  );
  if (numBaselineWindows < 3) {
    return res.json({ error: "not enough history for this window size", spikes: [] });
  }

  // windowIndex 0 = current, 1..N = baseline windows going back in time
  const seriesByKey = new Map(); // "district||category" -> number[] length N+1
  for (const r of rows) {
    const age = anchor - r.t.ts;
    const idx = Math.floor(age / windowMs);
    if (idx > numBaselineWindows) continue;
    const key = `${r.district}||${r.majorHead}`;
    let arr = seriesByKey.get(key);
    if (!arr) {
      arr = new Array(numBaselineWindows + 1).fill(0);
      seriesByKey.set(key, arr);
    }
    arr[idx] += 1;
  }

  const spikes = [];
  const all = [];
  for (const [key, arr] of seriesByKey.entries()) {
    const [district, majorHead] = key.split("||");
    const current = arr[0];
    const baseline = arr.slice(1);
    const { mean, sd } = meanSd(baseline);
    const z = sd > 0 ? (current - mean) / sd : current > mean ? 3 : 0;
    const entry = {
      district,
      majorHead,
      currentWindow: current,
      baselineMean: +mean.toFixed(1),
      baselineSd: +sd.toFixed(1),
      zScore: +z.toFixed(2),
      pctChange: mean > 0 ? +(((current - mean) / mean) * 100).toFixed(0) : null
    };
    all.push(entry);
    if (z >= 2 && current >= 5) spikes.push(entry);
  }

  spikes.sort((a, b) => b.zScore - a.zScore);
  res.json({
    windowDays,
    anchor: isoDate(horizon.maxTs),
    baselineWindows: numBaselineWindows,
    method: "current window vs mean+2sd of trailing baseline windows, per district x crime major head",
    spikes,
    all: all.sort((a, b) => b.zScore - a.zScore)
  });
}));

// GET /anomalies?limit=15
// Rarity scoring: how improbable is this case's combination of crime head,
// hour, gravity for its station, plus spatial deviation from the station's
// usual area. Every flagged case carries human-readable reasons.
const EARTH_KM = 6371;
function haversineKm(a, b, c, d) {
  const rad = Math.PI / 180;
  const dLat = (c - a) * rad, dLng = (d - b) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a * rad) * Math.cos(c * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.sqrt(h));
}

app.get("/anomalies", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  const filtered = applyFilters(rows, req.query);
  const limit = Math.min(50, +(req.query.limit || 15));

  const bucketOf = new Map();
  for (const b of HOUR_BUCKETS) for (const h of b.hours) bucketOf.set(h, b.name);
  const inc = (m, k) => m.set(k, (m.get(k) || 0) + 1);

  const nStation = new Map(), nStationMH = new Map(), nMH = new Map(),
        nMHHour = new Map(), nMHGrav = new Map();
  const geo = new Map(); // station -> { latSum, lngSum, n, pts: [] }
  for (const r of rows) {
    inc(nStation, r.station);
    inc(nStationMH, r.station + "||" + r.majorHead);
    inc(nMH, r.majorHead);
    inc(nMHHour, r.majorHead + "||" + bucketOf.get(r.t.h));
    inc(nMHGrav, r.majorHead + "||" + r.gravity);
    if (r.lat != null && r.lng != null) {
      let g = geo.get(r.station);
      if (!g) { g = { latSum: 0, lngSum: 0, n: 0 }; geo.set(r.station, g); }
      g.latSum += r.lat; g.lngSum += r.lng; g.n += 1;
    }
  }
  const distStats = new Map(); // station -> { mean, sd }
  for (const [station, g] of geo.entries()) {
    const cLat = g.latSum / g.n, cLng = g.lngSum / g.n;
    g.cLat = cLat; g.cLng = cLng;
    const ds = rows.filter((r) => r.station === station && r.lat != null)
      .map((r) => haversineKm(cLat, cLng, r.lat, r.lng));
    distStats.set(station, meanSd(ds));
  }

  const scored = filtered.map((r) => {
    const bucket = bucketOf.get(r.t.h);
    const pMH = (nStationMH.get(r.station + "||" + r.majorHead) || 1) / nStation.get(r.station);
    const pHour = (nMHHour.get(r.majorHead + "||" + bucket) || 1) / nMH.get(r.majorHead);
    const pGrav = (nMHGrav.get(r.majorHead + "||" + r.gravity) || 1) / nMH.get(r.majorHead);
    let score = -Math.log2(pMH) - Math.log2(pHour) - Math.log2(pGrav);

    const reasons = [];
    if (pMH < 0.05) reasons.push(`${r.majorHead} is rare at ${r.station} (${(pMH * 100).toFixed(1)}% of its cases)`);
    if (pHour < 0.12) reasons.push(`unusual time of day (${bucket}) for ${r.majorHead}`);
    if (pGrav < 0.12) reasons.push(`${r.gravity} gravity is atypical for ${r.majorHead}`);

    const g = geo.get(r.station);
    const ds = distStats.get(r.station);
    if (g && ds && ds.sd > 0 && r.lat != null) {
      const km = haversineKm(g.cLat, g.cLng, r.lat, r.lng);
      const zd = (km - ds.mean) / ds.sd;
      if (zd > 2.5) {
        score += zd;
        reasons.push(`location ${km.toFixed(1)} km outside the station's usual area`);
      }
    }
    return { r, score, reasons };
  });

  scored.sort((a, b) => b.score - a.score);
  res.json({
    method: "rarity scoring (-log2 P of crime-head/hour/gravity combination for the station) + spatial deviation",
    anomalies: (() => {
      const seen = new Map(), out = [];
      for (const s of scored) {
        if (!s.reasons.length) continue;
        const k = s.r.station + "||" + s.r.majorHead;
        const c = seen.get(k) || 0;
        if (c >= 2) continue;
        seen.set(k, c + 1);
        out.push(s);
        if (out.length >= limit) break;
      }
      return out;
    })().map(({ r, score, reasons }) => ({
      crimeNo: r.crimeNo,
      caseId: r.caseId,
      station: r.station,
      district: r.district,
      majorHead: r.majorHead,
      gravity: r.gravity,
      incidentAt: r.t.dateKey + " " + String(r.t.h).padStart(2, "0") + ":00",
      lat: r.lat, lng: r.lng,
      score: +score.toFixed(1),
      reasons
    }))
  });
}));

// GET /risk
// Per-station 3-month forecast via Holt's linear exponential smoothing on
// monthly counts, composed with volume percentile, trend, heinous share and
// spike pressure into a transparent 0-100 risk score.
function holtForecast(series, periods) {
  if (series.length < 4) return { forecast: [], trend: 0, level: series[series.length - 1] || 0 };
  const alpha = 0.5, beta = 0.3;
  let level = series[0];
  let trend = (series[series.length - 1] - series[0]) / (series.length - 1);
  for (let i = 1; i < series.length; i++) {
    const prevLevel = level;
    level = alpha * series[i] + (1 - alpha) * (level + trend);
    trend = beta * (level - prevLevel) + (1 - beta) * trend;
  }
  const forecast = [];
  for (let k = 1; k <= periods; k++) forecast.push(Math.max(0, Math.round(level + k * trend)));
  return { forecast, trend, level };
}

app.get("/risk", asyncRoute(async (req, res) => {
  const { rows, horizon } = await getData(req);
  const filtered = applyFilters(rows, req.query);

  // last 24 full months up to the data horizon
  const end = new Date(horizon.maxTs);
  const months = [];
  for (let i = 23; i >= 0; i--) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - i, 1));
    months.push(d.toISOString().slice(0, 7));
  }
  const monthIdx = new Map(months.map((m, i) => [m, i]));

  const perStation = new Map(); // station -> { district, series[24], heinousRecent, totalRecent }
  for (const r of filtered) {
    const idx = monthIdx.get(r.t.monthKey);
    if (idx == null) continue;
    let s = perStation.get(r.station);
    if (!s) {
      s = { district: r.district, series: new Array(24).fill(0), heinousRecent: 0, totalRecent: 0 };
      perStation.set(r.station, s);
    }
    s.series[idx] += 1;
    if (idx >= 18) { // last 6 months
      s.totalRecent += 1;
      if (r.gravity === "Heinous") s.heinousRecent += 1;
    }
  }

  const overallHeinous =
    filtered.filter((r) => r.gravity === "Heinous").length / (filtered.length || 1);

  const prelim = [...perStation.entries()].map(([station, s]) => {
    const { forecast, trend, level } = holtForecast(s.series, 3);
    const recentMean = s.series.slice(18).reduce((a, b) => a + b, 0) / 6;
    const heinousShare = s.totalRecent ? s.heinousRecent / s.totalRecent : 0;
    return { station, district: s.district, series: s.series, forecast,
      recentMean, trendPerMonth: +trend.toFixed(2), level, heinousShare };
  });

  const maxRecent = Math.max(...prelim.map((p) => p.recentMean), 1);
  const out = prelim.map((p) => {
    const volume = p.recentMean / maxRecent; // 0..1
    const trendNorm = Math.max(0, Math.min(1, p.level > 0 ? (p.trendPerMonth / p.level) * 6 + 0.5 : 0.5));
    const heinousNorm = Math.max(0, Math.min(1, overallHeinous > 0 ? (p.heinousShare / overallHeinous) / 2 : 0));
    const score = Math.round(100 * (0.45 * volume + 0.35 * trendNorm + 0.2 * heinousNorm));
    return {
      station: p.station,
      district: p.district,
      riskScore: score,
      forecastNext3Months: p.forecast,
      recentMonthlyMean: +p.recentMean.toFixed(1),
      trendPerMonth: p.trendPerMonth,
      heinousShareRecent: +(p.heinousShare * 100).toFixed(1),
      last12Months: p.series.slice(12),
      drivers: {
        volume: +volume.toFixed(2),
        trend: +trendNorm.toFixed(2),
        heinous: +heinousNorm.toFixed(2)
      }
    };
  }).sort((a, b) => b.riskScore - a.riskScore);

  res.json({
    anchor: isoDate(horizon.maxTs),
    method: "Holt linear forecast on 24 monthly counts; score = 45% volume percentile + 35% trend + 20% heinous share",
    weights: { volume: 0.45, trend: 0.35, heinous: 0.2 },
    stations: out
  });
}));

// ---------------------------------------------------------------------------
// Repeat offenders
// OFFENDER_SCHEMA: verify these column names with
//   SELECT * FROM PersonCaseLink LIMIT 1  and  SELECT * FROM ResolvedPerson LIMIT 1
// and adjust if your tables differ.
// ---------------------------------------------------------------------------
// OFFENDER_SCHEMA — verified against the live Data Store:
// PersonCaseLink: ResolvedPersonID, CaseMasterID, RoleTable, RoleRowID, MatchConfidence
// ResolvedPerson: ResolvedPersonID, CanonicalName, ApproxBirthYear, GenderID, CaseCount
const OFFENDER_SCHEMA = {
  linkTable: "PersonCaseLink",
  linkPersonCol: "ResolvedPersonID",
  linkCaseCol: "CaseMasterID",
  personTable: "ResolvedPerson",
  personIdCol: "ResolvedPersonID",
  personNameCol: "CanonicalName"
};

async function fetchTableRows(capp, table, cols) {
  const zcql = capp.zcql();
  const out = [];
  let offset = 0;
  for (;;) {
    const res = await zcql.executeZCQLQuery(
      `SELECT ${cols.join(", ")} FROM ${table} LIMIT ${offset}, ${PAGE_SIZE}`
    );
    if (!res || res.length === 0) break;
    for (const r of res) out.push(r[table]);
    if (res.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return out;
}

let peopleCache = null;
let peopleLoad = null;
async function getPeople(req) {
  if (peopleCache) return peopleCache;
  if (!peopleLoad) {
    const capp = catalyst.initialize(req);
    const S = OFFENDER_SCHEMA;
    peopleLoad = Promise.all([
      fetchTableRows(capp, S.linkTable, [S.linkPersonCol, S.linkCaseCol]),
      fetchTableRows(capp, S.personTable, [S.personIdCol, S.personNameCol])
    ]).then(([linkRows, personRows]) => {
      const names = new Map();
      for (const p of personRows) names.set(String(p[S.personIdCol]), p[S.personNameCol]);
      const links = linkRows.map((l) => ({
        personId: String(l[S.linkPersonCol]),
        caseId: String(l[S.linkCaseCol])
      }));
      peopleCache = { links, names };
      peopleLoad = null;
      return peopleCache;
    }).catch((e) => { peopleLoad = null; throw e; });
  }
  return peopleLoad;
}

// GET /offenders?limit=20&minCases=2
app.get("/offenders", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  let people;
  try {
    people = await getPeople(req);
  } catch (e) {
    return res.status(500).json({
      error: e.message,
      hint: "Check OFFENDER_SCHEMA column names in index.js against your PersonCaseLink / ResolvedPerson tables."
    });
  }
  const limit = Math.min(50, +(req.query.limit || 20));
  const minCases = Math.max(2, +(req.query.minCases || 2));

  const caseById = new Map();
  for (const r of rows) caseById.set(String(r.caseId), r);

  const byPerson = new Map();
  for (const l of people.links) {
    let set = byPerson.get(l.personId);
    if (!set) { set = new Set(); byPerson.set(l.personId, set); }
    set.add(l.caseId);
  }

  const profiles = [];
  for (const [personId, caseIds] of byPerson.entries()) {
    const cases = [...caseIds].map((id) => caseById.get(id)).filter(Boolean);
    if (cases.length < minCases) continue;
    const districts = [...new Set(cases.map((c) => c.district))];
    const stations = [...new Set(cases.map((c) => c.station))];
    const moCounts = countBy(cases, (c) => c.majorHead);
    const mo = mapToSortedArray(moCounts, "majorHead");
    cases.sort((a, b) => b.t.ts - a.t.ts);
    profiles.push({
      personId,
      name: people.names.get(personId) || "Unknown",
      caseCount: cases.length,
      districts,
      crossJurisdiction: districts.length > 1,
      dominantMO: mo[0].majorHead,
      moBreakdown: mo,
      timeline: cases.map((c) => ({
        crimeNo: c.crimeNo,
        district: c.district,
        station: c.station,
        majorHead: c.majorHead,
        gravity: c.gravity,
        status: c.status,
        date: c.t.dateKey
      }))
    });
  }
  profiles.sort((a, b) => b.caseCount - a.caseCount || b.districts.length - a.districts.length);
  res.json({
    totalRepeatOffenders: profiles.length,
    crossJurisdictionCount: profiles.filter((p) => p.crossJurisdiction).length,
    offenders: profiles.slice(0, limit)
  });
}));

// ---------------------------------------------------------------------------
// Socio-economic overlay — Census of India 2011 district figures, mapped to
// police units. Commissionerate boundaries differ from census districts, so
// rates are indicative; the caveat ships in the response.
// ---------------------------------------------------------------------------
const SOCIO = [
  { policeUnit: "Bengaluru City", censusDistrict: "Bengaluru Urban", population: 9621551, densityPerKm2: 4381, urbanPct: 90.9, literacyPct: 87.7 },
  { policeUnit: "Bengaluru Rural", censusDistrict: "Bengaluru Rural", population: 990923, densityPerKm2: 441, urbanPct: 27.1, literacyPct: 77.9 },
  { policeUnit: "Mysuru", censusDistrict: "Mysuru", population: 3001127, densityPerKm2: 476, urbanPct: 41.4, literacyPct: 72.8 },
  { policeUnit: "Mangaluru City", censusDistrict: "Dakshina Kannada", population: 2089649, densityPerKm2: 457, urbanPct: 47.7, literacyPct: 88.6 },
  { policeUnit: "Belagavi", censusDistrict: "Belagavi", population: 4779661, densityPerKm2: 356, urbanPct: 25.4, literacyPct: 73.5 },
  { policeUnit: "Hubballi-Dharwad", censusDistrict: "Dharwad", population: 1847023, densityPerKm2: 434, urbanPct: 56.8, literacyPct: 80.0 },
  { policeUnit: "Kalaburagi", censusDistrict: "Kalaburagi", population: 2566326, densityPerKm2: 236, urbanPct: 32.6, literacyPct: 64.9 },
  { policeUnit: "Shivamogga", censusDistrict: "Shivamogga", population: 1752753, densityPerKm2: 207, urbanPct: 35.5, literacyPct: 80.5 }
];

function pearson(xs, ys) {
  const n = xs.length;
  if (n < 3) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, dx = 0, dy = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i] - mx) * (ys[i] - my);
    dx += (xs[i] - mx) ** 2;
    dy += (ys[i] - my) ** 2;
  }
  return dx && dy ? +(num / Math.sqrt(dx * dy)).toFixed(2) : null;
}

// GET /socio?majorHead=
app.get("/socio", asyncRoute(async (req, res) => {
  const { rows } = await getData(req);
  const filtered = applyFilters(rows, { majorHead: req.query.majorHead, gravity: req.query.gravity });
  const byDistrict = countBy(filtered, (r) => r.district);

  const units = SOCIO.map((s) => {
    const cases = byDistrict.get(s.policeUnit) || 0;
    return { ...s, cases, casesPerLakh: +((cases / s.population) * 100000).toFixed(1) };
  });

  const withData = units.filter((u) => u.cases > 0);
  const y = withData.map((u) => u.casesPerLakh);
  res.json({
    source: "Census of India 2011 district figures",
    caveat: "Police commissionerate boundaries differ from census districts; rates are indicative.",
    correlations: {
      urbanizationVsCrimeRate: pearson(withData.map((u) => u.urbanPct), y),
      densityVsCrimeRate: pearson(withData.map((u) => u.densityPerKm2), y),
      literacyVsCrimeRate: pearson(withData.map((u) => u.literacyPct), y)
    },
    units
  });
}));

// GET /health
app.get("/health", (req, res) => {
  res.json({ ok: true, cached: !!cache, rows: cache ? cache.rows.length : 0 });
});

module.exports = app;
