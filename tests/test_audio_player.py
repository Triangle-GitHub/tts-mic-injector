# -*- coding: utf-8 -*-
"""测试 AudioPlayer — 下混、音量调节等纯逻辑方法。"""

import os
import sys
import struct
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.player import AudioPlayer


class TestDownmix(unittest.TestCase):
    """测试 _downmix() — PCM 多声道→单声道。"""

    def setUp(self):
        self.player = AudioPlayer()

    def test_stereo_16bit_positive(self):
        stereo = struct.pack("<hh", 1000, 2000)  # L=1000, R=2000
        mono = self.player._downmix(stereo, 2, 2)
        result = struct.unpack("<h", mono)[0]
        self.assertEqual(result, (1000 + 2000) // 2)

    def test_stereo_16bit_negative(self):
        stereo = struct.pack("<hh", -100, -200)
        mono = self.player._downmix(stereo, 2, 2)
        result = struct.unpack("<h", mono)[0]
        self.assertEqual(result, (-100 + -200) // 2)

    def test_stereo_16bit_mixed_sign(self):
        stereo = struct.pack("<hh", 32767, -32768)
        mono = self.player._downmix(stereo, 2, 2)
        result = struct.unpack("<h", mono)[0]
        self.assertEqual(result, (32767 + -32768) // 2)

    def test_stereo_16bit_multi_frame(self):
        stereo = struct.pack("<hhhh", 100, 200, 300, 400)
        mono = self.player._downmix(stereo, 2, 2)
        results = struct.unpack("<hh", mono)
        self.assertEqual(results[0], 150)
        self.assertEqual(results[1], 350)

    def test_stereo_16bit_clamp_boundary(self):
        stereo = struct.pack("<hh", 32767, 32767)
        mono = self.player._downmix(stereo, 2, 2)
        result = struct.unpack("<h", mono)[0]
        self.assertEqual(result, 32767)

    def test_4channel_general(self):
        ch4 = struct.pack("<hhhh", 1, 2, 3, 4)
        mono = self.player._downmix(ch4, 2, 4)
        result = struct.unpack("<h", mono)[0]
        self.assertEqual(result, (1 + 2 + 3 + 4) // 4)

    def test_3channel_general(self):
        ch3 = struct.pack("<hhh", 10, 20, 30)
        mono = self.player._downmix(ch3, 2, 3)
        result = struct.unpack("<h", mono)[0]
        self.assertEqual(result, (10 + 20 + 30) // 3)


class TestAdjustChunkVolume(unittest.TestCase):
    """测试 _adjust_chunk_volume() — 16-bit PCM 音量调节。"""

    def test_factor_one_no_change(self):
        data = struct.pack("<hhhh", 100, -50, 0, 32767)
        result = AudioPlayer._adjust_chunk_volume(data, 2, 1.0)
        self.assertEqual(result, data)

    def test_factor_half(self):
        data = struct.pack("<hh", 1000, -500)
        result = AudioPlayer._adjust_chunk_volume(data, 2, 0.5)
        vals = struct.unpack("<hh", result)
        self.assertEqual(vals[0], 500)
        self.assertEqual(vals[1], -250)

    def test_factor_zero(self):
        data = struct.pack("<hh", 1000, -500)
        result = AudioPlayer._adjust_chunk_volume(data, 2, 0.0)
        vals = struct.unpack("<hh", result)
        self.assertEqual(vals[0], 0)
        self.assertEqual(vals[1], 0)

    def test_factor_above_one_clamps(self):
        data = struct.pack("<h", 20000)
        result = AudioPlayer._adjust_chunk_volume(data, 2, 2.0)
        val = struct.unpack("<h", result)[0]
        self.assertEqual(val, 32767)  # 40000 clamped

    def test_factor_below_negative_one_clamps(self):
        data = struct.pack("<h", -20000)
        result = AudioPlayer._adjust_chunk_volume(data, 2, 2.0)
        val = struct.unpack("<h", result)[0]
        self.assertEqual(val, -32768)  # -40000 clamped

    def test_non_16bit_passthrough(self):
        data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        result = AudioPlayer._adjust_chunk_volume(data, 3, 0.5)
        self.assertEqual(result, data)  # 24-bit: pass through

    def test_non_16bit_passthrough_8bit(self):
        data = b"\x01\x02\x03\x04"
        result = AudioPlayer._adjust_chunk_volume(data, 1, 0.5)
        self.assertEqual(result, data)
