#!/usr/bin/env bash
# ==============================================================================
# service.sh - Local Background Service Manager for Antigravity Proxy & LiteLLM
#
# Usage:
#   ./service.sh start    - Start services in background (persists across terminal closes)
#   ./service.sh stop     - Stop background services
#   ./service.sh restart  - Restart background services
#   ./service.sh status   - Check status and health of services
#   ./service.sh logs     - Tail service logs (proxy / litellm)
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"
# Source .env if present
if [ -f "$DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$DIR/.env"
    set +a
fi


RUN_DIR="$DIR/.run"
LOG_DIR="$DIR/logs"
PROXY_PID_FILE="$RUN_DIR/proxy.pid"
LITELLM_PID_FILE="$RUN_DIR/litellm.pid"
PROXY_LOG="$LOG_DIR/proxy.log"
LITELLM_LOG="$LOG_DIR/litellm.log"

PROXY_PORT="${PORT:-8000}"
LITELLM_PORT="${LITELLM_PORT:-4000}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# Resolve python / uvicorn binaries
if [ -x "$DIR/.venv/bin/uvicorn" ]; then
    UVICORN="$DIR/.venv/bin/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
    UVICORN="$(command -v uvicorn)"
else
    echo "Error: uvicorn not found in .venv or PATH." >&2
    exit 1
fi

# Resolve litellm binary
LITELLM_BIN=""
if [ -x "$DIR/.venv/bin/litellm" ]; then
    LITELLM_BIN="$DIR/.venv/bin/litellm"
elif command -v litellm >/dev/null 2>&1; then
    LITELLM_BIN="$(command -v litellm)"
fi

is_running() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

start_proxy() {
    if is_running "$PROXY_PID_FILE"; then
        echo "ℹ Antigravity Proxy is already running (PID: $(cat "$PROXY_PID_FILE"))."
        return 0
    fi

    # Check for port conflict
    if lsof -i ":$PROXY_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "✗ Error: Port $PROXY_PORT is already in use by another process:" >&2
        lsof -i ":$PROXY_PORT" -sTCP:LISTEN >&2
        if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -q "antigravity-proxy"; then
            echo "" >&2
            echo "Tip: Docker is currently occupying port $PROXY_PORT." >&2
            echo "     Run 'docker compose down' to stop the container before starting locally." >&2
        fi
        return 1
    fi

    echo "→ Starting Antigravity Proxy on http://127.0.0.1:$PROXY_PORT..."
    nohup "$UVICORN" app.main:app --host 127.0.0.1 --port "$PROXY_PORT" >> "$PROXY_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$PROXY_PID_FILE"

    # Wait for health check
    local healthy=0
    for _ in {1..25}; do
        if curl -s "http://127.0.0.1:$PROXY_PORT/status" >/dev/null 2>&1; then
            healthy=1
            break
        fi
        sleep 0.2
    done

    if [ "$healthy" -eq 1 ]; then
        echo "✓ Antigravity Proxy started (PID: $pid). Logs: logs/proxy.log"
    else
        echo "⚠ Proxy process launched (PID: $pid), but health check is taking longer than usual."
        echo "  Inspect logs: tail -f logs/proxy.log"
    fi
}

start_litellm() {
    if [ -z "$LITELLM_BIN" ]; then
        echo "ℹ LiteLLM binary not found (optional sidecar skipped)."
        echo "  To enable LiteLLM sidecar on :$LITELLM_PORT, install it: uv pip install litellm or pip install litellm"
        return 0
    fi

    if is_running "$LITELLM_PID_FILE"; then
        echo "ℹ LiteLLM is already running (PID: $(cat "$LITELLM_PID_FILE"))."
        return 0
    fi

    if lsof -i ":$LITELLM_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "✗ Warning: Port $LITELLM_PORT is already in use. Skipping LiteLLM startup." >&2
        return 0
    fi

    # Generate local config pointing to 127.0.0.1:$PROXY_PORT
    local local_config="$DIR/litellm-config.local.yaml"
    sed "s|antigravity-proxy:8000|127.0.0.1:$PROXY_PORT|g" "$DIR/litellm-config.yaml" > "$local_config"

    echo "→ Starting LiteLLM on http://127.0.0.1:$LITELLM_PORT..."
    nohup "$LITELLM_BIN" --config "$local_config" --host 127.0.0.1 --port "$LITELLM_PORT" >> "$LITELLM_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$LITELLM_PID_FILE"

    local healthy=0
    for _ in {1..25}; do
        if curl -s "http://127.0.0.1:$LITELLM_PORT/health/readiness" >/dev/null 2>&1 || curl -s "http://127.0.0.1:$LITELLM_PORT/v1/models" >/dev/null 2>&1; then
            healthy=1
            break
        fi
        sleep 0.2
    done

    if [ "$healthy" -eq 1 ]; then
        echo "✓ LiteLLM started (PID: $pid). Logs: logs/litellm.log"
    else
        echo "⚠ LiteLLM process launched (PID: $pid). Check logs: tail -f logs/litellm.log"
    fi
}

stop_process() {
    local name="$1"
    local pid_file="$2"

    if [ ! -f "$pid_file" ]; then
        return 0
    fi

    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "→ Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null || true

        # Wait up to 5 seconds for graceful termination
        for _ in {1..25}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 0.2
        done

        # Force kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            echo "⚠ Force killing $name (PID: $pid)..."
            kill -9 "$pid" 2>/dev/null || true
        fi
        echo "✓ $name stopped."
    fi
    rm -f "$pid_file"
}

cmd_start() {
    echo "=== Starting Services in Background ==="
    start_proxy
    start_litellm
    echo "======================================="
    cmd_status
}

cmd_stop() {
    echo "=== Stopping Background Services ==="
    stop_process "LiteLLM" "$LITELLM_PID_FILE"
    stop_process "Antigravity Proxy" "$PROXY_PID_FILE"
    echo "✓ All background services stopped."
    echo "===================================="
}

cmd_status() {
    echo "================ Service Status ================"
    # Proxy status
    if is_running "$PROXY_PID_FILE"; then
        local pid
        pid="$(cat "$PROXY_PID_FILE")"
        echo "Antigravity Proxy: RUNNING"
        echo "  • PID:      $pid"
        echo "  • Endpoint: http://127.0.0.1:$PROXY_PORT"
        local status_json
        status_json="$(curl -s "http://127.0.0.1:$PROXY_PORT/status" 2>/dev/null || true)"
        if [ -n "$status_json" ]; then
            local auth total active
            auth="$(echo "$status_json" | grep -o '"authenticated":[a-z]*' | cut -d: -f2)"
            total="$(echo "$status_json" | grep -o '"total_accounts":[0-9]*' | cut -d: -f2)"
            active="$(echo "$status_json" | grep -o '"active_accounts":[0-9]*' | cut -d: -f2)"
            echo "  • Pool:     $active active / $total total (auth: $auth)"
        fi
    else
        echo "Antigravity Proxy: STOPPED"
    fi

    echo ""
    # LiteLLM status
    if is_running "$LITELLM_PID_FILE"; then
        local lpid
        lpid="$(cat "$LITELLM_PID_FILE")"
        echo "LiteLLM:           RUNNING"
        echo "  • PID:      $lpid"
        echo "  • Endpoint: http://127.0.0.1:$LITELLM_PORT"
    elif [ -n "$LITELLM_BIN" ]; then
        echo "LiteLLM:           STOPPED (binary available)"
    else
        echo "LiteLLM:           NOT INSTALLED (optional)"
    fi
    echo "================================================"
}

cmd_logs() {
    local target="${1:-proxy}"
    case "$target" in
        proxy)
            if [ -f "$PROXY_LOG" ]; then
                tail -n 50 -f "$PROXY_LOG"
            else
                echo "No proxy log found at $PROXY_LOG"
            fi
            ;;
        litellm)
            if [ -f "$LITELLM_LOG" ]; then
                tail -n 50 -f "$LITELLM_LOG"
            else
                echo "No LiteLLM log found at $LITELLM_LOG"
            fi
            ;;
        all)
            tail -n 25 -f "$PROXY_LOG" "$LITELLM_LOG" 2>/dev/null || true
            ;;
        *)
            echo "Usage: ./service.sh logs [proxy|litellm|all]"
            ;;
    esac
}

case "${1:-status}" in
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_stop
        sleep 1
        cmd_start
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs "${2:-proxy}"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs [proxy|litellm|all]}"
        exit 1
        ;;
esac
