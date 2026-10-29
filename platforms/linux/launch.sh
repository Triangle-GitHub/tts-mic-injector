#!/usr/bin/env bash
# TTS Mic Injector —— Linux 运行时启动器
#
# 桌面入口（.desktop）调用本脚本；install.sh 会把一个 soft wrapper 放到
# ~/.local/libexec/tts-mic-injector 指向这里。
#
# 职责只有三件：
#   1. 确保虚拟麦克风在（常驻靠 platforms/linux/51-tts-mic.conf，这里只兜底）
#   2. 用仓库自带的 .venv 跑 app.py（没有 .venv 就明确报错，不擅自 pip install）
#   3. 日志写到 ${XDG_STATE_HOME:-~/.local/state}/tts-mic-injector/app.log
set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_DIR="${TTS_MIC_APP_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PY="$APP_DIR/.venv/bin/python"
SINK=ttsmic_out
SOURCE=ttsmic_src
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/tts-mic-injector"

if [ ! -x "$PY" ]; then
    echo "找不到 venv 解释器: $PY" >&2
    echo "先跑一次：bash \"$APP_DIR/platforms/linux/install.sh\"" >&2
    exit 1
fi

# 1) 虚拟麦克风兜底（正常情况 drop-in 配置已经把它带来了）
if command -v pactl >/dev/null 2>&1; then
    pactl list short sinks 2>/dev/null | grep -q "$SINK" || \
        pactl load-module module-null-sink "sink_name=$SINK" rate=48000 >/dev/null 2>&1 || true
    pactl list short sources 2>/dev/null | grep -q "$SOURCE" || \
        pactl load-module module-remap-source "master=$SINK.monitor" "source_name=$SOURCE" >/dev/null 2>&1 || true
fi

# 2) 跑起来
mkdir -p "$LOG_DIR"
cd "$APP_DIR" || exit 1
exec "$PY" app.py "$@" >>"$LOG_DIR/app.log" 2>&1
