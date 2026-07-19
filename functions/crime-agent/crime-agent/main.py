#!/usr/bin/env python3
"""
main.py — Catalyst AdvancedIO function: KSP crime intelligence chat endpoint.

POST body:  { "message": "...", "history": [ {role, content}, ... ], "session_id": "..." }
Response:   { "answer": "...", "citations": [...], "audit": [...] }

Env vars (in catalyst-config.json -> deployment.env_variables):
  GLM_ENDPOINT_URL    https://api.catalyst.zoho.eu/quickml/v1/project/<pid>/glm/chat
  CAT_ORG_ID          Catalyst org id
  ZOHO_CLIENT_ID      self-client id
  ZOHO_CLIENT_SECRET  self-client secret
  ZOHO_REFRESH_TOKEN  permanent refresh token

Notes:
- ZCQL LIKE is non-functional on this Data Store -> person name matching is
  done in Python over a cached ResolvedPerson table; ZCQL is used only for
  exact/numeric filters (=, IN) which are verified working.
"""

import json
import os
import re
import time
import traceback
from collections import Counter

import requests
import zcatalyst_sdk

from agent_core import run_agent


# ------------------------------------------------------------------ ZCQL helpers

def _flatten(zcql_rows, table_hint=None):
    """ZCQL returns [{'CaseFlat': {...}}, ...]; flatten to plain dicts."""
    out = []
    for r in zcql_rows or []:
        if isinstance(r, dict):
            if table_hint and table_hint in r:
                out.append(r[table_hint])
            elif len(r) == 1 and isinstance(next(iter(r.values())), dict):
                out.append(next(iter(r.values())))
            else:
                out.append(r)
    return out


# ------------------------------------------------------------------ person matching
# (Python-side because ZCQL LIKE returns nothing on unindexed text columns)

_person_cache = {"rows": None, "ts": 0}


def _all_persons(app):
    if _person_cache["rows"] and time.time() - _person_cache["ts"] < 300:
        return _person_cache["rows"]
    table = app.datastore().table("ResolvedPerson")
    rows, token = [], None
    while True:
        resp = table.get_paged_rows(next_token=token, max_rows=300)
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None) or []
        token = getattr(resp, "next_token", None) or (resp.get("next_token") if isinstance(resp, dict) else None)
        rows.extend(data)
        if not token or not data:
            break
    _person_cache["rows"] = rows
    _person_cache["ts"] = time.time()
    return rows


def _norm(s):
    s = re.sub(r"[^a-z ]", " ", (s or "").lower())
    return " ".join(t[:-1] if len(t) > 4 and t.endswith("a") and
                    not t.endswith(("appa", "amma", "anna")) else t
                    for t in s.split())


def _find_persons(app, name, limit=3):
    qn = _norm(name)
    if not qn:
        return []
    scored = []
    for r in _all_persons(app):
        cn = _norm(r.get("CanonicalName", ""))
        if not cn:
            continue
        if qn in cn or cn in qn or qn.split()[0] in cn.split():
            scored.append((int(float(r.get("CaseCount") or 0)), r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


# ------------------------------------------------------------------ hotspot data

_geo_cache = {"rows": None, "ts": 0}


def _all_geo(app):
    """Slim cached copy of CaseFlat geo columns for map rendering."""
    if _geo_cache["rows"] and time.time() - _geo_cache["ts"] < 300:
        return _geo_cache["rows"]
    table = app.datastore().table("CaseFlat")
    rows, token = [], None
    while True:
        resp = table.get_paged_rows(next_token=token, max_rows=300)
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None) or []
        token = getattr(resp, "next_token", None) or (resp.get("next_token") if isinstance(resp, dict) else None)
        for r in data:
            try:
                lat, lon = float(r.get("latitude")), float(r.get("longitude"))
            except (TypeError, ValueError):
                continue
            d = str(r.get("CrimeRegisteredDate") or "")
            bf = (r.get("BriefFacts") or "").lower()
            rows.append({"lat": lat, "lon": lon,
                         "crime": r.get("CrimeMinorHead") or "",
                         "district": r.get("District") or "",
                         "year": d[:4], "ym": d[:7],
                         "cid": str(r.get("CaseMasterID") or ""),
                         "crimeno": r.get("CrimeNo") or "",
                         "status": r.get("CaseStatus") or "",
                         "gravity": r.get("Gravity") or "",
                         "toks": frozenset(w for w in re.findall(r"[a-z]{4,}", bf)
                                           if w not in _MO_STOP) if bf else frozenset()})
        if not token or not data:
            break
    _geo_cache["rows"] = rows
    _geo_cache["ts"] = time.time()
    return rows


def build_hotspot(app, filters):
    crime = (filters.get("crime") or "").strip()
    year = str(filters.get("year") or "").strip()
    district = (filters.get("district") or "").strip()
    pts, crimes, years = [], set(), set()
    for r in _all_geo(app):
        crimes.add(r["crime"]); years.add(r["year"])
        if crime and r["crime"] != crime:
            continue
        if year and r["year"] != year:
            continue
        if district and r["district"] != district:
            continue
        pts.append([round(r["lat"], 5), round(r["lon"], 5)])
    return {"points": pts, "total": len(pts),
            "crimes": sorted(c for c in crimes if c),
            "years": sorted(y for y in years if y)}


# ------------------------------------------------------------------ trends

def build_trend(app, filters):
    crime = (filters.get("crime") or "").strip()
    months, series = set(), {}
    for r in _all_geo(app):
        if not r["ym"] or len(r["ym"]) < 7:
            continue
        months.add(r["ym"])
        if crime and r["crime"] != crime:
            continue
        series[r["ym"]] = series.get(r["ym"], 0) + 1
    labels = sorted(months)
    counts = [series.get(m, 0) for m in labels]
    # seasonal baseline: mean of the same calendar month across years
    by_cal = {}
    for m, c in zip(labels, counts):
        by_cal.setdefault(m[5:7], []).append(c)
    baseline = {cal: sum(v) / len(v) for cal, v in by_cal.items()}
    alerts = [{"month": m, "actual": c, "baseline": round(baseline[m[5:7]], 1)}
              for m, c in zip(labels, counts)
              if baseline.get(m[5:7], 0) > 3 and c > baseline[m[5:7]] * 1.25]
    fc_labels, fc = [], []
    if labels:
        y, mo = int(labels[-1][:4]), int(labels[-1][5:7])
        for _ in range(6):
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
            lab = f"{y}-{mo:02d}"
            fc_labels.append(lab)
            fc.append(round(baseline.get(f"{mo:02d}", sum(counts[-12:]) / max(len(counts[-12:]), 1)), 1))
    return {"labels": labels, "counts": counts,
            "forecast_labels": fc_labels, "forecast": fc,
            "alerts": alerts[-6:],
            "crimes": sorted({r["crime"] for r in _all_geo(app) if r["crime"]})}


# ------------------------------------------------------------------ connection path

_pcl_cache = {"rows": None, "ts": 0}


def _all_links(app):
    if _pcl_cache["rows"] and time.time() - _pcl_cache["ts"] < 600:
        return _pcl_cache["rows"]
    table = app.datastore().table("PersonCaseLink")
    rows, token = [], None
    while True:
        resp = table.get_paged_rows(next_token=token, max_rows=300)
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None) or []
        token = getattr(resp, "next_token", None) or (resp.get("next_token") if isinstance(resp, dict) else None)
        for r in data:
            if r.get("RoleTable") and r.get("RoleTable") != "Accused":
                continue
            try:
                rows.append((int(float(r["ResolvedPersonID"])), str(int(float(r["CaseMasterID"])))))
            except (TypeError, ValueError, KeyError):
                continue
        if not token or not data:
            break
    _pcl_cache["rows"] = rows
    _pcl_cache["ts"] = time.time()
    return rows


def connection_path(app, name_from, name_to):
    a = _find_persons(app, name_from, limit=1)
    b = _find_persons(app, name_to, limit=1)
    if not a or not b:
        missing = name_from if not a else name_to
        return {"info": f"No resolved person found for '{missing}'."}
    src_id = int(float(a[0]["ResolvedPersonID"]))
    dst_id = int(float(b[0]["ResolvedPersonID"]))
    if src_id == dst_id:
        return {"info": "Both names resolve to the same person.",
                "person": a[0]["CanonicalName"]}
    links = _all_links(app)
    by_person, by_case = {}, {}
    for pid, cid in links:
        by_person.setdefault(pid, set()).add(cid)
        by_case.setdefault(cid, set()).add(pid)
    caseno = {r["cid"]: r["crimeno"] for r in _all_geo(app) if r["cid"]}
    names = {int(float(p["ResolvedPersonID"])): p.get("CanonicalName")
             for p in _all_persons(app)}
    # BFS over person nodes via shared cases
    from collections import deque
    prev = {src_id: (None, None)}
    q = deque([src_id])
    found = False
    while q and not found:
        cur = q.popleft()
        for cid in by_person.get(cur, ()):
            for nxt in by_case.get(cid, ()):
                if nxt in prev:
                    continue
                prev[nxt] = (cur, cid)
                if nxt == dst_id:
                    found = True
                    break
                q.append(nxt)
            if found:
                break
    if not found:
        return {"info": f"No connection found between {a[0]['CanonicalName']} "
                        f"and {b[0]['CanonicalName']} through shared cases."}
    hops, node = [], dst_id
    while prev[node][0] is not None:
        parent, cid = prev[node]
        hops.append({"from": names.get(parent, parent), "to": names.get(node, node),
                     "via_case": caseno.get(cid, cid)})
        node = parent
    hops.reverse()
    return {"from": a[0]["CanonicalName"], "to": b[0]["CanonicalName"],
            "degrees": len(hops), "path": hops}


# ------------------------------------------------------------------ risk scoring

def risk_score(app, name):
    persons = _find_persons(app, name, limit=1)
    if not persons:
        return {"info": f"No resolved person found for '{name}'."}
    pid = int(float(persons[0]["ResolvedPersonID"]))
    case_ids = {cid for p, cid in _all_links(app) if p == pid}
    geo = {r["cid"]: r for r in _all_geo(app) if r["cid"]}
    cases = sorted((geo[c] for c in case_ids if c in geo), key=lambda r: r["ym"])
    if not cases:
        return {"info": "Person has no linked cases with usable records."}
    n = len(cases)
    dataset_max = max(r["ym"] for r in _all_geo(app) if r["ym"])
    recent_cut = f"{int(dataset_max[:4]) - 1}{dataset_max[4:]}"
    heinous = sum(1 for c in cases if c["gravity"] == "Heinous")
    recent = sum(1 for c in cases if c["ym"] >= recent_cut)
    late_half = cases[n // 2:]
    escal = (sum(1 for c in late_half if c["gravity"] == "Heinous") / max(len(late_half), 1)
             - heinous / n)
    districts = len({c["district"] for c in cases})
    contributions = [
        {"factor": "Case volume", "detail": f"{n} linked cases",
         "points": round(min(n, 20) / 20 * 35, 1)},
        {"factor": "Heinous offence share", "detail": f"{heinous}/{n} heinous",
         "points": round(heinous / n * 25, 1)},
        {"factor": "Recent activity", "detail": f"{recent} cases in last 12 months of data",
         "points": round(min(recent, 6) / 6 * 20, 1)},
        {"factor": "Escalation trend", "detail": "later cases more severe" if escal > 0 else "no escalation",
         "points": round(max(escal, 0) * 15, 1)},
        {"factor": "Geographic spread", "detail": f"{districts} districts",
         "points": 5.0 if districts >= 3 else 0.0},
    ]
    score = round(sum(c["points"] for c in contributions), 1)
    band = "HIGH" if score >= 60 else "MEDIUM" if score >= 35 else "LOW"
    return {"person": persons[0]["CanonicalName"], "PersonID": pid,
            "risk_score": score, "band": band,
            "contributions": contributions,
            "method": "Transparent additive model — every point traceable to a factor; no black box."}


# ------------------------------------------------------------------ similar cases

def similar_cases(app, crimeno):
    crimeno = str(crimeno).strip()
    rows = [r for r in _all_geo(app) if r["crimeno"]]
    target = next((r for r in rows if r["crimeno"] == crimeno), None)
    if not target:
        return {"info": f"CrimeNo '{crimeno}' not found."}
    if not target["toks"]:
        return {"info": "Target case has no narrative text to compare."}
    scored = []
    for r in rows:
        if r["crimeno"] == crimeno or not r["toks"]:
            continue
        inter = len(target["toks"] & r["toks"])
        if not inter:
            continue
        j = inter / len(target["toks"] | r["toks"])
        if r["crime"] == target["crime"]:
            j += 0.1
        scored.append((round(j, 3), r))
    scored.sort(key=lambda x: -x[0])
    return {"target": {"CrimeNo": crimeno, "crime": target["crime"],
                       "district": target["district"], "month": target["ym"]},
            "similar": [{"CrimeNo": r["crimeno"], "similarity": s,
                         "crime": r["crime"], "district": r["district"],
                         "month": r["ym"], "status": r["status"]}
                        for s, r in scored[:5]]}


# ------------------------------------------------------------------ MO patterns

_mo_cache = {"result": None, "ts": 0}

_MO_STOP = set(("the a an and or of to in on at from with was were is are near by for "
                "complainant accused unknown person persons his her their around approx "
                "approximately rs hours between reported incident investigation taken up "
                "case registered while during about有").split())


def _mo_patterns(app):
    if _mo_cache["result"] and time.time() - _mo_cache["ts"] < 600:
        return _mo_cache["result"]
    table = app.datastore().table("CaseFlat")
    per_crime = {}
    token = None
    while True:
        resp = table.get_paged_rows(next_token=token, max_rows=300)
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None) or []
        token = getattr(resp, "next_token", None) or (resp.get("next_token") if isinstance(resp, dict) else None)
        for r in data:
            crime = r.get("CrimeMinorHead") or ""
            bf = r.get("BriefFacts") or ""
            if not crime or not bf:
                continue
            d = per_crime.setdefault(crime, {"n": 0, "terms": Counter(), "sample": None})
            d["n"] += 1
            if d["sample"] is None:
                d["sample"] = r.get("CrimeNo")
            words = re.findall(r"[a-z]{3,}", bf.lower())
            d["terms"].update(w for w in words if w not in _MO_STOP)
        if not token or not data:
            break
    result = {}
    for crime, d in sorted(per_crime.items(), key=lambda kv: -kv[1]["n"])[:10]:
        result[crime] = {"cases": d["n"],
                         "signature_terms": [w for w, _ in d["terms"].most_common(6)],
                         "sample_crimeno": d["sample"]}
    _mo_cache["result"] = result
    _mo_cache["ts"] = time.time()
    return result


# ------------------------------------------------------------------ network graph

def _links_for_person(backend, pid):
    return backend._q(
        f"SELECT CaseMasterID FROM PersonCaseLink WHERE ResolvedPersonID = {pid} "
        f"AND RoleTable = 'Accused' LIMIT 200", "PersonCaseLink")


def build_network(app, backend, name, max_nodes=30):
    """Ego network: the person, their co-accused, and co-accused-of-co-accused.
    Edge weight = number of shared cases."""
    roots = _find_persons(app, name, limit=1)
    if not roots:
        return {"error": f"No resolved person found for '{name}'."}
    persons_by_id = {int(float(p["ResolvedPersonID"])): p for p in _all_persons(app)}
    root_id = int(float(roots[0]["ResolvedPersonID"]))

    case_members = {}          # case_id -> set(person_ids), built lazily
    def members_of_cases(cids):
        missing = [c for c in cids if c not in case_members]
        if missing:
            rows = backend._q(
                f"SELECT ResolvedPersonID, CaseMasterID FROM PersonCaseLink "
                f"WHERE CaseMasterID IN ({','.join(missing)}) "
                f"AND RoleTable = 'Accused' LIMIT 300", "PersonCaseLink")
            for r in rows:
                cid = str(int(float(r["CaseMasterID"])))
                case_members.setdefault(cid, set()).add(int(float(r["ResolvedPersonID"])))
        return {c: case_members.get(c, set()) for c in cids}

    # hop 1
    root_cases = [str(int(float(l["CaseMasterID"]))) for l in _links_for_person(backend, root_id)]
    frontier = set()
    for c, mem in members_of_cases(root_cases).items():
        frontier |= mem
    frontier.discard(root_id)

    # hop 2 (bounded)
    nodes = {root_id} | frontier
    for pid in list(frontier)[:10]:
        if len(nodes) >= max_nodes:
            break
        cids = [str(int(float(l["CaseMasterID"]))) for l in _links_for_person(backend, pid)]
        for c, mem in members_of_cases(cids).items():
            for m in mem:
                if len(nodes) >= max_nodes:
                    break
                nodes.add(m)

    # edges from co-membership
    pair_cases = {}
    for cid, mem in case_members.items():
        mm = sorted(m for m in mem if m in nodes)
        for i in range(len(mm)):
            for j in range(i + 1, len(mm)):
                pair_cases.setdefault((mm[i], mm[j]), []).append(cid)

    out_nodes = []
    for pid in nodes:
        p = persons_by_id.get(pid, {})
        out_nodes.append({"id": pid,
                          "label": p.get("CanonicalName", f"Person {pid}"),
                          "cases": int(float(p.get("CaseCount") or 0)),
                          "root": pid == root_id})
    out_edges = [{"source": a, "target": b, "weight": len(cids),
                  "shared_cases": cids[:5]}
                 for (a, b), cids in pair_cases.items() if len(cids) >= 1]
    return {"center": roots[0].get("CanonicalName"), "nodes": out_nodes, "edges": out_edges}


# ------------------------------------------------------------------ backend

class CatalystBackend:
    def __init__(self, app):
        self.app = app
        self.zcql = app.zcql()

    def _q(self, query, table_hint=None):
        return _flatten(self.zcql.execute_query(query), table_hint)

    def sql(self, sql: str):
        try:
            rows = self._q(sql)
            if not rows:
                return {"info": "Query returned no rows."}
            if len(rows) > 40:
                extra = len(rows) - 40
                rows = rows[:40]
                for r in rows:
                    r.pop("BriefFacts", None)
                rows.append({"note": f"{extra} more rows truncated — narrow the query "
                                     f"or tell the user the total and show these."})
            elif len(rows) > 10:
                for r in rows:
                    bf = r.get("BriefFacts")
                    if isinstance(bf, str) and len(bf) > 120:
                        r["BriefFacts"] = bf[:120] + "…"
            return rows
        except Exception as e:
            return {"error": str(e)[:300]}

    def risk_score(self, name):
        try:
            return risk_score(self.app, name)
        except Exception as e:
            return {"error": str(e)[:300]}

    def similar_cases(self, crimeno):
        try:
            return similar_cases(self.app, crimeno)
        except Exception as e:
            return {"error": str(e)[:300]}

    def connection_path(self, name_from, name_to):
        try:
            return connection_path(self.app, name_from, name_to)
        except Exception as e:
            return {"error": str(e)[:300]}

    def mo_patterns(self):
        try:
            return _mo_patterns(self.app)
        except Exception as e:
            return {"error": str(e)[:300]}

    def overview(self):
        try:
            rows = _all_geo(self.app)
            by_crime, by_district, by_year = Counter(), Counter(), Counter()
            for r in rows:
                if r["crime"]: by_crime[r["crime"]] += 1
                if r["district"]: by_district[r["district"]] += 1
                if r["year"]: by_year[r["year"]] += 1
            return {"total_cases": len(rows),
                    "by_crime_type": dict(by_crime.most_common(12)),
                    "by_district": dict(by_district.most_common()),
                    "by_year": dict(sorted(by_year.items()))}
        except Exception as e:
            return {"error": str(e)[:300]}

    def person_history(self, name: str):
        try:
            persons = _find_persons(self.app, name, limit=2)
            if not persons:
                return {"info": f"No resolved person found matching '{name}'."}
            out = []
            for p in persons:
                pid = int(float(p["ResolvedPersonID"]))
                links = self._q(
                    f"SELECT CaseMasterID, MatchConfidence FROM PersonCaseLink "
                    f"WHERE ResolvedPersonID = {pid} AND RoleTable = 'Accused' "
                    f"LIMIT 200", "PersonCaseLink")
                ids = ",".join(sorted({str(int(float(l["CaseMasterID"]))) for l in links}))
                cases = self._q(
                    f"SELECT CaseMasterID, CrimeNo, CrimeRegisteredDate, CrimeMinorHead, "
                    f"PoliceStation, CaseStatus FROM CaseFlat "
                    f"WHERE CaseMasterID IN ({ids}) LIMIT 200", "CaseFlat") if ids else []
                seen = set()
                cases = [c for c in cases
                         if not (c.get("CaseMasterID") in seen or seen.add(c.get("CaseMasterID")))]
                by_crime, by_status = Counter(), Counter()
                for c in cases:
                    by_crime[c.get("CrimeMinorHead") or "?"] += 1
                    by_status[c.get("CaseStatus") or "?"] += 1
                recent = sorted(cases, key=lambda c: str(c.get("CrimeRegisteredDate")), reverse=True)
                out.append({
                    "PersonID": pid,
                    "CanonicalName": p["CanonicalName"],
                    "ApproxBirthYear": p.get("ApproxBirthYear"),
                    "TotalCases": len(cases),
                    "ByCrimeType": dict(by_crime.most_common()),
                    "ByStatus": dict(by_status.most_common()),
                    "RecentCases": [
                        {"CrimeNo": c.get("CrimeNo"),
                         "Date": c.get("CrimeRegisteredDate"),
                         "Crime": c.get("CrimeMinorHead"),
                         "PS": c.get("PoliceStation")} for c in recent[:10]],
                })
            return out
        except Exception as e:
            return {"error": str(e)[:300]}

    def co_accused(self, name: str):
        try:
            persons = _find_persons(self.app, name, limit=1)
            if not persons:
                return {"info": f"No resolved person found for '{name}'."}
            pid = int(float(persons[0]["ResolvedPersonID"]))
            links = self._q(
                f"SELECT CaseMasterID FROM PersonCaseLink WHERE ResolvedPersonID = {pid} "
                f"AND RoleTable = 'Accused' LIMIT 200", "PersonCaseLink")
            cids = [str(int(float(l["CaseMasterID"]))) for l in links]
            if not cids:
                return {"info": "No cases for this person."}
            others = self._q(
                f"SELECT ResolvedPersonID, CaseMasterID FROM PersonCaseLink "
                f"WHERE CaseMasterID IN ({','.join(cids)}) AND RoleTable = 'Accused' "
                f"LIMIT 300", "PersonCaseLink")
            shared = Counter(int(float(o["ResolvedPersonID"])) for o in others
                             if int(float(o["ResolvedPersonID"])) != pid)
            result = []
            for opid, n in shared.most_common(10):
                if n < 2:
                    continue
                nm = self._q(f"SELECT CanonicalName, CaseCount FROM ResolvedPerson "
                             f"WHERE ResolvedPersonID = {opid} LIMIT 1", "ResolvedPerson")
                result.append({"PersonID": opid, "SharedCases": n,
                               "CanonicalName": nm[0]["CanonicalName"] if nm else "?",
                               "TotalCases": nm[0].get("CaseCount") if nm else None})
            return result or {"info": "No repeat co-accused found."}
        except Exception as e:
            return {"error": str(e)[:300]}


# ------------------------------------------------------------------ GLM client

_token_cache = {"token": None, "expires": 0}


def _access_token():
    if _token_cache["token"] and time.time() < _token_cache["expires"]:
        return _token_cache["token"]
    r = requests.post("https://accounts.zoho.eu/oauth/v2/token", data={
        "grant_type": "refresh_token",
        "client_id": os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
    }, timeout=30)
    r.raise_for_status()
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"Zoho token refresh failed: {d}")
    _token_cache["token"] = d["access_token"]
    _token_cache["expires"] = time.time() + int(d.get("expires_in", 3600)) - 120
    return _token_cache["token"]


def make_llm():
    url = os.environ["GLM_ENDPOINT_URL"]

    def llm(messages):
        token = _access_token()
        headers = {
            "Authorization": f"Zoho-oauthtoken {token}",
            "CATALYST-ORG": os.environ["CAT_ORG_ID"],
            "Content-Type": "application/json",
        }
        body = {"model": "crm-di-glm47b_30b_it", "messages": messages,
                "temperature": 0.1, "max_tokens": 1500}
        t0 = time.time()
        r = requests.post(url, headers=headers, json=body, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f"GLM call failed: {r.status_code}: {r.text[:300]}")
        data = r.json()
        print(f"GLM {time.time()-t0:.1f}s usage={data.get('usage')}")
        text = data.get("response", "")
        if not text:
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                text = json.dumps(data)[:2000]
        # GLM emits reasoning terminated by </think> (often without an opener)
        if "</think>" in text:
            text = text.split("</think>")[-1]
        elif text.lstrip().startswith(("1.", "**", "Let me", "Checking", "The user")):
            # reasoning ran past max_tokens without closing -> let the agent retry
            return ""
        return text.strip()

    return llm


# ------------------------------------------------------------------ handler

def handler(request):
    try:
        app = zcatalyst_sdk.initialize()
        body = request.get_json(silent=True) or {}
        if body.get("poll"):
            tag = re.sub(r"[^A-Za-z0-9#\-]", "", str(body["poll"]))[:80]
            try:
                rows = _flatten(app.zcql().execute_query(
                    f"SELECT Answer, Citations, Audit FROM AuditLog "
                    f"WHERE SessionID = '{tag}' LIMIT 1"), "AuditLog")
            except Exception as e:
                return {"pending": True, "note": str(e)[:120]}
            if not rows:
                return {"pending": True}
            row = rows[0]
            def _load(s, default):
                try:
                    return json.loads(s) if s else default
                except Exception:
                    return default
            return {"pending": False,
                    "answer": row.get("Answer") or "",
                    "citations": _load(row.get("Citations"), []),
                    "audit": _load(row.get("Audit"), [])}

        if body.get("trend") is not None:
            return build_trend(app, body.get("trend") or {})

        if body.get("hotspot") is not None:
            return build_hotspot(app, body.get("hotspot") or {})

        if body.get("network"):
            backend = CatalystBackend(app)
            return build_network(app, backend, str(body["network"]))

        message = (body.get("message") or "").strip()
        if not message:
            return {"error": "message is required"}, 400
        history = body.get("history", [])

        turn = run_agent(message, history, make_llm(), CatalystBackend(app))

        try:
            app.datastore().table("AuditLog").insert_row({
                "SessionID": str(body.get("session_id", "")),
                "UserMessage": message[:2000],
                "Answer": (turn.answer or "")[:5000],
                "Citations": json.dumps(turn.citations)[:2000],
                "Audit": json.dumps(turn.audit, default=str)[:20000],
            })
        except Exception as e:
            print("AuditLog write failed:", str(e)[:200])

        return {"answer": turn.answer,
                "citations": turn.citations,
                "audit": json.loads(json.dumps(turn.audit, default=str))}
    except Exception:
        print("Agent FAILED:\n" + traceback.format_exc())
        return {"error": "internal error — see function logs"}, 500
