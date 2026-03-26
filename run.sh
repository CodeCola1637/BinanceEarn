#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ── 配置 ──────────────────────────────────────
VENV="venv"
PID_FILE="logs/earner.pid"
LOG_FILE="logs/earner.log"
DASH_PID_FILE="logs/dashboard.pid"
DASH_LOG_FILE="logs/dashboard.log"

activate_venv() {
    if [ -d "$VENV" ]; then
        source "$VENV/bin/activate"
    else
        echo "⚠ 虚拟环境不存在，正在创建..."
        python3 -m venv "$VENV"
        source "$VENV/bin/activate"
        pip install -r requirements.txt
    fi
}

print_status() {
    echo ""
    echo "── 进程状态 ──────────────────────────"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "  Earner:    ✅ 运行中 (PID $(cat "$PID_FILE"))"
    else
        echo "  Earner:    ⏹ 未运行"
    fi
    if [ -f "$DASH_PID_FILE" ] && kill -0 "$(cat "$DASH_PID_FILE")" 2>/dev/null; then
        echo "  Dashboard: ✅ 运行中 (PID $(cat "$DASH_PID_FILE"))"
    else
        echo "  Dashboard: ⏹ 未运行"
    fi
    echo "─────────────────────────────────────"
}

do_start_dryrun() {
    activate_venv
    echo "▶ 启动 Dry-Run（只查询不操作）..."
    nohup python3 binance_earner.py --dry-run >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "  PID: $(cat "$PID_FILE") | 日志: $LOG_FILE"
}

do_start_live() {
    activate_venv
    echo "▶ 启动正式运行..."
    nohup python3 binance_earner.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "  PID: $(cat "$PID_FILE") | 日志: $LOG_FILE"
}

do_start_dashboard() {
    activate_venv
    echo "▶ 启动 Dashboard..."
    nohup python3 dashboard.py >> "$DASH_LOG_FILE" 2>&1 &
    echo $! > "$DASH_PID_FILE"
    echo "  PID: $(cat "$DASH_PID_FILE") | 日志: $DASH_LOG_FILE"
}

do_stop() {
    echo "■ 停止所有服务..."
    for pf in "$PID_FILE" "$DASH_PID_FILE"; do
        if [ -f "$pf" ]; then
            pid=$(cat "$pf")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" && echo "  已停止 PID $pid"
            fi
            rm -f "$pf"
        fi
    done
    # 清理残留进程
    pkill -f "binance_earner.py" 2>/dev/null && echo "  清理 earner 残留进程" || true
    pkill -f "dashboard.py" 2>/dev/null && echo "  清理 dashboard 残留进程" || true
    fuser -k 8080/tcp 2>/dev/null || true
}

do_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -50 "$LOG_FILE"
    else
        echo "暂无日志"
    fi
}

# ── 主菜单 ────────────────────────────────────
mkdir -p logs

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║     BinanceEarn 自动理财系统          ║"
echo "╚═══════════════════════════════════════╝"

print_status

echo ""
echo "  1) Dry-Run（只查询不操作）"
echo "  2) Dry-Run + Dashboard"
echo "  3) 正式运行（LIVE）"
echo "  4) 正式运行 + Dashboard"
echo "  D) 仅启动 Dashboard"
echo "  L) 查看日志（最近 50 行）"
echo "  0) 停止所有服务"
echo "  q) 退出"
echo ""
read -rp "请选择 > " choice

case "$choice" in
    1)  do_stop; do_start_dryrun ;;
    2)  do_stop; do_start_dryrun; do_start_dashboard ;;
    3)  do_stop; do_start_live ;;
    4)  do_stop; do_start_live; do_start_dashboard ;;
    D|d) do_start_dashboard ;;
    L|l) do_logs ;;
    0)  do_stop ;;
    q|Q) echo "Bye!" ;;
    *)  echo "无效选择" ;;
esac

print_status
