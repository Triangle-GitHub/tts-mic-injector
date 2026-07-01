# -*- coding: utf-8 -*-
"""
配置中心 — TTS Mic Injector

所有可配置项均从 config.json 读取，缺失时使用下方硬编码默认值。
导出的模块级常量供各模块直接 import 使用。
"""

import os
import json
import logging
from pathlib import Path

ALIYUN_CONFIG_PATH = Path(__file__).parent / "config.json"

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
    },
    "ui": {
        "window_title": "TTS Mic Injector",
        "window_geometry": "720x620",
        "window_minsize": [600, 520],
        "input_font": ["Microsoft YaHei", 11],
        "input_height": 3,
        "log_font": ["Consolas", 9],
        "log_height": 8,
        "log_max_lines": 200,
        "history_height": 5,
        "speed_scale_length": 180,
        "volume_scale_length": 100,
        "pitch_scale_length": 250,
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

# ── UI 外观 ──
WINDOW_TITLE = _cfg["ui"]["window_title"]
WINDOW_GEOMETRY = _cfg["ui"]["window_geometry"]
WINDOW_MINSIZE = tuple(_cfg["ui"]["window_minsize"])
INPUT_FONT = tuple(_cfg["ui"]["input_font"])
INPUT_HEIGHT = _cfg["ui"]["input_height"]
LOG_FONT = tuple(_cfg["ui"]["log_font"])
LOG_HEIGHT = _cfg["ui"]["log_height"]
LOG_MAX_LINES = _cfg["ui"]["log_max_lines"]
HISTORY_HEIGHT = _cfg["ui"]["history_height"]
SPEED_SCALE_LENGTH = _cfg["ui"]["speed_scale_length"]
VOLUME_SCALE_LENGTH = _cfg["ui"]["volume_scale_length"]
PITCH_SCALE_LENGTH = _cfg["ui"]["pitch_scale_length"]

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
