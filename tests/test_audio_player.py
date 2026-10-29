# -*- coding: utf-8 -*-
"""测试 AudioPlayer 的纯逻辑部分（下混 / 音量），以及 AudioDevice 的语义。

纯 PCM 逻辑已经搬到 audio/pcm.py，两个平台共用，所以这组用例不依赖 pyaudio，
在任何平台都能跑。
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.device import AudioDevice
from audio.pcm import PULSE_FORMATS, downmix_mono, scale_volume


class TestDownmix(unittest.TestCase):
    """downmix_mono() — PCM 多声道→单声道。"""

    def test_stereo_16bit_positive(self):
        stereo = struct.pack("<hh", 1000, 2000)  # L=1000, R=2000
        mono = downmix_mono(stereo, 2, 2)
        self.assertEqual(struct.unpack("<h", mono)[0], (1000 + 2000) // 2)

    def test_stereo_16bit_negative(self):
        stereo = struct.pack("<hh", -100, -200)
        mono = downmix_mono(stereo, 2, 2)
        self.assertEqual(struct.unpack("<h", mono)[0], (-100 + -200) // 2)

    def test_stereo_16bit_mixed_sign(self):
        stereo = struct.pack("<hh", -1000, 500)
        mono = downmix_mono(stereo, 2, 2)
        self.assertEqual(struct.unpack("<h", mono)[0], (-1000 + 500) // 2)

    def test_stereo_multi_frame(self):
        frames = [(100, 200), (-300, -400), (32767, -32768)]
        data = b"".join(struct.pack("<hh", l, r) for l, r in frames)
        mono = downmix_mono(data, 2, 2)
        values = struct.unpack("<%dh" % len(frames), mono)
        self.assertEqual(values,
                         tuple((l + r) // 2 for l, r in frames))

    def test_4channel_general(self):
        data = struct.pack("<hhhh", 100, 200, 300, 400)
        mono = downmix_mono(data, 2, 4)
        self.assertEqual(struct.unpack("<h", mono)[0], (100 + 200 + 300 + 400) // 4)

    def test_mono_input_untouched(self):
        data = struct.pack("<hh", 1000, -1000)
        self.assertEqual(downmix_mono(data, 2, 1), data)


class TestScaleVolume(unittest.TestCase):
    """scale_volume() — 逐 chunk 调音量。"""

    def test_full_volume_is_noop(self):
        data = struct.pack("<hh", 1000, -1000)
        self.assertEqual(scale_volume(data, 2, 1.0), data)

    def test_half_volume(self):
        data = struct.pack("<h", 1000)
        self.assertEqual(struct.unpack("<h", scale_volume(data, 2, 0.5))[0], 500)

    def test_clipping(self):
        data = struct.pack("<h", 20000)
        self.assertEqual(struct.unpack("<h", scale_volume(data, 2, 3.0))[0], 32767)

    def test_clipping_negative(self):
        data = struct.pack("<h", -20000)
        self.assertEqual(struct.unpack("<h", scale_volume(data, 2, 3.0))[0], -32768)

    def test_non_16bit_untouched(self):
        data = b"\x01\x02\x03"
        self.assertEqual(scale_volume(data, 3, 0.5), data)


class TestAudioDevice(unittest.TestCase):
    """AudioDevice 是不可变值对象，两端共用。"""

    def test_immutable(self):
        d = AudioDevice(id=3, label="Speaker")
        with self.assertRaises(Exception):
            d.label = "other"  # frozen dataclass

    def test_str_is_label(self):
        self.assertEqual(str(AudioDevice(id="x", label="TTS Mic")), "TTS Mic")

    def test_virtual_flag(self):
        self.assertFalse(AudioDevice(id=1, label="a").is_virtual)
        self.assertTrue(AudioDevice(id="s", label="a", is_virtual=True).is_virtual)

    def test_pulse_format_table(self):
        self.assertEqual(PULSE_FORMATS[2], "s16le")
        self.assertEqual(PULSE_FORMATS[1], "u8")
        self.assertNotIn(5, PULSE_FORMATS)


if __name__ == "__main__":
    unittest.main()
