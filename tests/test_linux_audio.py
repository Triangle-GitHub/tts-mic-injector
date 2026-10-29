# -*- coding: utf-8 -*-
"""测试 Linux 音频后端（audio/linux_audio.py）。

不碰真实 PipeWire：pactl / pacat 全被替身接管，所以这组用例在任何 Linux CI
里都能跑，也不需要装 pulseaudio-utils。
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio import linux_audio
from audio.device import AudioDevice
from config import VIRTUAL_MIC_SINK, VIRTUAL_MIC_SOURCE


def _sink(name, desc):
    return {"index": 1, "name": name, "description": desc, "state": "IDLE"}


class TestDeviceEnumeration(unittest.TestCase):
    """list_output_devices(): 设备标识是名字，且排除虚拟麦克风自身。"""

    @patch.object(linux_audio.shutil, "which", return_value="/usr/bin/x")
    @patch.object(linux_audio, "list_sinks")
    def test_excludes_virtual_mic(self, sinks, _which):
        sinks.return_value = [
            _sink(VIRTUAL_MIC_SINK, "TTS Mic Sink"),
            _sink("alsa_output.speaker", "Speaker"),
            _sink("alsa_output.hdmi", "HDMI"),
        ]
        devices = linux_audio.AudioPlayer.list_output_devices()
        self.assertEqual([d.label for d in devices], ["Speaker", "HDMI"])
        self.assertTrue(all(isinstance(d, AudioDevice) for d in devices))
        self.assertTrue(all(d.id.startswith("alsa_output") for d in devices))

    @patch.object(linux_audio.shutil, "which", return_value=None)
    def test_no_tools_returns_empty(self, _which):
        self.assertEqual(linux_audio.AudioPlayer.list_output_devices(), [])

    @patch.object(linux_audio.shutil, "which", return_value="/usr/bin/x")
    @patch.object(linux_audio, "list_sinks", side_effect=RuntimeError("boom"))
    def test_pactl_failure_is_not_fatal(self, _sinks, _which):
        self.assertEqual(linux_audio.AudioPlayer.list_output_devices(), [])


class TestVirtualMic(unittest.TestCase):

    @patch.object(linux_audio.shutil, "which", return_value=None)
    def test_find_raises_without_tools(self, _which):
        with self.assertRaises(RuntimeError):
            linux_audio.AudioPlayer.find_virtual_mic()

    @patch.object(linux_audio.shutil, "which", return_value="/usr/bin/x")
    @patch.object(linux_audio, "list_sources")
    @patch.object(linux_audio, "list_sinks")
    @patch.object(linux_audio, "_pactl")
    def test_ensure_creates_when_missing(self, pactl, sinks, sources, _which):
        # 第一次查不到 --> 走创建流程；创建后再查一次就“有了”
        sinks.side_effect = [
            [],                                    # virtual_mic_present() 第一次
            [_sink(VIRTUAL_MIC_SINK, "TTS Mic Sink")],   # 创建后复核
        ]
        sources.side_effect = [
            [],
            [{"index": 2, "name": VIRTUAL_MIC_SOURCE, "description": "TTS Mic"}],
        ]
        pactl.side_effect = ["", "", "m1", "m2"]

        sink, source, created = linux_audio.ensure_virtual_mic()
        self.assertEqual((sink, source), (VIRTUAL_MIC_SINK, VIRTUAL_MIC_SOURCE))
        self.assertTrue(created)

        calls = [" ".join(str(a) for a in c.args) for c in pactl.call_args_list]
        self.assertTrue(any("module-null-sink" in c for c in calls))
        self.assertTrue(any("module-remap-source" in c for c in calls))

    @patch.object(linux_audio.shutil, "which", return_value="/usr/bin/x")
    @patch.object(linux_audio, "list_sources")
    @patch.object(linux_audio, "list_sinks")
    @patch.object(linux_audio, "_pactl")
    def test_ensure_is_idempotent(self, pactl, sinks, sources, _which):
        sinks.return_value = [_sink(VIRTUAL_MIC_SINK, "TTS Mic Sink")]
        sources.return_value = [
            {"index": 2, "name": VIRTUAL_MIC_SOURCE, "description": "TTS Mic"}
        ]
        _sink_name, _src, created = linux_audio.ensure_virtual_mic()
        self.assertFalse(created)
        pactl.assert_not_called()      # 已经在了，一条命令都不该发

    @patch.object(linux_audio.shutil, "which", return_value="/usr/bin/x")
    @patch.object(linux_audio, "list_sources")
    @patch.object(linux_audio, "list_sinks")
    @patch.object(linux_audio, "_pactl", return_value="")
    def test_find_returns_device(self, _pactl, sinks, sources, _which):
        sinks.return_value = [_sink(VIRTUAL_MIC_SINK, "TTS Mic Sink")]
        sources.return_value = [
            {"index": 2, "name": VIRTUAL_MIC_SOURCE, "description": "TTS Mic"}
        ]
        device = linux_audio.AudioPlayer.find_virtual_mic()
        self.assertEqual(device.id, VIRTUAL_MIC_SINK)
        self.assertTrue(device.is_virtual)


class TestPlayerConstruction(unittest.TestCase):

    def test_default_device_is_virtual_mic(self):
        player = linux_audio.AudioPlayer()
        self.assertEqual(player.device.id, VIRTUAL_MIC_SINK)
        self.assertTrue(player.device.is_virtual)

    def test_accepts_explicit_device(self):
        dev = AudioDevice(id="alsa_output.x", label="Speaker")
        self.assertEqual(linux_audio.AudioPlayer(device=dev).device, dev)

    def test_stop_without_playback_is_safe(self):
        linux_audio.AudioPlayer().stop()


if __name__ == "__main__":
    unittest.main()
