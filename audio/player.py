# -*- coding: utf-8 -*-
"""
AudioPlayer — Windows 侧播放器：pyaudio + VB-Cable 虚拟声卡。

本文件只在 Windows 上被 audio/__init__.py 选中；Linux 侧对应实现是
audio/linux_audio.py。两者暴露同一套接口：

    AudioPlayer(device=AudioDevice(...))
    AudioPlayer.find_virtual_mic()      -> AudioDevice
    AudioPlayer.list_output_devices()   -> [AudioDevice, ...]
    AudioPlayer.play(wav_path, stop_event, monitor=..., monitor_device=AudioDevice(...))

设备标识在 Windows 上是 pyaudio 的 output_device_index（整数），但对外统一包成
AudioDevice，上层不需要知道这个差别。
"""

import logging
import threading
import wave

from audio.device import AudioDevice
from audio.pcm import downmix_mono, scale_volume
from config import VB_CABLE_KEYWORDS

try:
    import pyaudio
except ImportError:
    pyaudio = None

logger = logging.getLogger("TTSMicInjector")


class AudioPlayer:
    """管理音频播放到 VB-Cable 和可选的监听设备。"""

    def __init__(self, device: AudioDevice = None, vb_device_index: int = None):
        self._stream = None
        self._monitor_stream = None
        self._pyaudio = None
        self._playing = False
        # vb_device_index 是 v1 的旧接口，等价于 device.id
        self._device = device
        if self._device is None and vb_device_index is not None:
            self._device = AudioDevice(id=vb_device_index, label="VB-Cable",
                                       is_virtual=True)

    # ── 设备发现 ──

    @staticmethod
    def find_virtual_mic() -> AudioDevice:
        """查找 VB-Cable 虚拟麦克风（CABLE Input）并返回它。"""
        if pyaudio is None:
            raise RuntimeError("pyaudio 未安装。请执行: pip install pyaudio")

        p = pyaudio.PyAudio()
        try:
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxOutputChannels"] == 0:
                    continue
                name = info["name"]
                for kw in VB_CABLE_KEYWORDS:
                    if kw.lower() in name.lower():
                        logger.info(f"找到虚拟麦克风输出设备: [{i}] {name}")
                        return AudioDevice(id=i, label=name, is_virtual=True)
            raise RuntimeError(
                "未检测到 VB-Cable 虚拟麦克风输出设备（CABLE Input）。\n"
                "请从 https://vb-audio.com/Cable/ 下载安装，\n"
                "然后在系统声音设置中将 'CABLE Input' 设为默认通信设备。"
            )
        finally:
            p.terminate()

    @staticmethod
    def list_output_devices() -> list:
        """返回所有输出设备 [AudioDevice, ...]。"""
        if pyaudio is None:
            return []
        p = pyaudio.PyAudio()
        try:
            devices = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxOutputChannels"] > 0:
                    devices.append(AudioDevice(id=i, label=info["name"]))
            return devices
        finally:
            p.terminate()

    @staticmethod
    def default_output_device() -> AudioDevice:
        """系统默认输出设备（监听默认目标）。"""
        if pyaudio is None:
            return None
        p = pyaudio.PyAudio()
        try:
            info = p.get_default_output_device_info()
            return AudioDevice(id=info["index"], label=info["name"])
        except Exception:
            return None
        finally:
            p.terminate()

    @staticmethod
    def virtual_source_name() -> str:
        """Windows 上虚拟麦克风的『源』是 VB-Cable 驱动，没有可选的 source 名。"""
        return "CABLE Output"

    @staticmethod
    def virtual_source_desc() -> str:
        return "CABLE Output"

    # ── 播放 ──

    def play(self, wav_path: str, stop_event: threading.Event,
             monitor: bool = False, monitor_device: AudioDevice = None,
             volume_getter=None) -> bool:
        """播放 WAV 文件到 VB-Cable，可选同时输出到监听设备。

        当 VB-Cable 不可用（device 为 None）时，仍可通过监听设备播放，
        实现"仅监听"模式。

        返回 True 表示播放完成，False 表示被中断。
        """
        if pyaudio is None:
            raise RuntimeError("pyaudio 未安装")

        vb_index = self._device.id if self._device is not None else None
        monitor_index = None
        if monitor:
            monitor_device = monitor_device or self.default_output_device()
            monitor_index = monitor_device.id if monitor_device is not None else None

        self._playing = True
        try:
            wf = wave.open(wav_path, "rb")
            p = self._pyaudio = pyaudio.PyAudio()

            sampwidth = wf.getsampwidth()
            nchannels = wf.getnchannels()
            framerate = wf.getframerate()
            audio_format = p.get_format_from_width(sampwidth)

            # ── VB-Cable 输出流（可选） ──
            need_downmix = False
            if vb_index is not None:
                try:
                    self._stream = p.open(
                        format=audio_format,
                        channels=nchannels,
                        rate=framerate,
                        output=True,
                        output_device_index=vb_index,
                        frames_per_buffer=1024,
                    )
                except OSError:
                    if nchannels > 1:
                        logger.info(f"VB-Cable 不支持 {nchannels} 声道，转为单声道播放")
                        self._stream = p.open(
                            format=audio_format,
                            channels=1,
                            rate=framerate,
                            output=True,
                            output_device_index=vb_index,
                            frames_per_buffer=1024,
                        )
                        need_downmix = True
                    else:
                        raise
            else:
                logger.info("VB-Cable 未安装，跳过虚拟麦克风输出")
                self._stream = None

            # ── 监听输出流（可选） ──
            if monitor:
                try:
                    kwargs = dict(
                        format=audio_format,
                        channels=nchannels,
                        rate=framerate,
                        output=True,
                        frames_per_buffer=1024,
                    )
                    if monitor_index is not None:
                        kwargs["output_device_index"] = monitor_index
                    self._monitor_stream = p.open(**kwargs)
                    logger.info("监听已启用")
                except Exception as e:
                    logger.warning(f"无法打开监听设备: {e}")
                    self._monitor_stream = None

            # ── 播放循环 ──
            chunk = 1024
            data = wf.readframes(chunk)
            while data and not stop_event.is_set():
                if volume_getter:
                    data = scale_volume(data, sampwidth, volume_getter())

                # VB-Cable 输出：需要时降混为单声道
                cable_data = (downmix_mono(data, sampwidth, nchannels)
                              if need_downmix and nchannels > 1 else data)

                if self._stream:
                    self._stream.write(cable_data)
                if self._monitor_stream:
                    try:
                        self._monitor_stream.write(data)
                    except Exception:
                        pass
                data = wf.readframes(chunk)

            completed = not stop_event.is_set()
            wf.close()
            return completed
        finally:
            self._cleanup()

    def stop(self):
        """立即停止播放。"""
        self._cleanup()

    def _cleanup(self):
        self._playing = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._monitor_stream:
            try:
                self._monitor_stream.stop_stream()
                self._monitor_stream.close()
            except Exception:
                pass
            self._monitor_stream = None
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

    # ── 属性 ──

    @property
    def device(self):
        return self._device

    @device.setter
    def device(self, value):
        self._device = value

    @property
    def vb_device_index(self):
        """v1 旧接口：等价于 device.id。"""
        return self._device.id if self._device is not None else None

    @vb_device_index.setter
    def vb_device_index(self, value):
        self._device = (AudioDevice(id=value, label="VB-Cable", is_virtual=True)
                        if value is not None else None)

    @property
    def is_playing(self):
        return self._playing
