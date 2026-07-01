# -*- coding: utf-8 -*-
"""
TTSService — TTS 核心编排层，零 UI 依赖。
管理引擎生命周期、播放状态、线程调度，通过回调通知外部状态变化。
"""

import os
import shutil
import threading
import logging

from audio.player import AudioPlayer
from engines import create_engine
from engines.edge import EdgeEngine
from engines.sapi5 import SystemTTSEngine

try:
    import pyaudio
except ImportError:
    pyaudio = None

logger = logging.getLogger("TTSMicInjector")


class TTSService:
    """TTS 核心服务，完全不依赖 tkinter。"""

    def __init__(self):
        self.engine = None
        self.engine_name = ""
        self._stop_event = threading.Event()
        self._is_playing = False
        self._playback_gen = 0
        self._vb_device_index = None
        self._current_wav = None

        self._callbacks = {}

    # ── 回调注册 ──
    def on(self, event: str, callback):
        """注册事件回调。事件: status, engine_ready, vb_cable_detected, vb_cable_error, log"""
        self._callbacks.setdefault(event, []).append(callback)

    def _emit(self, event: str, *args):
        """触发事件回调。"""
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                logger.debug(f"回调异常 ({event}): {e}")

    # ── 引擎管理 ──
    def start_engine(self, name: str, **kwargs):
        """初始化并启动指定引擎。"""
        return self.switch_engine(name, **kwargs)

    def switch_engine(self, name: str, **kwargs):
        """切换引擎（不中断当前播放）。"""
        old_engine = self.engine

        try:
            new_engine = create_engine(name, **kwargs)
        except (FileNotFoundError, RuntimeError) as e:
            logger.error(str(e))
            return False

        self.engine = new_engine
        self.engine_name = name

        if isinstance(old_engine, SystemTTSEngine):
            old_engine.stop()

        self._emit("engine_ready", name)
        return True

    def get_voices(self):
        if self.engine and hasattr(self.engine, 'get_voices'):
            return self.engine.get_voices()
        return []

    def set_voice(self, voice_id):
        if self.engine and hasattr(self.engine, 'set_voice'):
            self.engine.set_voice(voice_id)

    def set_pitch(self, pitch_hz: float):
        if isinstance(self.engine, EdgeEngine):
            self.engine.set_pitch(pitch_hz)

    def get_speed_range(self):
        if self.engine:
            return self.engine.get_speed_range()
        return None

    # ── 播放 ──
    def speak(self, text: str, speed: float, volume: float, save_path: str = None):
        """合成并播放（非阻塞）。"""
        if not self.engine:
            logger.error("引擎未就绪，无法合成")
            return

        if self._is_playing:
            logger.info("正在播放中，先停止当前播放")
            self.stop()

        self._stop_event.clear()
        self._is_playing = True
        self._playback_gen += 1
        gen = self._playback_gen
        self._emit("status", "🔊 合成中...", "orange")

        monitor_idx = self._get_monitor_device_index() if hasattr(self, '_monitor_enabled') else None
        player = AudioPlayer(vb_device_index=self._vb_device_index)
        threading.Thread(target=self._speak_worker,
                         args=(text, speed, volume, player, monitor_idx, gen, save_path),
                         daemon=True).start()

    def _speak_worker(self, text: str, speed: float, volume: float,
                      player: AudioPlayer, monitor_device_index: int,
                      gen: int, save_path: str = None):
        """后台线程：合成 + 播放。"""
        wav_path = None
        try:
            logger.info(f"合成: 「{text[:50]}{'...' if len(text)>50 else ''}」")

            wav_path = self.engine.synthesize(text, speed, volume)

            if self._stop_event.is_set() or gen != self._playback_gen:
                return

            if save_path and os.path.exists(wav_path):
                try:
                    shutil.copy2(wav_path, save_path)
                    logger.info(f"已保存: {save_path}")
                except Exception as e:
                    logger.error(f"保存失败: {e}")

            logger.info("合成完成，播放中...")
            if gen == self._playback_gen:
                self._emit("status", "🔊 播放中...", "#2a7a2a")

            self._current_wav = wav_path
            monitor = self._get_monitor_enabled() if hasattr(self, '_get_monitor_enabled') else False
            vol_getter = self._get_volume_getter if hasattr(self, '_get_volume_getter') else None

            completed = player.play(wav_path, self._stop_event,
                                     monitor=monitor,
                                     monitor_device_index=monitor_device_index,
                                     volume_getter=vol_getter)

            if gen == self._playback_gen:
                if completed:
                    logger.info("播放完成")
                else:
                    logger.info("播放被中断")
                self._emit("status", "🟢 就绪", "green")
                self._is_playing = False

        except Exception as e:
            logger.error(f"错误: {e}")
            if gen == self._playback_gen:
                self._emit("status", f"❌ 错误: {str(e)}", "red")
                self._emit("status", "🟢 就绪", "green")  # reset after error display
                self._is_playing = False
        finally:
            if gen == self._playback_gen:
                self._is_playing = False
            player.stop()
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            self._current_wav = None

    def stop(self):
        """停止当前播放。"""
        self._stop_event.set()
        self._is_playing = False
        self._emit("status", "🟢 就绪", "green")

    # ── 设备检测 ──
    def detect_vb_cable(self) -> bool:
        """检测 VB-Cable 是否存在。返回 True/False。"""
        try:
            if pyaudio is None:
                self._emit("vb_cable_error", "pyaudio 未安装")
                return False
            idx = AudioPlayer.find_vb_cable()
            self._vb_device_index = idx
            self._emit("vb_cable_detected", idx)
            return True
        except RuntimeError as e:
            self._emit("vb_cable_error", str(e))
            return False

    def list_monitor_devices(self) -> list:
        """返回所有输出设备列表 [(index, name), ...]."""
        return AudioPlayer.list_output_devices()

    # ── 延迟绑定的 UI 状态获取器 ──
    # 这些属性在 ui/app.py 中通过 setter 注入，避免 service 直接依赖 tkinter

    def set_monitor_state_getter(self, getter):
        """注入监听启用状态的获取器。"""
        self._get_monitor_enabled = getter

    def set_monitor_device_getter(self, getter):
        """注入监听设备索引的获取器。"""
        self._get_monitor_device_index = getter

    def set_pitch_getter(self, getter):
        """注入音调参数的获取器。"""
        self._get_pitch = getter

    def set_volume_getter(self, getter):
        """注入实时音量的获取器（返回 0.0~1.0）。"""
        self._get_volume_getter = getter
