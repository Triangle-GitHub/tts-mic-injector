# -*- coding: utf-8 -*-
"""测试 TTSService — 状态机、gen 竞态、停止、回调。"""

import os
import sys
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.tts_service import TTSService


class TestTTSServiceInit(unittest.TestCase):
    """TTSService 初始状态。"""

    def test_initial_state(self):
        s = TTSService()
        self.assertIsNone(s.engine)
        self.assertFalse(s._is_playing)
        self.assertEqual(s._playback_gen, 0)
        self.assertEqual(s._active_stops, [])
        self.assertFalse(s._concurrent_mode)

    def test_callback_registration(self):
        s = TTSService()
        received = []

        def cb(text, color):
            received.append((text, color))

        s.on("status", cb)
        s._emit("status", "🟢 就绪", "green")
        self.assertEqual(received, [("🟢 就绪", "green")])

    def test_multiple_callbacks_same_event(self):
        s = TTSService()
        results = []

        s.on("status", lambda t, c: results.append(1))
        s.on("status", lambda t, c: results.append(2))
        s._emit("status", "x", "y")
        self.assertEqual(results, [1, 2])

    def test_callback_exception_does_not_crash(self):
        s = TTSService()
        ok = []

        def bad(*_):
            raise RuntimeError("callback error")

        def good(t, c):
            ok.append(t)

        s.on("status", bad)
        s.on("status", good)
        s._emit("status", "test", "red")
        self.assertEqual(ok, ["test"])


class TestTTSServiceEngineManagement(unittest.TestCase):
    """引擎切换。"""

    def test_switch_engine_success(self):
        s = TTSService()
        with patch("service.tts_service.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_engine.name = "eSpeak"
            mock_create.return_value = mock_engine

            result = s.switch_engine("eSpeak")
            self.assertTrue(result)
            self.assertEqual(s.engine, mock_engine)
            self.assertEqual(s.engine_name, "eSpeak")

    def test_switch_engine_failure_returns_false(self):
        s = TTSService()
        with patch("service.tts_service.create_engine", side_effect=FileNotFoundError):
            result = s.switch_engine("eSpeak")
            self.assertFalse(result)
            self.assertIsNone(s.engine)

    def test_switch_engine_emits_callback(self):
        s = TTSService()
        received = []
        s.on("engine_ready", lambda name: received.append(name))

        with patch("service.tts_service.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            s.switch_engine("Piper")
            self.assertEqual(received, ["Piper"])

    def test_switch_from_sapi5_calls_stop_on_old(self):
        from engines.sapi5 import SystemTTSEngine
        s = TTSService()
        old = MagicMock(spec=SystemTTSEngine)
        s.engine = old
        s.engine_name = "SAPI5"

        with patch("service.tts_service.create_engine") as mock_create:
            mock_create.return_value = MagicMock()
            s.switch_engine("eSpeak")
            old.stop.assert_called_once()


class TestTTSServiceSpeak(unittest.TestCase):
    """speak() 核心流程。"""

    def setUp(self):
        self.s = TTSService()
        self.mock_engine = MagicMock()
        self.mock_engine.synthesize.return_value = "/tmp/fake.wav"
        self.s.engine = self.mock_engine
        self.s.engine_name = "eSpeak"
        self.block = threading.Event()
        self.synthesize_done = threading.Event()

    def _blocking_synthesize(self, *args):
        """阻塞 synthesize，允许测试控制 worker 生命周期。"""
        self.block.wait(timeout=5)
        self.synthesize_done.set()
        return "/tmp/fake.wav"

    def test_speak_no_engine(self):
        self.s.engine = None
        self.s.speak("hello", 100, 1.0)
        self.assertFalse(self.s._is_playing)

    def test_speak_increments_gen(self):
        self.mock_engine.synthesize = self._blocking_synthesize
        self.s.speak("hello", 100, 1.0)
        self.assertEqual(self.s._playback_gen, 1)
        self.assertTrue(self.s._is_playing)
        self.block.set()
        self.synthesize_done.wait(timeout=2)

    def test_rapid_speak_cancels_by_gen(self):
        """连续两次 speak，第一个 worker 因 gen 不匹配被中断。"""
        self.mock_engine.synthesize = self._blocking_synthesize

        self.s.speak("first", 100, 1.0)
        gen1 = self.s._playback_gen  # = 1
        self.assertTrue(self.s._is_playing)

        self.s.speak("second", 100, 1.0)
        gen2 = self.s._playback_gen  # = 2
        self.assertEqual(gen2, gen1 + 1)

        # 释放阻塞，第一个 worker 会检测 gen != _playback_gen 而退出
        self.block.set()
        self.synthesize_done.wait(timeout=2)
        # gen 防护生效
        self.assertEqual(self.s._playback_gen, 2)

    def test_speak_emits_status_callback(self):
        statuses = []
        self.s.on("status", lambda t, c: statuses.append(t))

        with patch("service.tts_service.AudioPlayer") as mock_player_class:
            mock_player = MagicMock()
            mock_player.play.return_value = True
            mock_player_class.return_value = mock_player

            self.s.speak("hello", 100, 1.0)
            self.assertIn("🔊 合成中...", statuses)

    def test_speak_creates_new_audio_player(self):
        with patch("service.tts_service.AudioPlayer") as mock_player_class:
            mock_player = MagicMock()
            mock_player.play.return_value = True
            mock_player_class.return_value = mock_player

            self.s.speak("hello", 100, 1.0)
            time.sleep(0.1)
            mock_player_class.assert_called_once()

    def test_speak_worker_cleans_up_temp_wav(self):
        with patch("service.tts_service.AudioPlayer") as mock_player_class, \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink") as mock_unlink:
            mock_player = MagicMock()
            mock_player.play.return_value = True
            mock_player_class.return_value = mock_player

            self.s.speak("hello", 100, 1.0)
            time.sleep(0.1)
            mock_unlink.assert_called()


class TestTTSServiceStop(unittest.TestCase):
    """stop() 行为。"""

    def test_stop_sets_event(self):
        s = TTSService()
        evt = threading.Event()
        s._active_stops.append(evt)
        s.stop()
        self.assertTrue(evt.is_set())
        self.assertEqual(s._active_stops, [])

    def test_stop_resets_playing(self):
        s = TTSService()
        s._is_playing = True
        s.stop()
        self.assertFalse(s._is_playing)

    def test_stop_emits_status(self):
        s = TTSService()
        statuses = []
        s.on("status", lambda t, c: statuses.append((t, c)))
        s.stop()
        self.assertEqual(statuses[0], ("🟢 就绪", "green"))


class TestTTSServiceDeviceDetection(unittest.TestCase):
    """设备检测。"""

    def test_vb_cable_found(self):
        s = TTSService()
        found = []
        s.on("vb_cable_detected", lambda idx: found.append(idx))

        with patch("service.tts_service.AudioPlayer.find_vb_cable", return_value=7):
            result = s.detect_vb_cable()
            self.assertTrue(result)
            self.assertEqual(s._vb_device_index, 7)
            self.assertEqual(found, [7])

    def test_vb_cable_not_found(self):
        s = TTSService()
        errors = []
        s.on("vb_cable_error", lambda msg: errors.append(msg))

        with patch("service.tts_service.AudioPlayer.find_vb_cable",
                   side_effect=RuntimeError("not found")):
            result = s.detect_vb_cable()
            self.assertFalse(result)
            self.assertEqual(len(errors), 1)

    def test_pyaudio_not_installed(self):
        s = TTSService()
        errors = []
        s.on("vb_cable_error", lambda msg: errors.append(msg))

        with patch("service.tts_service.pyaudio", None):
            result = s.detect_vb_cable()
            self.assertFalse(result)
            self.assertEqual(errors[0], "pyaudio 未安装")

    def test_list_monitor_devices(self):
        s = TTSService()
        with patch("service.tts_service.AudioPlayer.list_output_devices",
                   return_value=[(0, "Speakers"), (1, "Headphones")]):
            devices = s.list_monitor_devices()
            self.assertEqual(devices, [(0, "Speakers"), (1, "Headphones")])

    def test_vb_cable_available_after_detection(self):
        s = TTSService()
        self.assertFalse(s.vb_cable_available)

        with patch("service.tts_service.AudioPlayer.find_vb_cable", return_value=7):
            s.detect_vb_cable()
            self.assertTrue(s.vb_cable_available)

    def test_vb_cable_not_available_on_error(self):
        s = TTSService()
        with patch("service.tts_service.AudioPlayer.find_vb_cable",
                   side_effect=RuntimeError("not found")):
            s.detect_vb_cable()
            self.assertFalse(s.vb_cable_available)

    def test_install_vbcable_returns_installer(self):
        s = TTSService()
        with patch("service.tts_service.VBCableInstaller") as mock_installer_class:
            mock_installer = MagicMock()
            mock_installer.is_busy.return_value = False
            mock_installer_class.return_value = mock_installer
            mock_installer_class.is_busy.return_value = False

            result = s.install_vbcable()
            mock_installer.start.assert_called_once()
            self.assertIsNotNone(result)

    def test_install_vbcable_busy_returns_none(self):
        s = TTSService()
        with patch("service.tts_service.VBCableInstaller") as mock_installer_class:
            mock_installer_class.is_busy.return_value = True
            result = s.install_vbcable()
            self.assertIsNone(result)


class TestTTSServiceGetters(unittest.TestCase):
    """UI 状态获取器注入。"""

    def test_volume_getter_injection(self):
        s = TTSService()
        s.set_volume_getter(lambda: 0.75)
        self.assertTrue(hasattr(s, "_get_volume_getter"))
        self.assertEqual(s._get_volume_getter(), 0.75)

    def test_monitor_state_getter_injection(self):
        s = TTSService()
        s.set_monitor_state_getter(lambda: True)
        self.assertTrue(s._get_monitor_enabled())


# ═══════════════════════════════════════════════════════════
#  BUG 2 Regression Tests: 监听设备 getter 注入
#  Bug: hasattr(self, '_monitor_enabled') 永远 False
#        → monitor_idx 永远为 None
#       应使用 hasattr(self, '_get_monitor_device_index')
# ═══════════════════════════════════════════════════════════

class TestMonitorDeviceGetter(unittest.TestCase):
    """验证监听设备索引 getter 注入后能被 speak() 正确使用。"""

    def test_getter_not_injected_returns_none(self):
        """未注入 getter 时 monitor_idx 应为 None。"""
        s = TTSService()
        self.assertFalse(hasattr(s, "_get_monitor_device_index"))
        # speak() 中的检查应返回 None（不崩溃）
        monitor_idx = s._get_monitor_device_index() if hasattr(s, "_get_monitor_device_index") else None
        self.assertIsNone(monitor_idx)

    def test_getter_injected_returns_value(self):
        """注入 getter 后 speak() 应能获取监听设备索引。"""
        s = TTSService()
        s.set_monitor_device_getter(lambda: 3)
        self.assertTrue(hasattr(s, "_get_monitor_device_index"))
        monitor_idx = s._get_monitor_device_index() if hasattr(s, "_get_monitor_device_index") else None
        self.assertEqual(monitor_idx, 3)

    def test_getter_returns_none_when_disabled(self):
        """getter 返回 None 时（监听关闭），speak() 中 monitor_idx 为 None。"""
        s = TTSService()
        s.set_monitor_device_getter(lambda: None)
        monitor_idx = s._get_monitor_device_index() if hasattr(s, "_get_monitor_device_index") else None
        self.assertIsNone(monitor_idx)
