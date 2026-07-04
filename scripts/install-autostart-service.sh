#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-icloud-hme.service}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn)}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5050}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"

if [ -n "${PYTHON_BIN:-}" ]; then
  :
elif [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  PYTHON_BIN="$(command -v python3)"
fi

if [ ! -f "${ROOT_DIR}/web_ui.py" ]; then
  echo "web_ui.py 不存在：${ROOT_DIR}" >&2
  exit 1
fi

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "Python 不可执行：${PYTHON_BIN}" >&2
  exit 1
fi

write_unit() {
  cat <<EOF
[Unit]
Description=iCloud HME Web UI
Wants=network-online.target
After=network-online.target
RequiresMountsFor=${ROOT_DIR}
StartLimitIntervalSec=0

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${ROOT_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${ROOT_DIR}
Environment=HOST=${HOST}
Environment=PORT=${PORT}
Environment=AUTO_START_SCHEDULER=1
ExecStart=${PYTHON_BIN} -u ${ROOT_DIR}/web_ui.py --scheduler --no-sync
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

if [ "${DRY_RUN:-0}" = "1" ] || [ "${PRINT_UNIT:-0}" = "1" ]; then
  write_unit
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl 不可用：当前系统无法安装 systemd 开机自启服务" >&2
  exit 1
fi

SYSTEMD_STATE="$(systemctl is-system-running 2>/dev/null || true)"
case "$SYSTEMD_STATE" in
  running|degraded) ;;
  *)
    echo "systemd 未运行：${SYSTEMD_STATE:-unknown}。请先启用系统 systemd，再安装开机自启服务。" >&2
    exit 1
    ;;
esac

TMP_UNIT="$(mktemp)"
trap 'rm -f "$TMP_UNIT"' EXIT

write_unit > "$TMP_UNIT"
sudo install -m 0644 "$TMP_UNIT" "$UNIT_PATH"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME"
