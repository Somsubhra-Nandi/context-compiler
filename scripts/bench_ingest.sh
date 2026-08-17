#!/usr/bin/env bash
# Ingest benchmark matrix for the Item 3 results doc.
#
# Each configuration runs against a freshly wiped store, so timings are
# comparable and read-back counts are exact.
#
#   scripts/bench_ingest.sh <symbols.jsonl> <edges.jsonl> <limit> [batch...]
set -euo pipefail

cd "$(dirname "$0")/.."
SYMBOLS="${1:?usage: bench_ingest.sh SYMBOLS EDGES LIMIT [BATCH...]}"
EDGES="${2:?}"
LIMIT="${3:?}"
shift 3
BATCHES=("${@:-500}")

OUT="${BENCH_OUT:-/tmp/cc-bench}"
mkdir -p "$OUT"

run() {
  local label="$1"; shift
  bash scripts/run_hydradb.sh reset >/dev/null
  echo "=== $label ==="
  .venv/bin/python -m context_compiler.graph.ingest \
    --symbols "$SYMBOLS" --edges "$EDGES" --limit "$LIMIT" --json "$@" \
    > "$OUT/$label.json"
  .venv/bin/python - "$OUT/$label.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
print(f"  wall {r['wall_seconds']}s  batch={r['batch_size']}  text={r['text_in_graph']}")
for p in r["passes"]:
    print(f"    {p['name']:<24} {p['rows']:>8,} rows  {p['requests']:>5} req  {p['seconds']:>7.2f}s"
          + (f"  retries={p['retries']}" if p['retries'] else "")
          + (f"  splits={p['splits']}" if p['splits'] else ""))
n = r["nodes"]
if n["oversize_symbols"]:
    print(f"    OVERSIZE: {n['oversize_symbols']} symbols, fields {n['oversize_fields']}")
PY
}

for b in "${BATCHES[@]}"; do
  run "notext-b$b" --batch "$b"
done
for b in "${BATCHES[@]}"; do
  run "text-b$b" --batch "$b" --text --text-batch "$b"
done
