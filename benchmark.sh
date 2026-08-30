#!/bin/bash
# KSP Analytics Platform - benchmark harness
# Usage: ./benchmark.sh https://project-rainfall-20116559418.development.catalystserverless.eu

BASE="${1:?usage: ./benchmark.sh <base-url>}/server/analytics"
N=20

t() { curl -s -o /dev/null -w "%{time_total}" "$1"; }

echo "=== Cold start (cache cleared via /refresh) ==="
curl -s "$BASE/refresh" > /dev/null
COLD=$(t "$BASE/summary")
echo "summary (full 5k-row load): ${COLD}s"
TRAIN=$(t "$BASE/ml")
echo "ml (first call = model training): ${TRAIN}s"

echo ""
echo "=== Warm latency, $N requests per endpoint (p50 / p95 / max, seconds) ==="
for EP in summary by-district by-station timeseries heatmap hotspots spikes anomalies risk offenders socio "network?name=syed" ml "ml/triage?limit=10"; do
  TIMES=()
  for i in $(seq 1 $N); do TIMES+=("$(t "$BASE/$EP")"); done
  SORTED=($(printf "%s\n" "${TIMES[@]}" | sort -n))
  P50=${SORTED[$((N / 2 - 1))]}
  P95=${SORTED[$((N * 95 / 100 - 1))]}
  MAX=${SORTED[$((N - 1))]}
  printf "%-22s p50 %ss   p95 %ss   max %ss\n" "$EP" "$P50" "$P95" "$MAX"
done

echo ""
echo "=== Freshness chain (manual) ==="
echo "1. Note the time; INSERT a row via ZCQL console"
echo "2. curl $BASE/refresh"
echo "3. Watch the dashboard stamp; note when the count ticks"
echo "   (expected: within one 90s poll cycle)"
