#!/usr/bin/env bash
# Idempotent local HydraDB startup for Context Compiler spike work.
#
# Usage:
#   scripts/run_hydradb.sh start   # default: start (or reuse) a backgrounded node, wait for readiness
#   scripts/run_hydradb.sh stop    # stop a node started by this script
#   scripts/run_hydradb.sh status  # check whether the node is ready
#   scripts/run_hydradb.sh reset   # stop, WIPE the local store, start clean
#
# `reset` destroys every node and relationship in the local dev store. It exists
# so ingest benchmarks and count assertions start from an empty graph; leftover
# spike fixtures otherwise inflate the read-back counts. It only ever touches
# $HYDRADB_DATA_ROOT (default ~/.local/state/hydradb-dev), never ~/hydradb.
#
# Exits non-zero if the node never becomes ready.
set -euo pipefail

HYDRADB_DIR="${HYDRADB_DIR:-$HOME/hydradb}"
DATA_ROOT="${HYDRADB_DATA_ROOT:-$HOME/.local/state/hydradb-dev}"
LOG_FILE="${HYDRADB_LOG_FILE:-/tmp/hydradb-node.log}"
PID_FILE="${HYDRADB_PID_FILE:-/tmp/hydradb-node.pid}"

BOLT_ADDR="127.0.0.1:7687"
HTTP_ADDR="127.0.0.1:8443"
ADMIN_ADDR="127.0.0.1:9090"
AUTH_TOKEN="local-development-token-32-bytes"

export CLOUD_PROVIDER=local
export LOCAL_PATH="$DATA_ROOT/store"
export GRAPH_NAMESPACE=default
export GRAPH_ID=default
export GRAPH_CELL_ID=cell-0
export GRAPH_CELLS=cell-0
export GRAPH_NODE_ID=node-0
export GRAPH_BOLT_NODE_ADDRESSES="node-0=${BOLT_ADDR}"
export GRAPH_ADVERTISED_BOLT_ADDR="${BOLT_ADDR}"
export GRAPH_DATA_CACHE_DIR="$DATA_ROOT/cache"
export GRAPH_AUTH_TOKEN_FILE="$DATA_ROOT/auth-token"
export GRAPH_ALLOW_PLAINTEXT=true
# Without this, graph-node builds, serves /readyz, then aborts with a stack
# overflow on the first query (README "Troubleshooting local runs").
export RUST_MIN_STACK=33554432

wait_ready() {
  for _ in $(seq 1 60); do
    if curl -sf "http://${ADMIN_ADDR}/readyz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

cmd="${1:-start}"

case "$cmd" in
  status)
    if is_running && curl -sf "http://${ADMIN_ADDR}/readyz" >/dev/null 2>&1; then
      echo "hydradb: ready (pid $(cat "$PID_FILE"))"
      exit 0
    fi
    echo "hydradb: not ready"
    exit 1
    ;;
  stop)
    if is_running; then
      pid="$(cat "$PID_FILE")"
      kill "$pid"
      # Wait for full exit before returning: the writer holds an object-store
      # lease and runs an async compactor, so a caller that immediately wipes
      # or reuses the data directory can race a still-shutting-down process
      # and corrupt the manifest's view of compacted SST files.
      for _ in $(seq 1 60); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      if kill -0 "$pid" 2>/dev/null; then
        echo "hydradb: pid $pid did not exit after SIGTERM within 30s" >&2
        exit 1
      fi
      rm -f "$PID_FILE"
      echo "hydradb: stopped"
    else
      echo "hydradb: not running"
    fi
    exit 0
    ;;
  reset)
    # Stop first and wait for full exit: the writer holds an object-store lease
    # and runs an async compactor, so wiping under a live process corrupts the
    # new instance's view of compacted SST files (see Item 0 results).
    "$0" stop
    rm -rf "$DATA_ROOT/store" "$DATA_ROOT/cache"
    exec "$0" start
    ;;
  start)
    mkdir -p "$DATA_ROOT/store" "$DATA_ROOT/cache"
    if [[ ! -f "$DATA_ROOT/auth-token" ]]; then
      printf '%s\n' "$AUTH_TOKEN" > "$DATA_ROOT/auth-token"
    fi

    if is_running && curl -sf "http://${ADMIN_ADDR}/readyz" >/dev/null 2>&1; then
      echo "hydradb: already running and ready (pid $(cat "$PID_FILE"))"
      exit 0
    fi

    cd "$HYDRADB_DIR"
    nohup cargo run --locked --features server-runtime --bin graph-node \
      > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    disown

    if wait_ready; then
      echo "hydradb: ready (pid $(cat "$PID_FILE"))"
      echo "hydradb: bolt=${BOLT_ADDR} http=${HTTP_ADDR} admin=${ADMIN_ADDR} token=${AUTH_TOKEN}"
      exit 0
    else
      echo "hydradb: node never became ready; see ${LOG_FILE}" >&2
      tail -n 60 "$LOG_FILE" >&2 || true
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|status|reset}" >&2
    exit 2
    ;;
esac
