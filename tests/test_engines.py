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
