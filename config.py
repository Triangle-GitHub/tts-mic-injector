# -*- coding: utf-8 -*-
"""
配置中心 — TTS Mic Injector

所有可配置项均从 config.json 读取，缺失时使用下方硬编码默认值。
导出的模块级常量供各模块直接 import 使用。
"""

import os
import sys
import json
import logging
from pathlib import Path


def _get_app_dir() -> Path:
    """获取 app 根目录。

    打包后（sys.frozen）为 exe 所在目录，源码运行时为此文件所在目录。
    用于需要可写访问的文件（config.json / qt_config.json / logs）。
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _get_data_dir() -> Path:
    """获取只读资源目录。

    打包后为 PyInstaller 的解压临时目录（sys._MEIPASS），
    源码运行时为此文件所在目录。

    用于 assets/ 等只读资源。
    """
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass)
    return Path(__file__).parent


ALIYUN_CONFIG_PATH = _get_app_dir() / "config.json"

logger = logging.getLogger("TTSMicInjector")

# ── 硬编码默认值（config.json 中缺失的字段会回退至此） ──
_DEFAULTS = {
    "aliyun": {
        "api_key": "",
        "model": "qwen3-tts-flash-realtime",
        "voice": "Ethan",
    },
    "paths": {
        "espeak": "espeak-ng.exe",
        "piper": "piper.exe",
        "piper_models": "piper_models",
        "ffmpeg": "ffmpeg",
    },
    "vb_cable": {
        "keywords": ["CABLE Input"],
    },
    "edge": {
        "default_voice": "zh-CN-YunxiNeural",
        "pitch_range": [-50, 50],
    },
    "timeouts": {
        "espeak_synth": 10,
        "sapi5_voices": 10,
        "sapi5_synth": 60,
        "piper_synth": 60,
        "aliyun_synth": 120,
    },
    "defaults": {
        "speed": 175,
        "volume": 100,
        "pitch": 0,
        "monitor_enabled": True,
        "piper_length_scale_min": 0.2,
        "piper_length_scale_max": 5.0,
        "concurrent_mode": False,
        "disable_log_file": False,
    },
    "ui": {
        "window_title": "TTS Mic Injector",
        "window_width": 800,
        "window_height": 620,
        "window_minsize": [600, 520],
        "panel_min_width": 256,
        "panel_hidden_min_width": 400,
        "input_font": ["Microsoft YaHei", 11],
        "input_height": 3,
        "log_font": ["Consolas", 9],
        "log_height": 8,
        "log_max_lines": 200,
        "history_height": 5,
        "speed_scale_length": 180,
        "volume_scale_length": 200,
        "pitch_scale_length": 250,
    },
    "theme": {
        "dark": {
            "window_bg": "#1e1e1e",
            "central_bg": "#252525",
            "chat_bg": "#2a2a2a",
            "bubble_bg": "#3a3a3a",
            "bubble_text": "rgba(255,255,255,0.90)",
            "bubble_time": "rgba(255,255,255,0.45)",
            "bubble_playing_bg": "#314a6e",
            "bubble_playing_border": "rgba(70,125,200,0.45)",
            "stop_btn_bg": "#3a3a3a",
            "stop_btn_border": "rgba(255,255,255,0.15)",
            "engine_btn_selected_bg": "rgba(95,140,200,0.35)",
            "engine_btn_selected_fg": "rgba(255,255,255,0.9)",
            "engine_btn_normal_bg": "rgba(255,255,255,0.05)",
            "engine_btn_normal_fg": "rgba(255,255,255,0.75)",
            "engine_btn_border_selected": "rgba(95,140,200,0.5)",
            "engine_btn_border_normal": "rgba(255,255,255,0.08)",
        },
        "light": {
            "window_bg": "#f5f5f5",
            "central_bg": "#ffffff",
            "chat_bg": "#fafafa",
            "bubble_bg": "#f0f0f0",
            "bubble_text": "rgba(0,0,0,0.85)",
            "bubble_time": "rgba(0,0,0,0.45)",
            "bubble_playing_bg": "#dce8f5",
            "bubble_playing_border": "rgba(70,125,200,0.35)",
            "stop_btn_bg": "#ffffff",
            "stop_btn_border": "rgba(0,0,0,0.12)",
            "engine_btn_selected_bg": "rgba(95,140,200,0.20)",
            "engine_btn_selected_fg": "#1a1a1a",
            "engine_btn_normal_bg": "rgba(0,0,0,0.03)",
            "engine_btn_normal_fg": "rgba(0,0,0,0.75)",
            "engine_btn_border_selected": "rgba(95,140,200,0.5)",
            "engine_btn_border_normal": "rgba(0,0,0,0.08)",
        },
    },
    "engine_speed_ranges": {
        "eSpeak": [80, 450],
        "SAPI5": [50, 400],
        "Piper": [50, 200],
        "Edge": [50, 200],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个 dict，override 中的值覆盖 base。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_raw_config() -> dict:
    """从 config.json 读取原始内容，失败返回 {}。"""
    try:
        if ALIYUN_CONFIG_PATH.exists():
            with open(ALIYUN_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"加载 config.json 失败: {e}")
    return {}


# ── 合并后的最终配置 ──
_cfg = _deep_merge(_DEFAULTS, _load_raw_config())

# ============================================================
#  导出的模块级常量
# ============================================================

# ── 阿里云 ──
ALIYUN_API_KEY = _cfg["aliyun"]["api_key"]
ALIYUN_MODEL = _cfg["aliyun"]["model"]
ALIYUN_VOICE = _cfg["aliyun"]["voice"]

# ── 外部程序路径 ──
ESPEAK_PATH = _cfg["paths"]["espeak"]
PIPER_PATH = _cfg["paths"]["piper"]
PIPER_MODEL_DIR = _cfg["paths"]["piper_models"]
FFMPEG_PATH = _cfg["paths"]["ffmpeg"]

# ── VB-Cable ──
VB_CABLE_KEYWORDS = _cfg["vb_cable"]["keywords"]

# ── Edge ──
EDGE_DEFAULT_VOICE = _cfg["edge"]["default_voice"]
EDGE_PITCH_MIN = _cfg["edge"]["pitch_range"][0]
EDGE_PITCH_MAX = _cfg["edge"]["pitch_range"][1]

# ── 超时 ──
ESPEAK_SYNTH_TIMEOUT = _cfg["timeouts"]["espeak_synth"]
SAPI5_VOICES_TIMEOUT = _cfg["timeouts"]["sapi5_voices"]
SAPI5_SYNTH_TIMEOUT = _cfg["timeouts"]["sapi5_synth"]
PIPER_SYNTH_TIMEOUT = _cfg["timeouts"]["piper_synth"]
ALIYUN_SYNTH_TIMEOUT = _cfg["timeouts"]["aliyun_synth"]

# ── 默认值 ──
SPEED_DEFAULT = _cfg["defaults"]["speed"]
VOLUME_DEFAULT = _cfg["defaults"]["volume"]
PITCH_DEFAULT = _cfg["defaults"]["pitch"]
MONITOR_ENABLED_DEFAULT = _cfg["defaults"]["monitor_enabled"]
PIPER_LENGTH_SCALE_MIN = _cfg["defaults"]["piper_length_scale_min"]
PIPER_LENGTH_SCALE_MAX = _cfg["defaults"]["piper_length_scale_max"]
CONCURRENT_MODE_DEFAULT = _cfg["defaults"]["concurrent_mode"]
DISABLE_LOG_FILE = _cfg["defaults"]["disable_log_file"]

# ── UI 外观 ──
WINDOW_TITLE = _cfg["ui"]["window_title"]
WINDOW_WIDTH = _cfg["ui"]["window_width"]
WINDOW_HEIGHT = _cfg["ui"]["window_height"]
WINDOW_MINSIZE = tuple(_cfg["ui"]["window_minsize"])
PANEL_MIN_WIDTH = _cfg["ui"]["panel_min_width"]
PANEL_HIDDEN_MIN_WIDTH = _cfg["ui"]["panel_hidden_min_width"]
INPUT_FONT = tuple(_cfg["ui"]["input_font"])
INPUT_HEIGHT = _cfg["ui"]["input_height"]
LOG_FONT = tuple(_cfg["ui"]["log_font"])
LOG_HEIGHT = _cfg["ui"]["log_height"]
LOG_MAX_LINES = _cfg["ui"]["log_max_lines"]
HISTORY_HEIGHT = _cfg["ui"]["history_height"]
SPEED_SCALE_LENGTH = _cfg["ui"]["speed_scale_length"]
VOLUME_SCALE_LENGTH = _cfg["ui"]["volume_scale_length"]
PITCH_SCALE_LENGTH = _cfg["ui"]["pitch_scale_length"]

# ── 引擎语速范围 ──
ENGINE_SPEED_RANGES = {k: tuple(v) for k, v in _cfg["engine_speed_ranges"].items()}

# ── 主题颜色 ──
THEME_DARK = _cfg["theme"]["dark"]
THEME_LIGHT = _cfg["theme"]["light"]


def get_theme(dark: bool) -> dict:
    """返回当前主题的颜色配置 dict。"""
    return THEME_DARK if dark else THEME_LIGHT


# ── 引擎内部硬约束（不可配置） ──
SPEED_MIN = 80
SPEED_MAX = 450
VOLUME_MAX = 1.0  # 未实际使用，保留兼容


def get_aliyun_config():
    """兼容旧接口：返回阿里云相关配置 dict。"""
    _raw = _load_raw_config()
    aliyun = _raw.get("aliyun", {}) if _raw else {}
    if not aliyun:
        aliyun = {}
    return aliyun


# 兼容旧函数名
load_aliyun_config = get_aliyun_config
