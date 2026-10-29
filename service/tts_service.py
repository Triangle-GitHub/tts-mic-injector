# -*- coding: utf-8 -*-
"""
TTSService — TTS 核心编排层，零 UI 依赖。
管理引擎生命周期、播放状态、线程调度，通过回调通知外部状态变化。

平台无关：音频后端由 audio/ 包按平台选择，本层只跟 AudioDevice 打交道。
"""

import os
import sys
import shutil
import threading
import logging

from audio import AudioPlayer, AudioDevice
from engines import create_engine
from engines.edge import EdgeEngine
from engines.sapi5 import SystemTTSEngine
from config import CONCURRENT_MODE_DEFAULT
from installer import VBCableInstaller, is_vbcable_installed

logger = logging.getLogger("TTSMicInjector")

# 事件别名：v1 叫 vb_cable_*，v2 起叫 virtual_mic_*（Windows 上是 VB-Cable，
# Linux 上是 PipeWire 虚拟源，同一个概念）。两套名字都能用，UI 无需立刻改。
_EVENT_ALIASES = {
    "vb_cable_detected": "virtual_mic_detected",
    "vb_cable_error": "virtual_mic_error",
}


def _canonical_event(name: str) -> str:
    return _EVENT_ALIASES.get(name, name)


class TTSService:
    """TTS 核心服务，完全不依赖任何 GUI 框架。"""

    def __init__(self):
        self.engine = None
        self.engine_name = ""
        self._active_stops = []          # per-worker stop events
        self._is_playing = False
        self._playback_gen = 0
        self._mic_device = None          # AudioDevice | None
        self._current_wav = None
        self._concurrent_mode = CONCURRENT_MODE_DEFAULT
        self._virtual_mic_available = False
        self._installer = None

        self._callbacks = {}

    # ── 回调注册 ──
    def on(self, event: str, callback):
        """注册事件回调。
        事件: status, engine_ready, virtual_mic_detected, virtual_mic_error, log
        （旧名 vb_cable_detected / vb_cable_error 仍然可用）
        """
        self._callbacks.setdefault(_canonical_event(event), []).append(callback)

    def _emit(self, event: str, *args):
        """触发事件回调。"""
        for cb in self._callbacks.get(_canonical_event(event), []):
            try:
                cb(*args)
            except Exception as e:
                logger.debug(f"回调异常 ({event}): {e}")

    # ── 并发模式 ──

    @property
    def concurrent_mode(self) -> bool:
        return self._concurrent_mode

    @concurrent_mode.setter
    def concurrent_mode(self, enabled: bool):
        self._concurrent_mode = enabled

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

        if not self._concurrent_mode:
            self.stop()
            self._active_stops.clear()

        self._is_playing = True
        self._playback_gen += 1
        gen = self._playback_gen
        self._emit("status", "🔊 合成中...", "orange")

        worker_stop = threading.Event()
        self._active_stops.append(worker_stop)

        monitor_device = (self._get_monitor_device()
                          if hasattr(self, '_get_monitor_device') else None)
        player = AudioPlayer(device=self._mic_device)
        threading.Thread(target=self._speak_worker,
                         args=(text, speed, volume, player, monitor_device, gen,
                               save_path, worker_stop),
                         daemon=True).start()

    def _speak_worker(self, text: str, speed: float, volume: float,
                      player: AudioPlayer, monitor_device: AudioDevice,
                      gen: int, save_path: str, stop_evt: threading.Event):
        """后台线程：合成 + 播放。"""
        wav_path = None
        try:
            logger.info(f"合成: 「{text[:50]}{'...' if len(text)>50 else ''}」")

            wav_path = self.engine.synthesize(text, speed, volume)

            if stop_evt.is_set() or gen != self._playback_gen:
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

            completed = player.play(wav_path, stop_evt,
                                    monitor=monitor,
                                    monitor_device=monitor_device,
                                    volume_getter=vol_getter)

            if gen == self._playback_gen:
                if completed:
                    logger.info("播放完成")
                else:
                    logger.info("播放被中断")
                self._emit("status", "🟢 就绪", "green")

        except Exception as e:
            logger.error(f"错误: {e}")
            if gen == self._playback_gen:
                self._emit("status", f"❌ 错误: {str(e)}", "red")
                self._emit("status", "🟢 就绪", "green")
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
            if stop_evt in self._active_stops:
                self._active_stops.remove(stop_evt)

    def stop(self):
        """停止所有播放。"""
        for evt in list(self._active_stops):
            evt.set()
        self._active_stops.clear()
        self._is_playing = False
        self._emit("status", "🟢 就绪", "green")

    # ── 虚拟麦克风检测 ──

    def detect_virtual_mic(self) -> bool:
        """检测（必要时创建）虚拟麦克风。

        Windows: VB-Cable；其他平台: PipeWire/Pulse 虚拟源。
        """
        try:
            device = AudioPlayer.find_virtual_mic()
            self._mic_device = device
            self._virtual_mic_available = True
            self._emit("virtual_mic_detected", device)
            return True
        except RuntimeError as e:
            self._mic_device = None
            self._virtual_mic_available = False
            self._emit("virtual_mic_error", str(e))
            return False

    # v1 旧名，保留以兼容外部调用
    def detect_vb_cable(self) -> bool:
        return self.detect_virtual_mic()

    @property
    def virtual_mic_available(self) -> bool:
        return self._virtual_mic_available

    @property
    def vb_cable_available(self) -> bool:
        """v1 旧名。"""
        return self._virtual_mic_available

    @property
    def mic_device(self):
        return self._mic_device

    def install_vbcable(self):
        """启动 VB-Cable 一键安装（非阻塞，仅 Windows）。"""
        if sys.platform != "win32":
            logger.warning("VB-Cable 只用在 Windows；Linux 侧用 PipeWire 虚拟麦克风，无需安装")
            return None

        if VBCableInstaller.is_busy():
            logger.warning("VB-Cable 安装已在进行中")
            return self._installer

        self._installer = VBCableInstaller()
        self._installer.progress.connect(lambda msg: logger.info(f"[VB-Cable] {msg}"))
        self._installer.finished.connect(self._on_install_finished)
        self._installer.error_occurred.connect(
            lambda et, msg: logger.error(f"[VB-Cable] {et}: {msg}")
        )
        self._installer.start()
        return self._installer

    def _on_install_finished(self, success: bool, message: str):
        if success:
            logger.info(f"VB-Cable 安装成功: {message}")
            self.detect_virtual_mic()
        else:
            logger.error(f"VB-Cable 安装失败: {message}")

    # ── 设备列表 ──

    def list_output_devices(self) -> list:
        """所有输出设备 [AudioDevice, ...]（已排除虚拟麦克风自身）。"""
        return AudioPlayer.list_output_devices()

    def list_monitor_devices(self) -> list:
        """v1 旧接口：[(id, label), ...]。新代码请用 list_output_devices()。"""
        return [(d.id, d.label) for d in self.list_output_devices()]

    # ── 延迟绑定的 UI 状态获取器 ──

    def set_monitor_state_getter(self, getter):
        """注入监听启用状态的获取器。"""
        self._get_monitor_enabled = getter

    def set_monitor_device_getter(self, getter):
        """注入监听设备的获取器（应返回 AudioDevice 或 None）。"""
        self._get_monitor_device = getter

    def set_pitch_getter(self, getter):
        """注入音调参数的获取器。"""
        self._get_pitch = getter

    def set_volume_getter(self, getter):
        """注入实时音量的获取器（返回 0.0~1.0）。"""
        self._get_volume_getter = getter
