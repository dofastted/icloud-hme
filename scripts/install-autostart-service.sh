#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-icloud-hme.service}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn)}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5050}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"
AUTOSTART_MODE="${AUTOSTART_MODE:-auto}"
TASK_NAME="${TASK_NAME:-iCloud HME Web UI}"
TASK_TRIGGER="${TASK_TRIGGER:-ONLOGON}"
LAUNCHER_PATH="${ROOT_DIR}/scripts/run-autostart-service.sh"
WSL_EXE="${WSL_EXE:-C:\Windows\System32\wsl.exe}"

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

if [ ! -f "${LAUNCHER_PATH}" ]; then
  echo "启动脚本不存在：${LAUNCHER_PATH}" >&2
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

systemd_state() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "missing"
    return 0
  fi
  systemctl is-system-running 2>/dev/null || true
}

systemd_available() {
  state="$(systemd_state)"
  case "$state" in
    running|degraded) return 0 ;;
    *) return 1 ;;
  esac
}

is_wsl() {
  [ -n "${WSL_DISTRO_NAME:-}" ] && return 0
  if [ -r /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
    return 0
  fi
  return 1
}

has_windows_task_unsafe_chars() {
  case "$1" in
    *[[:space:]]*|*\"*|*"'"*) return 0 ;;
    *) return 1 ;;
  esac
}

validate_windows_task_value() {
  name="$1"
  value="$2"
  if has_windows_task_unsafe_chars "$value"; then
    echo "${name} 不能包含空格或引号：${value}" >&2
    exit 1
  fi
}

windows_task_action() {
  distro="$1"
  printf '%s -d %s --exec /bin/sh %s\n' "$WSL_EXE" "$distro" "$LAUNCHER_PATH"
}

write_windows_task_plan() {
  distro="${WSL_DISTRO_NAME:-}"
  if [ -z "$distro" ]; then
    distro="<WSL_DISTRO_NAME>"
  fi
  action="$(windows_task_action "$distro")"
  cat <<EOF
# Windows Task Scheduler
TaskName=${TASK_NAME}
Trigger=${TASK_TRIGGER}
Action=${action}
Scheduler=web_ui.py --scheduler --no-sync
Environment=AUTO_START_SCHEDULER=1
EOF
}

install_systemd_service() {
  if ! systemd_available; then
    state="$(systemd_state)"
    echo "systemd 未运行：${state:-unknown}。当前环境不能安装 systemd 开机自启服务。" >&2
    exit 1
  fi

  tmp_unit="$(mktemp)"
  trap 'rm -f "$tmp_unit"' EXIT

  write_unit > "$tmp_unit"
  sudo install -m 0644 "$tmp_unit" "$UNIT_PATH"
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  sudo systemctl --no-pager --full status "$SERVICE_NAME"
}

harden_windows_task_settings() {
  # schtasks 默认：电池停跑、72h 杀进程、失败不重试。这里改成长期后台友好设置。
  if ! command -v schtasks.exe >/dev/null 2>&1; then
    return 0
  fi
  schtasks_path="$(command -v schtasks.exe)"
  case "$schtasks_path" in
    /mnt/c/*|*/System32/*|*/SysWOW64/*) ;;
    *)
      echo "检测到非 Windows schtasks（${schtasks_path}），跳过任务设置加固。" >&2
      return 0
      ;;
  esac
  if ! command -v powershell.exe >/dev/null 2>&1; then
    echo "powershell.exe 不可用，跳过任务设置加固。" >&2
    return 0
  fi
  powershell.exe -NoProfile -Command "
\$ErrorActionPreference = 'Stop'
\$task = Get-ScheduledTask -TaskName '$TASK_NAME'
\$settings = \$task.Settings
\$settings.DisallowStartIfOnBatteries = \$false
\$settings.StopIfGoingOnBatteries = \$false
\$settings.StartWhenAvailable = \$true
\$settings.ExecutionTimeLimit = 'PT0S'
\$settings.RestartCount = 3
\$settings.RestartInterval = 'PT1M'
\$settings.AllowHardTerminate = \$true
\$settings.MultipleInstances = 'IgnoreNew'
Set-ScheduledTask -TaskName '$TASK_NAME' -Settings \$settings | Out-Null
\$trigger = \$task.Triggers | Select-Object -First 1
if (\$null -ne \$trigger) {
  try {
    \$trigger.Delay = 'PT45S'
    Set-ScheduledTask -TaskName '$TASK_NAME' -Trigger \$trigger | Out-Null
  } catch {
    # 旧系统可能不支持 Delay，忽略
  }
}
Write-Output 'task settings hardened'
" || echo "任务设置加固失败，继续使用默认设置。" >&2
}

install_windows_task() {
  if ! is_wsl; then
    echo "当前不是 WSL；不能安装 Windows 登录自启任务。" >&2
    exit 1
  fi
  if [ -z "${WSL_DISTRO_NAME:-}" ]; then
    echo "缺少 WSL_DISTRO_NAME，无法确定 Windows 任务要启动的发行版。" >&2
    exit 1
  fi
  if ! command -v schtasks.exe >/dev/null 2>&1; then
    echo "schtasks.exe 不可用：无法安装 Windows 登录自启任务。" >&2
    exit 1
  fi

  validate_windows_task_value "WSL_DISTRO_NAME" "$WSL_DISTRO_NAME"
  validate_windows_task_value "LAUNCHER_PATH" "$LAUNCHER_PATH"
  validate_windows_task_value "WSL_EXE" "$WSL_EXE"
  action="$(windows_task_action "$WSL_DISTRO_NAME")"
  # 参数不能加引号：Windows 计划任务会把引号带进 wsl 参数导致失败
  schtasks.exe /Create /F /TN "$TASK_NAME" /SC "$TASK_TRIGGER" /TR "$action"
  harden_windows_task_settings
  schtasks.exe /Run /TN "$TASK_NAME"
  write_windows_task_plan
}

if [ "${DRY_RUN:-0}" = "1" ] || [ "${PRINT_UNIT:-0}" = "1" ]; then
  case "$AUTOSTART_MODE" in
    systemd) write_unit ;;
    windows-task) write_windows_task_plan ;;
    auto)
      if systemd_available; then
        write_unit
      elif is_wsl; then
        write_windows_task_plan
      else
        write_unit
      fi
      ;;
    *) echo "未知 AUTOSTART_MODE：${AUTOSTART_MODE}" >&2; exit 1 ;;
  esac
  exit 0
fi

case "$AUTOSTART_MODE" in
  systemd) install_systemd_service ;;
  windows-task) install_windows_task ;;
  auto)
    if systemd_available; then
      install_systemd_service
    elif is_wsl; then
      echo "systemd 未运行，改用 Windows 登录自启任务。" >&2
      install_windows_task
    else
      state="$(systemd_state)"
      echo "systemd 不可用：${state:-unknown}；当前环境未提供可安装的开机自启后端。" >&2
      exit 1
    fi
    ;;
  *) echo "未知 AUTOSTART_MODE：${AUTOSTART_MODE}" >&2; exit 1 ;;
esac
