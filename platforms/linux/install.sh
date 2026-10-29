#!/usr/bin/env bash
# TTS Mic Injector —— Linux（PipeWire）安装器
#
# 幂等，可反复跑。只动用户级文件，不需要 sudo。
#
#   bash platforms/linux/install.sh            # 装：虚拟麦克风 + .desktop + 图标 + venv
#   bash platforms/linux/install.sh --check    # 只体检，不写任何东西
#   bash platforms/linux/install.sh --uninstall
#
# 装了什么：
#   1. ~/.config/pipewire/pipewire.conf.d/51-tts-mic.conf   ← 虚拟麦克风 TTS Mic（常驻）
#   2. <repo>/.venv                                       ← PyQt5 + PyQt-Fluent-Widgets + edge-tts
#   3. ~/.local/libexec/tts-mic-injector                   ← 指向 platforms/linux/launch.sh
#   4. ~/.local/share/applications/tts-mic-injector.desktop
#   5. ~/.local/share/icons/hicolor/scalable/apps/tts-mic-injector.svg
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=$(cd "$SCRIPT_DIR/../.." && pwd)

PW_CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/pipewire/pipewire.conf.d"
PW_CONF="$PW_CONF_DIR/51-tts-mic.conf"
BIN="$HOME/.local/libexec/tts-mic-injector"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP="$DESKTOP_DIR/tts-mic-injector.desktop"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
ICON="$ICON_DIR/tts-mic-injector.svg"

SINK=ttsmic_out
SOURCE=ttsmic_src

ok()   { printf '  \033[32m✅\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m❌\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠\033[0m  %s\n' "$*"; }
info() { printf '  →  %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

check() {
    echo "体检（只读）"
    local failed=0

    if have pactl && have pacat; then
        ok "pactl / pacat 就位（$(pactl --version | head -1)）"
    else
        bad "缺 pactl/pacat —— 需要 pulseaudio-utils（Fedora: sudo dnf install pulseaudio-utils）"
        failed=1
    fi

    if have pactl && pactl list short sinks | grep -q "^[0-9]*	$SINK	"; then
        ok "虚拟麦克风 sink 在：$SINK"
    else
        bad "虚拟麦克风 sink 不在：$SINK（跑一次本脚本不带参数即可安装）"
        failed=1
    fi

    if have pactl && pactl list short sources | grep -q "	$SOURCE	"; then
        ok "虚拟麦克风 source 在：$SOURCE（任何程序里选麦克风 → TTS Mic）"
    else
        bad "虚拟麦克风 source 不在：$SOURCE"
        failed=1
    fi

    if [ -x "$APP_DIR/.venv/bin/python" ]; then
        ok "venv 就位：$APP_DIR/.venv"
    else
        warn "venv 缺失：$APP_DIR/.venv（安装时会建）"
    fi

    if have espeak-ng; then
        ok "espeak-ng 就位（Linux 默认引擎）"
    else
        warn "缺 espeak-ng（Fedora: sudo dnf install espeak-ng）—— 也可以改用 Edge 引擎（需联网）"
    fi

    if [ -f "$DESKTOP" ]; then
        ok "桌面入口：$DESKTOP"
    else
        warn "桌面入口未装：$DESKTOP"
    fi

    if [ -f "$HOME/.local/share/icons/hicolor/index.theme" ]; then
        ok "图标主题索引在（index.theme；缺了它启动器只会显示通用图标）"
    else
        bad "缺 ~/.local/share/icons/hicolor/index.theme —— 启动器会显示通用图标"
        failed=1
    fi

    hits=$(ls "$HOME"/.local/share/icons/hicolor/*/apps/tts-mic-injector.* 2>/dev/null | wc -l)
    if [ "$hits" -gt 0 ]; then
        ok "图标文件 $hits 份（含多尺寸位图）"
    else
        bad "找不到 tts-mic-injector 图标文件"
        failed=1
    fi

    return $failed
}

install_all() {
    echo "安装 TTS Mic Injector（Linux / PipeWire）"

    # 0) 依赖工具
    if ! have pactl || ! have pacat; then
        bad "缺 pactl/pacat，无法继续。Fedora: sudo dnf install pulseaudio-utils"
        exit 1
    fi

    # 1) 虚拟麦克风 drop-in
    mkdir -p "$PW_CONF_DIR"
    if [ -f "$PW_CONF" ] && cmp -s "$SCRIPT_DIR/51-tts-mic.conf" "$PW_CONF"; then
        ok "虚拟麦克风配置已是最新：$PW_CONF"
    else
        install -m 644 "$SCRIPT_DIR/51-tts-mic.conf" "$PW_CONF"
        ok "已装虚拟麦克风配置：$PW_CONF"
        info "重启 pipewire 生效（会打断正在播放的声音几秒）"
        systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || \
            warn "重启 pipewire 失败（非 systemd 环境？），请手动重启"
        sleep 2
    fi

    if pactl list short sources | grep -q "	$SOURCE	"; then
        ok "虚拟麦克风已出现：$SOURCE（显示名 TTS Mic）"
    else
        bad "虚拟麦克风没出现，请检查 pipewire 日志；退路见 README 的 pulse 模块方案"
    fi

    # 2) venv
    if [ -x "$APP_DIR/.venv/bin/python" ]; then
        ok "venv 已存在：$APP_DIR/.venv"
    else
        info "创建 venv 并安装依赖（PyQt5 / PyQt-Fluent-Widgets / edge-tts）"
        python3 -m venv "$APP_DIR/.venv"
        "$APP_DIR/.venv/bin/pip" -q install --upgrade pip
        "$APP_DIR/.venv/bin/pip" -q install -r "$APP_DIR/requirements.txt"
        if [ -z "${TTS_MIC_SKIP_EDGE:-}" ]; then
            "$APP_DIR/.venv/bin/pip" -q install edge-tts || \
                warn "edge-tts 装失败（不吃紧，eSpeak 引擎不需要它）"
        fi
        ok "venv 就绪"
    fi

    # 3) 启动器 wrapper
    mkdir -p "$(dirname "$BIN")"
    cat > "$BIN" <<WRAPPER
#!/usr/bin/env bash
# 由 $APP_DIR/platforms/linux/install.sh 生成，指向仓库里的 launch.sh
exec "$APP_DIR/platforms/linux/launch.sh" "\$@"
WRAPPER
    chmod +x "$BIN"
    ok "启动器：$BIN"

    # 4) 图标
    #  ⚠️ ~/.local/share/icons/hicolor 里只有图标、没有 index.theme 时，KDE/GTK 会把这个
    #     目录当成无效主题，图标查找静默失败 —— 表现就是「启动器里只有通用图标」。
    HICOLOR="$HOME/.local/share/icons/hicolor"
    mkdir -p "$HICOLOR"
    if [ ! -f "$HICOLOR/index.theme" ]; then
        if [ -f /usr/share/icons/hicolor/index.theme ]; then
            install -m 644 /usr/share/icons/hicolor/index.theme "$HICOLOR/index.theme"
            ok "补上 index.theme（之前缺，图标会被静默忽略）"
        else
            warn "系统里找不到 /usr/share/icons/hicolor/index.theme，图标可能仍显示不出来"
        fi
    else
        ok "index.theme 已存在"
    fi

    mkdir -p "$ICON_DIR"
    install -m 644 "$SCRIPT_DIR/tts-mic-injector.svg" "$ICON"
    ok "图标（矢量）：$ICON"

    # 位图尺寸：菜单小图标更锐利，也照顾只读固定尺寸的实现
    png_tool=""
    have rsvg-convert && png_tool=rsvg
    [ -z "$png_tool" ] && have magick && png_tool=magick
    [ -z "$png_tool" ] && have convert && png_tool=convert
    if [ -n "$png_tool" ]; then
        for s in 16 24 32 48 64 128 256 512; do
            mkdir -p "$HICOLOR/${s}x${s}/apps"
            case "$png_tool" in
                rsvg)  rsvg-convert -w "$s" -h "$s" "$SCRIPT_DIR/tts-mic-injector.svg" \
                           -o "$HICOLOR/${s}x${s}/apps/tts-mic-injector.png" ;;
                magick) magick -background none -density 1024 "$SCRIPT_DIR/tts-mic-injector.svg" \
                           -resize "${s}x${s}" "$HICOLOR/${s}x${s}/apps/tts-mic-injector.png" ;;
                convert) convert -background none -density 1024 "$SCRIPT_DIR/tts-mic-injector.svg" \
                           -resize "${s}x${s}" "$HICOLOR/${s}x${s}/apps/tts-mic-injector.png" ;;
            esac
        done
        ok "图标（位图）：16/24/32/48/64/128/256/512 共 8 份（$png_tool）"
    else
        warn "没有 rsvg-convert / magick，只装矢量图标（通常够用）"
    fi

    # 5) .desktop
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP" <<DESKTOPEOF
[Desktop Entry]
Type=Application
Name=TTS Mic Injector
Name[zh_CN]=TTS 虚拟麦克风
GenericName=Text to Virtual Microphone
Comment=把输入的文字合成语音并注入虚拟麦克风 TTS Mic（PipeWire / pacat）
Comment[en]=Type text, speak it through the virtual microphone "TTS Mic"
Exec=$BIN
TryExec=$BIN
Icon=tts-mic-injector
Terminal=false
Categories=AudioVideo;Audio;
Keywords=tts;text-to-speech;mic;microphone;voice;虚拟麦克风;语音;朗读;文字转语音;
StartupNotify=true
StartupWMClass=TTSMicInjector
DESKTOPEOF
    ok "桌面入口：$DESKTOP"

    have desktop-file-validate >/dev/null 2>&1 && \
        desktop-file-validate "$DESKTOP" && ok "desktop-file-validate 通过"
    have update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    have gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    have kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 --noincremental 2>/dev/null || true

    echo
    echo "装完了。用法：应用菜单里打开「TTS 虚拟麦克风」，"
    echo "然后在目标程序（浏览器 / Discord / Element / OBS）里把麦克风选成「TTS Mic」。"
    echo "自检：bash $SCRIPT_DIR/install.sh --check"
}

uninstall_all() {
    echo "卸载 TTS Mic Injector 的桌面侧（仓库和 venv 保留）"
    rm -f "$DESKTOP" && ok "已删 $DESKTOP"
    rm -f "$BIN" && ok "已删 $BIN"
    rm -f "$ICON" && ok "已删 $ICON"
    for s in 16 24 32 48 64 128 256 512; do
        rm -f "$HOME/.local/share/icons/hicolor/${s}x${s}/apps/tts-mic-injector.png"
    done
    ok "已删各尺寸位图图标"
    info "index.theme 保留（删掉会连累其它程序找图标）"
    if [ -f "$PW_CONF" ]; then
        rm -f "$PW_CONF" && ok "已删 $PW_CONF"
        systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || \
            warn "重启 pipewire 失败，请手动重启"
        sleep 1
    fi
    have update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    echo
    echo "仓库本体和 .venv 没删（$APP_DIR）；要清掉自己 rm -rf。"
}

case "${1:-}" in
    --check) check ;;
    --uninstall) uninstall_all ;;
    "") install_all ;;
    *) echo "用法: $0 [--check|--uninstall]" >&2; exit 2 ;;
esac
