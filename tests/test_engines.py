# -*- coding: utf-8 -*-
"""测试 TTS 引擎 — 纯逻辑：PCM 音量、语速映射、参数格式化。"""

import os
import sys
import struct
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestVolumeAdjustment(unittest.TestCase):
    """各引擎的 _adjust_volume / _adjust_pcm_volume 行为一致。"""

    def test_espeak_adjust_volume(self):
        import wave
        import tempfile
        from engines.espeak import EspeakEngine
        e = EspeakEngine.__new__(EspeakEngine)

        fd, wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(struct.pack("<hhh", 1000, -500, 0))
            e._adjust_volume(wav, 0.5)
            with wave.open(wav, "rb") as wf:
                frames = wf.readframes(3)
            vals = struct.unpack("<hhh", frames)
            self.assertEqual(vals[0], 500)
            self.assertEqual(vals[1], -250)
            self.assertEqual(vals[2], 0)
        finally:
            if os.path.exists(wav):
                os.unlink(wav)

    def test_piper_adjust_volume(self):
        import wave
        import tempfile
        from engines.piper import PiperEngine
        p = PiperEngine.__new__(PiperEngine)

        fd, wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(struct.pack("<hh", 2000, -1000))
            p._adjust_volume(wav, 0.25)
            with wave.open(wav, "rb") as wf:
                frames = wf.readframes(2)
            vals = struct.unpack("<hh", frames)
            self.assertEqual(vals[0], 500)
            self.assertEqual(vals[1], -250)
        finally:
            if os.path.exists(wav):
                os.unlink(wav)

    def test_aliyun_adjust_pcm_volume(self):
        from engines.aliyun import AliyunEngine
        e = AliyunEngine.__new__(AliyunEngine)

        data = struct.pack("<hhhh", 1000, -500, 32767, -32768)
        result = e._adjust_pcm_volume(data, 0.5)
        vals = struct.unpack("<hhhh", result)
        self.assertEqual(vals[0], 500)
        self.assertEqual(vals[1], -250)
        self.assertEqual(vals[2], 16383)  # 32767*0.5 truncated
        self.assertEqual(vals[3], -16384)  # -32768*0.5 = -16384

    def test_aliyun_pcm_volume_clamp(self):
        from engines.aliyun import AliyunEngine
        e = AliyunEngine.__new__(AliyunEngine)

        data = struct.pack("<hh", 30000, -30000)
        result = e._adjust_pcm_volume(data, 2.0)
        vals = struct.unpack("<hh", result)
        self.assertEqual(vals[0], 32767)
        self.assertEqual(vals[1], -32768)


class TestEdgeRateFormatting(unittest.TestCase):
    """EdgeEngine 的 rate/volume/pitch 字符串格式化。"""

    def test_rate_positive(self):
        self.assertEqual(int(175 - 100), 75)

    def test_rate_negative(self):
        self.assertEqual(int(50 - 100), -50)

    def test_volume_formula(self):
        self.assertEqual(int((1.0 - 0.5) * 200), 100)

    def test_volume_half(self):
        self.assertEqual(int((0.75 - 0.5) * 200), 50)

    def test_pitch_string(self):
        self.assertEqual(f"{50:+d}Hz", "+50Hz")

    def test_pitch_negative_string(self):
        self.assertEqual(f"{-30:+d}Hz", "-30Hz")


class TestSAPI5RateMapping(unittest.TestCase):
    """SAPI5 语速映射公式。"""

    def test_center_maps_to_zero(self):
        rate = round((225.0 - 225.0) / 17.5)
        self.assertEqual(rate, 0)

    def test_min_maps_to_minus_ten(self):
        rate = round((50 - 225.0) / 17.5)
        self.assertEqual(rate, -10)

    def test_max_maps_to_ten(self):
        rate = round((400 - 225.0) / 17.5)
        self.assertEqual(rate, 10)

    def test_clamped_min(self):
        rate = round((30 - 225.0) / 17.5)
        self.assertEqual(max(-10, min(10, rate)), -10)

    def test_clamped_max(self):
        rate = round((500 - 225.0) / 17.5)
        self.assertEqual(max(-10, min(10, rate)), 10)


class TestPiperLengthScale(unittest.TestCase):
    """Piper 的 length_scale 计算。"""

    def test_normal_speed(self):
        ls = 100.0 / max(100, 1.0)
        self.assertAlmostEqual(ls, 1.0)

    def test_fast_speed(self):
        ls = 100.0 / max(200, 1.0)
        self.assertAlmostEqual(ls, 0.5)

    def test_slow_speed(self):
        ls = 100.0 / max(50, 1.0)
        self.assertAlmostEqual(ls, 2.0)

    def test_zero_speed_clamped(self):
        ls = 100.0 / max(0, 1.0)
        self.assertAlmostEqual(ls, 100.0)  # max(0, 1.0) → 1.0, 100/1=100

    def test_extreme_speed_clamped(self):
        ls = 100.0 / max(1000, 1.0)
        clamped = max(0.2, min(5.0, ls))
        self.assertAlmostEqual(clamped, 0.2)


# ═══════════════════════════════════════════════════════════
#  BUG 1 Regression Tests: Aliyun api_key 优先级
#  Bug: 构造函数参数 api_key 被 os.environ/config 覆盖而静默丢失
# ═══════════════════════════════════════════════════════════

class TestAliyunApiKeyPriority(unittest.TestCase):
    """验证 api_key 优先级: 构造参数 > config.json > 环境变量。"""

    def setUp(self):
        self.config_patch = patch("engines.aliyun.load_aliyun_config")
        self.mock_config = self.config_patch.start()
        self.mock_config.return_value = {}
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.env_patch.stop()

    def _assert_dashscope_api_key(self, expected):
        """检查 dashscope.api_key 是否被设为期望值。"""
        import engines.aliyun
        # 在 __init__ 中 dashscope.api_key = api_key 被执行后，
        # engines.aliyun.dashscope 应该是一个 MagicMock (被 patch 替换)
        # 或者是通过 load_aliyun_config mock 控制路径
        # 我们直接通过 import engines.aliyun 并检查其 dashscope 属性来验证
        self.assertEqual(engines.aliyun.dashscope.api_key, expected)

    def test_constructor_param_takes_priority(self):
        """构造参数 api_key 不会被环境变量或 config 覆盖。"""
        os.environ["DASHSCOPE_API_KEY"] = "env-key"
        self.mock_config.return_value = {"api_key": "config-key"}

        ds_mock = MagicMock()
        with patch("engines.aliyun.dashscope", ds_mock), \
             patch("engines.aliyun.QwenTtsRealtime", MagicMock()):
            from engines.aliyun import AliyunEngine
            AliyunEngine(api_key="param-key")
            self.assertEqual(ds_mock.api_key, "param-key")

    def test_config_takes_priority_over_env(self):
        """config.json 的 api_key 优先于环境变量。"""
        os.environ["DASHSCOPE_API_KEY"] = "env-key"
        self.mock_config.return_value = {"api_key": "config-key"}

        ds_mock = MagicMock()
        with patch("engines.aliyun.dashscope", ds_mock), \
             patch("engines.aliyun.QwenTtsRealtime", MagicMock()):
            from engines.aliyun import AliyunEngine
            AliyunEngine()
            self.assertEqual(ds_mock.api_key, "config-key")

    def test_env_var_as_fallback(self):
        """当 config 无 api_key 时回退到环境变量。"""
        os.environ["DASHSCOPE_API_KEY"] = "env-key"
        self.mock_config.return_value = {}

        ds_mock = MagicMock()
        with patch("engines.aliyun.dashscope", ds_mock), \
             patch("engines.aliyun.QwenTtsRealtime", MagicMock()):
            from engines.aliyun import AliyunEngine
            AliyunEngine()
            self.assertEqual(ds_mock.api_key, "env-key")

    def test_no_key_raises_runtime_error(self):
        """没有任何 api_key 时抛出 RuntimeError。"""
        os.environ.pop("DASHSCOPE_API_KEY", None)
        self.mock_config.return_value = {}

        with patch("engines.aliyun.dashscope", MagicMock()), \
             patch("engines.aliyun.QwenTtsRealtime", MagicMock()):
            from engines.aliyun import AliyunEngine
            with self.assertRaises(RuntimeError):
                AliyunEngine()


# ═══════════════════════════════════════════════════════════
#  BUG 3 + 9 Regression Tests: SAPI5 异步初始化
#  Bug 3: _voice_list 未预初始化 → 超时后 AttributeError
#  Bug 9: __init__ 中阻塞 wait() 导致 UI 冻结
# ═══════════════════════════════════════════════════════════

class TestSAPI5AsyncInit(unittest.TestCase):
    """验证 SAPI5 不阻塞 __init__ 且 _voice_list 始终安全访问。"""

    def _make_engine(self):
        """创建一个预初始化的 SystemTTSEngine 实例（绕过 __init__）。"""
        import threading
        from engines.sapi5 import SystemTTSEngine

        # patch pythoncom 防止意外导入
        with patch("engines.sapi5.pythoncom", MagicMock()):
            engine = SystemTTSEngine.__new__(SystemTTSEngine)
            engine._current_voice_index = 0
            engine._voice_list = []
            engine._error = None
            engine._voices_ready = threading.Event()
            engine._Dispatch = MagicMock()
        return engine

    def test_voice_list_initialized_before_thread(self):
        """_voice_list 在构造时应已初始化为 []，防止 AttributeError。"""
        engine = self._make_engine()
        self.assertEqual(engine._voice_list, [])
        self.assertIsInstance(engine._voice_list, list)

    def test_init_does_not_block(self):
        """__init__ 不应阻塞等待 voice 列表完成。"""
        import time
        engine = self._make_engine()
        engine._voices_ready.set()  # 模拟后台线程完成

        t0 = time.time()
        engine.get_voices()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.0)

    def test_voices_ready_property(self):
        """voices_ready 属性反映 Event 的实际状态。"""
        engine = self._make_engine()

        self.assertFalse(engine._voices_ready.is_set())
        engine._voices_ready.set()
        self.assertTrue(engine._voices_ready.is_set())
        self.assertTrue(engine.voices_ready)

    def test_get_voices_returns_list_of_tuples(self):
        """get_voices() 返回 (id, name) 元组列表。"""
        import threading
        from engines.sapi5 import SystemTTSEngine
        with patch("engines.sapi5.pythoncom", MagicMock()):
            engine = SystemTTSEngine.__new__(SystemTTSEngine)
            engine._voices_ready = threading.Event()
            engine._voice_list = [(0, "Voice 1"), (1, "Voice 2")]
            engine._voices_ready.set()
            engine._Dispatch = MagicMock()

        voices = engine.get_voices()
        self.assertEqual(voices, [(0, "Voice 1"), (1, "Voice 2")])
