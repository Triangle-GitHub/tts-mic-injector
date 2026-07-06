# -*- coding: utf-8 -*-
"""
QConfig 配置中心 — 替代旧的 config.py 手动 JSON 加载。
使用 qfluentwidgets 的 QConfig 实现自动持久化和变更通知。
"""

import json
from pathlib import Path

from config import _get_app_dir

from qfluentwidgets import (
    QConfig, qconfig, OptionsConfigItem, OptionsValidator,
    ConfigSerializer, ConfigValidator, ConfigItem, Theme,
)


class PassValidator(ConfigValidator):
    """不做校验，直接通过。"""
    def validate(self, value):
        return True

    def correct(self, value):
        return value


class StringValidator(ConfigValidator):
    def validate(self, value):
        return isinstance(value, str)

    def correct(self, value):
        if value is None:
            return ""
        return str(value)


class IntValidator(ConfigValidator):
    def __init__(self, default=0):
        super().__init__()
        self.default = default

    def validate(self, value):
        return isinstance(value, int)

    def correct(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return self.default


class ListValidator(ConfigValidator):
    def validate(self, value):
        return isinstance(value, list)

    def correct(self, value):
        if isinstance(value, list):
            return value
        return []


class StringSerializer(ConfigSerializer):
    def serialize(self, value):
        return str(value) if value is not None else ""

    def deserialize(self, value):
        return str(value) if value is not None else ""


class IntSerializer(ConfigSerializer):
    def serialize(self, value):
        return int(value)

    def deserialize(self, value):
        return int(value)


class BooleanSerializer(ConfigSerializer):
    def serialize(self, value):
        return bool(value)

    def deserialize(self, value):
        return bool(value)


class PassSerializer(ConfigSerializer):
    def serialize(self, value):
        return value

    def deserialize(self, value):
        return value


CONFIG_JSON_PATH = _get_app_dir() / "config.json"
QCONFIG_PATH = _get_app_dir() / "qt_config.json"


class Config(QConfig):
    """TTS Mic Injector 配置。"""

    # ── 阿里云 ──
    aliyunApiKey = ConfigItem("aliyun", "api_key", "", StringValidator(), StringSerializer())
    aliyunModel = ConfigItem("aliyun", "model", "qwen3-tts-flash-realtime", StringValidator(), StringSerializer())
    aliyunVoice = ConfigItem("aliyun", "voice", "Ethan", StringValidator(), StringSerializer())

    # ── 路径 ──
    espeakPath = ConfigItem("paths", "espeak", "espeak-ng.exe", StringValidator(), StringSerializer())
    piperPath = ConfigItem("paths", "piper", "piper.exe", StringValidator(), StringSerializer())
    piperModels = ConfigItem("paths", "piper_models", "piper_models", StringValidator(), StringSerializer())
    ffmpegPath = ConfigItem("paths", "ffmpeg", "ffmpeg", StringValidator(), StringSerializer())

    # ── VB-Cable ──
    vbCableKeywords = ConfigItem("vb_cable", "keywords", ["CABLE Input"], ListValidator(), PassSerializer())

    # ── Edge ──
    edgeDefaultVoice = ConfigItem("edge", "default_voice", "zh-CN-YunxiNeural", StringValidator(), StringSerializer())

    # ── 超时 ──
    espeakSynthTimeout = ConfigItem("timeouts", "espeak_synth", 10, IntValidator(10), IntSerializer())
    sapi5VoicesTimeout = ConfigItem("timeouts", "sapi5_voices", 10, IntValidator(10), IntSerializer())
    sapi5SynthTimeout = ConfigItem("timeouts", "sapi5_synth", 60, IntValidator(60), IntSerializer())
    piperSynthTimeout = ConfigItem("timeouts", "piper_synth", 60, IntValidator(60), IntSerializer())
    aliyunSynthTimeout = ConfigItem("timeouts", "aliyun_synth", 120, IntValidator(120), IntSerializer())

    # ── 默认值 ──
    volumeDefault = ConfigItem("defaults", "volume", 100, IntValidator(200), IntSerializer())
    monitorEnabledDefault = OptionsConfigItem(
        "defaults", "monitor_enabled", True,
        OptionsValidator([True, False]), BooleanSerializer()
    )
    disableLogFile = OptionsConfigItem(
        "defaults", "disable_log_file", False,
        OptionsValidator([True, False]), BooleanSerializer()
    )

    # ── UI ──
    windowTitle = ConfigItem("ui", "window_title", "TTS Mic Injector", StringValidator(), StringSerializer())
    windowGeometry = ConfigItem("ui", "window_geometry", "720x620", StringValidator(), StringSerializer())
    windowMinsizeW = ConfigItem("ui", "window_minsize_w", 600, IntValidator(600), IntSerializer())
    windowMinsizeH = ConfigItem("ui", "window_minsize_h", 520, IntValidator(520), IntSerializer())
    inputHeight = ConfigItem("ui", "input_height", 3, IntValidator(3), IntSerializer())
    logHeight = ConfigItem("ui", "log_height", 8, IntValidator(8), IntSerializer())
    logMaxLines = ConfigItem("ui", "log_max_lines", 200, IntValidator(200), IntSerializer())
    historyHeight = ConfigItem("ui", "history_height", 5, IntValidator(5), IntSerializer())


def _load_config_json_to_qconfig():
    """从旧 config.json 迁移配置到 QConfig。"""
    if not CONFIG_JSON_PATH.exists():
        return
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    sections = [
        "aliyun", "paths", "vb_cable", "edge",
        "timeouts", "defaults", "ui"
    ]
    for section in sections:
        sd = data.get(section, {})
        if not isinstance(sd, dict):
            continue
        for key, value in sd.items():
            if isinstance(value, (list, dict)):
                continue
            qkey = f"{section}/{key}"
            try:
                cfg.set(qkey, value)
            except Exception:
                pass


cfg = Config()
cfg.themeMode.value = Theme.AUTO
cfg.themeColor.value = "#ff5d74a2"
qconfig.load(str(QCONFIG_PATH), cfg)

_load_config_json_to_qconfig()
