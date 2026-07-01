# -*- coding: utf-8 -*-
"""测试 config.py — 配置常量和 config.json 加载。"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ESPEAK_PATH, SPEED_DEFAULT, SPEED_MIN, SPEED_MAX, VOLUME_MAX,
    VB_CABLE_KEYWORDS, LOG_MAX_LINES,
    EDGE_DEFAULT_VOICE, PIPER_PATH, PIPER_MODEL_DIR,
    FFMPEG_PATH, EDGE_PITCH_MIN, EDGE_PITCH_MAX,
    ESPEAK_SYNTH_TIMEOUT, SAPI5_VOICES_TIMEOUT, SAPI5_SYNTH_TIMEOUT,
    PIPER_SYNTH_TIMEOUT, ALIYUN_SYNTH_TIMEOUT,
    VOLUME_DEFAULT, PITCH_DEFAULT, MONITOR_ENABLED_DEFAULT,
    PIPER_LENGTH_SCALE_MIN, PIPER_LENGTH_SCALE_MAX,
    WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_MINSIZE,
    INPUT_FONT, INPUT_HEIGHT, LOG_FONT, LOG_HEIGHT,
    HISTORY_HEIGHT, SPEED_SCALE_LENGTH, VOLUME_SCALE_LENGTH, PITCH_SCALE_LENGTH,
    ALIYUN_API_KEY, ALIYUN_MODEL, ALIYUN_VOICE,
    load_aliyun_config, ALIYUN_CONFIG_PATH,
)


class TestConstants(unittest.TestCase):
    """验证所有常量值不变。"""

    def test_path_constants(self):
        self.assertEqual(ESPEAK_PATH, "espeak-ng.exe")
        self.assertEqual(PIPER_PATH, "piper.exe")
        self.assertEqual(PIPER_MODEL_DIR, "piper_models")
        self.assertEqual(EDGE_DEFAULT_VOICE, "zh-CN-YunxiNeural")

    def test_numeric_constants(self):
        self.assertEqual(SPEED_DEFAULT, 175)
        self.assertEqual(SPEED_MIN, 80)
        self.assertEqual(SPEED_MAX, 450)
        self.assertEqual(LOG_MAX_LINES, 200)
        self.assertAlmostEqual(VOLUME_MAX, 1.0)

    def test_vb_cable_keywords(self):
        self.assertEqual(VB_CABLE_KEYWORDS, ["CABLE Input"])

    def test_aliyun_config_path(self):
        self.assertTrue(ALIYUN_CONFIG_PATH.name == "config.json")


class TestNewConfigConstants(unittest.TestCase):
    """验证新增的可配置常量都有合理默认值。"""

    def test_ffmpeg_path(self):
        self.assertIsInstance(FFMPEG_PATH, str)
        self.assertTrue(len(FFMPEG_PATH) > 0)

    def test_edge_pitch_range(self):
        self.assertLess(EDGE_PITCH_MIN, EDGE_PITCH_MAX)

    def test_timeouts_positive(self):
        for val in [ESPEAK_SYNTH_TIMEOUT, SAPI5_VOICES_TIMEOUT,
                     SAPI5_SYNTH_TIMEOUT, PIPER_SYNTH_TIMEOUT,
                     ALIYUN_SYNTH_TIMEOUT]:
            self.assertGreater(val, 0)

    def test_defaults_in_range(self):
        self.assertGreaterEqual(VOLUME_DEFAULT, 0)
        self.assertLessEqual(VOLUME_DEFAULT, 100)
        self.assertGreaterEqual(PIPER_LENGTH_SCALE_MIN, 0.01)
        self.assertLess(PIPER_LENGTH_SCALE_MIN, PIPER_LENGTH_SCALE_MAX)

    def test_ui_constants(self):
        self.assertIsInstance(WINDOW_TITLE, str)
        self.assertGreater(WINDOW_WIDTH, 0)
        self.assertGreater(WINDOW_HEIGHT, 0)
        self.assertEqual(len(WINDOW_MINSIZE), 2)
        self.assertEqual(len(INPUT_FONT), 2)
        self.assertGreater(INPUT_HEIGHT, 0)
        self.assertGreater(LOG_HEIGHT, 0)
        self.assertGreater(LOG_MAX_LINES, 0)
        self.assertGreater(HISTORY_HEIGHT, 0)
        self.assertGreater(SPEED_SCALE_LENGTH, 0)
        self.assertGreater(VOLUME_SCALE_LENGTH, 0)

    def test_aliyun_defaults(self):
        self.assertIsInstance(ALIYUN_MODEL, str)
        self.assertIsInstance(ALIYUN_VOICE, str)

    def test_monitor_enabled_default_is_bool(self):
        self.assertIsInstance(MONITOR_ENABLED_DEFAULT, bool)


class TestDeepMerge(unittest.TestCase):
    """_deep_merge 递归合并。"""

    def test_flat_override(self):
        from config import _deep_merge
        base = {"a": 1, "b": 2}
        override = {"a": 99}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": 99, "b": 2})

    def test_nested_override(self):
        from config import _deep_merge
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"x": 99}}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": {"x": 99, "y": 2}, "b": 3})

    def test_new_key_added(self):
        from config import _deep_merge
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_empty_override(self):
        from config import _deep_merge
        base = {"a": 1}
        result = _deep_merge(base, {})
        self.assertEqual(result, {"a": 1})

    def test_list_not_merged(self):
        from config import _deep_merge
        base = {"k": [1, 2, 3]}
        override = {"k": [4]}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"k": [4]})

    def test_deeply_nested(self):
        from config import _deep_merge
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": {"b": {"c": 99, "d": 2}}})


class TestLoadAliyunConfig(unittest.TestCase):
    """测试 load_aliyun_config()。"""

    def test_file_not_exists_returns_empty(self):
        with patch.object(Path, "exists", return_value=False):
            result = load_aliyun_config()
            self.assertEqual(result, {})

    def test_file_exists_with_valid_json(self):
        data = {"aliyun": {"api_key": "sk-test", "model": "custom-model", "voice": "Cherry"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            tmp_path = f.name
        try:
            with patch("config.ALIYUN_CONFIG_PATH", Path(tmp_path)):
                result = load_aliyun_config()
                self.assertEqual(result, data["aliyun"])
        finally:
            os.unlink(tmp_path)

    def test_file_exists_but_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("not valid json{{{")
            tmp_path = f.name
        try:
            with patch("config.ALIYUN_CONFIG_PATH", Path(tmp_path)):
                result = load_aliyun_config()
                self.assertEqual(result, {})
        finally:
            os.unlink(tmp_path)

    def test_file_exists_empty_returns_empty_dict(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({}, f)
            tmp_path = f.name
        try:
            with patch("config.ALIYUN_CONFIG_PATH", Path(tmp_path)):
                result = load_aliyun_config()
                self.assertEqual(result, {})
        finally:
            os.unlink(tmp_path)
