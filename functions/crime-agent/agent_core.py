#!/usr/bin/env python3
"""
agent_core.py — orchestration loop, ZCQL edition (production/Catalyst).

Same agent design as the local version, but the query tool speaks ZCQL:
no EXTRACT(), no window functions, date filters via LIKE on text dates,
single-table queries against the pre-joined CaseFlat.
"""

import json
import re
from dataclasses import dataclass, field

MAX_STEPS = 4

SYSTEM_PROMPT = """You are the Karnataka State Police Crime Intelligence Assistant.
You answer investigator questions using ONLY the tools below. Never invent facts.

TOOLS (respond with EXACTLY one JSON object, nothing else — no prose, no markdown):

1. Query case records (ZCQL over the pre-joined CaseFlat table):
   {"action": "sql_query", "sql": "SELECT ... FROM CaseFlat WHERE ... LIMIT 50", "reason": "..."}
   CaseFlat columns:
     CaseMasterID (int), CrimeNo (text), CrimeRegisteredDate (text 'YYYY-MM-DD'),
     PoliceStation (text), District (text, EXACT values: 'Bengaluru City',
       'Bengaluru Rural','Mysuru','Mangaluru City','Belagavi','Kalaburagi',
       'Hubballi-Dharwad','Shivamogga'),
     CaseCategory (text: 'FIR','UDR','PAR','Zero FIR'),
     Gravity (text: 'Heinous'|'Non-Heinous'), CrimeMajorHead (text),
     CrimeMinorHead (text, EXACT values: 'Murder','Attempt to Murder',
       'Grievous Hurt','Simple Hurt','House Burglary','Vehicle Theft',
       'Chain Snatching','Robbery','Ordinary Theft',
       'Cruelty by Husband/Relatives','Molestation','Cheating',
       'Criminal Breach of Trust','Online Financial Fraud','Identity Theft',
       'Rioting','Unlawful Assembly'),
     CaseStatus (text), Court (text), IncidentFromDate (text),
     latitude (double), longitude (double), BriefFacts (text),
     FinalReportType (text: 'A'=chargesheet,'B'=false case,'C'=undetected)

   ZCQL DIALECT RULES (important — this is NOT full SQL):
   - SELECT only, single table (CaseFlat), no JOINs, no subqueries.
   - LIKE does NOT work. NEVER use LIKE. Use only = != > >= < <= AND OR IN.
   - Text columns match by EXACT equality with values from the lists above,
     e.g. District = 'Bengaluru City', CrimeMinorHead = 'Chain Snatching'.
     Never guess partial strings.
   - NO EXTRACT()/YEAR()/date functions. Date filters use string range
     comparison: year 2025 ->
       CrimeRegisteredDate >= '2025-01-01' AND CrimeRegisteredDate <= '2025-12-31'
   - COUNT(*) is NOT supported; use COUNT(CaseMasterID) instead.
   - Supported: WHERE, GROUP BY, ORDER BY, COUNT(column), SUM, AVG, MIN, MAX, LIMIT.
   - Always include LIMIT (max 200). Select CrimeNo when returning case
     lists so answers can be cited.
   - For "list the cases/events" questions: LIMIT 25, select ONLY CrimeNo,
     CrimeRegisteredDate, PoliceStation, CrimeMinorHead, CaseStatus. NEVER
     select BriefFacts or * in lists. Summarize the list and tell the user
     details of any specific CrimeNo can be requested next.
   - Answer in AT MOST 2 tool calls. Design the first query to fully answer
     the question; do not explore with multiple queries.

2. Resolved person criminal history (handles name spelling variants):
   {"action": "person_history", "name": "Manjunath", "reason": "..."}

3. Co-accused network of a person:
   {"action": "co_accused", "name": "Syed Imran", "reason": "..."}

4. Final answer:
   {"action": "final_answer", "answer": "...", "citations": ["CrimeNo:...", "Person:..."]}

RULES:
- Keep final_answer under 120 words. When listing cases, show AT MOST 8 and
  state the total count; the user can ask for more or for a specific CrimeNo.
- Output the JSON on a single line. No markdown, no line breaks inside JSON.
- For a person's history/record use person_history, NOT sql_query on a name.
- Questions like "who does X work/operate/commit crimes with", gangs, or
  associates -> use co_accused first.
- Cite CrimeNos or Person IDs from tool results in every final_answer.
- If tools return nothing, say so; never fabricate.
- Answer in the user's language (English or Kannada).
"""

SQL_ALLOWED = re.compile(r"^\s*SELECT\s", re.I)
SQL_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|COPY|PRAGMA|EXPORT|CALL|LIKE)\b", re.I)
ALLOWED_TABLES = {"caseflat"}


def validate_sql(sql: str) -> str | None:
    if not SQL_ALLOWED.match(sql):
        return "Only SELECT statements are permitted."
    if SQL_FORBIDDEN.search(sql):
        return "Statement contains a forbidden keyword."
    scan = re.sub(r"\b(EXTRACT|SUBSTRING|TRIM)\s*\([^)]*\)", " ", sql, flags=re.I)
    tables = set(t.lower() for t in re.findall(r"\bFROM\s+([A-Za-z_]+)", scan, re.I))
    tables |= set(t.lower() for t in re.findall(r"\bJOIN\s+([A-Za-z_]+)", scan, re.I))
    if not tables.issubset(ALLOWED_TABLES):
        return f"Only these tables are queryable: {sorted(ALLOWED_TABLES)}."
    if not re.search(r"\bLIMIT\s+\d+", sql, re.I):
        return "Missing LIMIT."
    return None


@dataclass
class AgentTurn:
    answer: str = ""
    citations: list = field(default_factory=list)
    audit: list = field(default_factory=list)


def run_agent(user_message: str, history: list, llm, backend) -> AgentTurn:
    turn = AgentTurn()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history[-8:]
    messages.append({"role": "user", "content": user_message})
    tools_used = 0

    for step in range(MAX_STEPS):
        raw = llm(messages)
        action = parse_action(raw)
        turn.audit.append({"step": step, "model_action": action if action else raw[:500]})

        if action is None:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content":
                "Invalid response. Reply with exactly one JSON action object, no other text."})
            continue

        kind = action.get("action")
        if kind == "final_answer":
            if tools_used == 0:
                turn.audit.append({"step": step, "guardrail":
                    "final_answer without tool evidence rejected"})
                messages.append({"role": "assistant", "content": json.dumps(action)})
                messages.append({"role": "user", "content":
                    "REJECTED: You answered without consulting any tool. You have no "
                    "knowledge of this database. Call person_history, co_accused, or "
                    "sql_query first, then answer only from the observation."})
                continue
            turn.answer = action.get("answer", "")
            turn.citations = action.get("citations", [])
            return turn

        if kind == "sql_query":
            sql = action.get("sql", "")
            err = validate_sql(sql)
            obs = {"error": err} if err else backend.sql(sql)
        elif kind == "person_history":
            obs = backend.person_history(action.get("name", ""))
        elif kind == "co_accused":
            obs = backend.co_accused(action.get("name", ""))
        else:
            obs = {"error": f"Unknown action '{kind}'."}

        if not (isinstance(obs, dict) and "error" in obs):
            tools_used += 1

        turn.audit.append({"step": step,
                           "observation_rows": len(obs) if isinstance(obs, list) else obs})
        messages.append({"role": "assistant", "content": json.dumps(action)})
        messages.append({"role": "user", "content":
            "OBSERVATION:\n" + json.dumps(obs, default=str)[:6000] +
            "\nContinue: another tool call or final_answer with citations."})

    turn.answer = ("I couldn't complete this within the step limit. "
                   "Please rephrase or narrow the question.")
    return turn


def parse_action(raw: str):
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # salvage a final_answer truncated mid-JSON by max_tokens: recover the
    # answer text so the user gets a (clipped) reply instead of a retry loop
    if '"final_answer"' in raw:
        am = re.search(r'"answer"\s*:\s*"(.*)', raw, re.DOTALL)
        if am:
            text = am.group(1)
            # cut at an unescaped closing quote if present, else take it all
            qm = re.search(r'(?<!\\)"', text)
            if qm:
                text = text[:qm.start()]
            text = text.replace('\\n', '\n').replace('\\"', '"').strip()
            if len(text) > 20:
                return {"action": "final_answer",
                        "answer": text + " …",
                        "citations": [],
                        "salvaged": True}
    return None
