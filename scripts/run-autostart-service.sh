#!/usr/bin/env sh
set -eu

SCRIPT_PATH="$0"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5050}"
LOG_DIR="${ROOT_DIR}/logs"
LOG_FILE="${WEB_UI_AUTOSTART_LOG:-${LOG_DIR}/web_ui.autostart.log}"
PID_FILE="${WEB_UI_AUTOSTART_PID:-${LOG_DIR}/web_ui.autostart.pid}"
LOCK_FILE="${WEB_UI_AUTOSTART_LOCK:-${LOG_DIR}/web_ui.autostart.lock}"
WAIT_MOUNTS_SECONDS="${WAIT_MOUNTS_SECONDS:-120}"
AUTOSTART_USER="${AUTOSTART_USER:-mci777}"

# --detach: 给 WSL boot.command 用（在发行版 boot 上下文中 nohup 可存活）
# 默认前台：给 Windows 计划任务用（必须保持 wsl.exe 会话不退出）
MODE="foreground"
for arg in "$@"; do
  case "$arg" in
    --detach) MODE="detach" ;;
    --foreground) MODE="foreground" ;;
  esac
done

# WSL boot.command 以 root 运行；切到业务用户再启动
if [ "$(id -u)" -eq 0 ] && [ "${AUTOSTART_AS_USER:-0}" != "1" ]; then
  if id "$AUTOSTART_USER" >/dev/null 2>&1 && command -v runuser >/dev/null 2>&1; then
    exec runuser -u "$AUTOSTART_USER" -- env \
      AUTOSTART_AS_USER=1 \
      HOST="$HOST" \
      PORT="$PORT" \
      PYTHON_BIN="${PYTHON_BIN:-}" \
      WEB_UI_AUTOSTART_LOG="${WEB_UI_AUTOSTART_LOG:-}" \
      WEB_UI_AUTOSTART_PID="${WEB_UI_AUTOSTART_PID:-}" \
      WEB_UI_AUTOSTART_LOCK="${WEB_UI_AUTOSTART_LOCK:-}" \
      WAIT_MOUNTS_SECONDS="$WAIT_MOUNTS_SECONDS" \
      /bin/sh "$SCRIPT_PATH" "$@"
  fi
fi

if [ "$MODE" = "detach" ]; then
  mkdir -p "$LOG_DIR"
  nohup /bin/sh "$SCRIPT_PATH" --foreground >>"$LOG_FILE" 2>&1 < /dev/null &
  child=$!
  echo "$child" >"${PID_FILE}.launcher"
  # 给子进程一点时间抢锁/写日志，便于 boot 脚本判断
  sleep 1
  exit 0
fi

if [ -n "${PYTHON_BIN:-}" ]; then
  :
elif [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  PYTHON_BIN="$(command -v python3)"
fi

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "Python 不可执行：${PYTHON_BIN}" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

printf '\n[%s] starting iCloud HME Web UI autostart (pid=%s user=%s mode=%s)\n' \
  "$(date '+%Y-%m-%d %H:%M:%S')" "$$" "$(id -un 2>/dev/null || echo unknown)" "$MODE"

# 项目在 /mnt/x 时，登录后盘符可能晚挂载
waited=0
while [ ! -f "${ROOT_DIR}/web_ui.py" ]; do
  if [ "$waited" -ge "$WAIT_MOUNTS_SECONDS" ]; then
    echo "等待项目目录超时：${ROOT_DIR}/web_ui.py"
    exit 1
  fi
  sleep 2
  waited=$((waited + 2))
done
if [ "$waited" -gt 0 ]; then
  echo "项目目录就绪，等待 ${waited}s"
fi

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "已有自启进程在运行，跳过重复启动"
    # 前台模式若已有实例，保持会话存活，避免 wsl 会话退出影响其他服务
    if [ "$MODE" = "foreground" ]; then
      while kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; do
        sleep 30
      done
    fi
    exit 0
  fi
fi

# 若已在监听则跳过真正启动；前台模式仍挂起保持 wsl 会话
port_in_use=0
if command -v ss >/dev/null 2>&1; then
  if ss -lnt 2>/dev/null | awk '{print $4}' | grep -E "(:|\\.)${PORT}$" >/dev/null 2>&1; then
    port_in_use=1
  fi
fi
if [ "$port_in_use" -eq 1 ]; then
  echo "端口 ${PORT} 已在监听，跳过重复启动"
  if [ "$MODE" = "foreground" ]; then
    while ss -lnt 2>/dev/null | awk '{print $4}' | grep -E "(:|\\.)${PORT}$" >/dev/null 2>&1; do
      sleep 30
    done
    echo "端口 ${PORT} 已不再监听，结束保持会话"
  fi
  exit 0
fi

echo $$ >"$PID_FILE"
cleanup() {
  code=$?
  printf '[%s] autostart exited code=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$code"
  if [ -f "$PID_FILE" ]; then
    current="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ "$current" = "$$" ]; then
      rm -f "$PID_FILE"
    fi
  fi
}
trap cleanup EXIT INT TERM

export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT_DIR"
export HOST
export PORT
export AUTO_START_SCHEDULER=1
cd "$ROOT_DIR"
echo "python=${PYTHON_BIN} host=${HOST} port=${PORT}"
exec "$PYTHON_BIN" -u "$ROOT_DIR/web_ui.py" --scheduler --no-sync
