#!/usr/bin/env python3
"""
main.py — one-shot SEEDER cron function: loads bundled CSVs into Data Store.

PREREQUISITE: every target table must already exist in Data Store with
columns named EXACTLY like the CSV headers (case-sensitive). This function
inserts rows; it cannot create tables or columns.

Env vars (all optional):
  TABLES          comma list to seed a subset, e.g. "Accused,CaseMaster"
                  (default: all bundled tables)
  TRUNCATE_FIRST  "true" to wipe each table before inserting (default "false")

Run it once (or per-subset if you hit the execution time limit), then check
the logs: it prints per-table inserted counts and any per-batch errors.
Delete or disable the function afterwards.
"""

import csv
import os
import traceback

import zcatalyst_sdk

BATCH = 100


def find_data_dir():
    """Resolve the bundled data/ dir across possible runtime layouts."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "data"),
        os.path.join(os.getcwd(), "data"),
        here,                      # CSVs at bundle root as a last resort
        os.getcwd(),
    ]
    for c in candidates:
        if os.path.isdir(c) and any(f.endswith(".csv") for f in os.listdir(c)):
            return c
    return None


def log_environment():
    here = os.path.dirname(os.path.abspath(__file__))
    print(f"DIAG __file__ dir: {here}")
    print(f"DIAG contents: {sorted(os.listdir(here))[:30]}")
    print(f"DIAG cwd: {os.getcwd()}")
    print(f"DIAG cwd contents: {sorted(os.listdir(os.getcwd()))[:30]}")

# seed order: parents before children (harmless either way — Data Store
# doesn't enforce FKs — but keeps things tidy)
ALL_TABLES = [
    "CaseMaster", "Inv_OccuranceTime", "Accused", "Victim",
    "ComplainantDetails", "ActSectionAssociation", "ArrestSurrender",
    "inv_arrestsurrenderaccused", "ChargesheetDetails",
    "ResolvedPerson", "PersonCaseLink", "CaseFlat",
]


def fetch_all(table):
    rows, token = [], None
    while True:
        resp = table.get_paged_rows(next_token=token, max_rows=300)
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None) or []
        token = getattr(resp, "next_token", None) or (resp.get("next_token") if isinstance(resp, dict) else None)
        rows.extend(data)
        if not token or not data:
            return rows


def truncate(table):
    while True:
        rows = fetch_all(table)
        if not rows:
            return
        ids = [r["ROWID"] for r in rows if "ROWID" in r]
        for k in range(0, len(ids), BATCH):
            table.delete_rows(ids[k:k + BATCH])


def clean(row):
    """Empty strings -> omit key (lets Data Store store NULL)."""
    return {k: v for k, v in row.items() if v is not None and str(v) != ""}


def handler(cron_details, context):
    try:
        log_environment()
        data_dir = find_data_dir()
        if not data_dir:
            print("FATAL: bundled data/ directory not found anywhere — see DIAG lines above")
            _close(context, False)
            return
        print(f"DIAG using data dir: {data_dir}")

        app = zcatalyst_sdk.initialize(req=context)
        ds = app.datastore()
        wanted = os.environ.get("TABLES", "")
        tables = [t.strip() for t in wanted.split(",") if t.strip()] or ALL_TABLES
        do_truncate = os.environ.get("TRUNCATE_FIRST", "false").lower() == "true"

        for name in tables:
            path = os.path.join(data_dir, f"{name}.csv")
            if not os.path.exists(path):
                print(f"{name}: no bundled CSV, skipping")
                continue
            with open(path) as f:
                rows = [clean(r) for r in csv.DictReader(f)]
            try:
                table = ds.table(name)
                if do_truncate:
                    truncate(table)
                inserted = 0
                for k in range(0, len(rows), BATCH):
                    batch = rows[k:k + BATCH]
                    try:
                        table.insert_rows(batch)
                        inserted += len(batch)
                    except Exception as e:
                        print(f"{name}: batch {k}-{k+len(batch)} FAILED: {str(e)[:200]}")
                print(f"{name}: inserted {inserted}/{len(rows)}")
            except Exception as e:
                print(f"{name}: TABLE-LEVEL FAILURE: {str(e)[:300]}")

        print("Seeding complete.")
        _close(context, True)
    except Exception:
        print("Seeder FAILED:\n" + traceback.format_exc())
        _close(context, False)


def _close(context, success):
    for attr in (("close_with_success",) if success else ("close_with_failure",)) + ("close",):
        fn = getattr(context, attr, None)
        if callable(fn):
            fn()
            return
