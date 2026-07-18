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

    def person_history(self, name: str):
        try:
            persons = _find_persons(self.app, name)
            if not persons:
                return {"info": f"No resolved person found matching '{name}'."}
            out = []
            for p in persons:
                pid = int(float(p["ResolvedPersonID"]))
                links = self._q(
                    f"SELECT RoleRowID, CaseMasterID, MatchConfidence FROM PersonCaseLink "
                    f"WHERE ResolvedPersonID = {pid} AND RoleTable = 'Accused' "
                    f"LIMIT 200", "PersonCaseLink")
                conf = {str(l["CaseMasterID"]): l.get("MatchConfidence") for l in links}
                ids = ",".join(str(int(float(l["CaseMasterID"]))) for l in links)
                cases = self._q(
                    f"SELECT CaseMasterID, CrimeNo, CrimeRegisteredDate, CrimeMinorHead, "
                    f"PoliceStation, CaseStatus FROM CaseFlat "
                    f"WHERE CaseMasterID IN ({ids}) LIMIT 200", "CaseFlat") if ids else []
                for c in cases:
                    c.update({"PersonID": pid,
                              "CanonicalName": p["CanonicalName"],
                              "ApproxBirthYear": p.get("ApproxBirthYear"),
                              "MatchConfidence": conf.get(str(c.get("CaseMasterID")))})
                    out.append(c)
            return out or {"info": "Person found but no linked cases."}
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
                "temperature": 0.1, "max_tokens": 2000}
        r = requests.post(url, headers=headers, json=body, timeout=90)
        if r.status_code != 200:
            raise RuntimeError(f"GLM call failed: {r.status_code}: {r.text[:300]}")
        data = r.json()
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
