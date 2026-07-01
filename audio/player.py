# -*- coding: utf-8 -*-
"""
AudioPlayer — 管理音频播放到 VB-Cable 和可选的监听设备。
"""

import wave
import struct
import threading
import logging

from config import VB_CABLE_KEYWORDS

try:
    import pyaudio
except ImportError:
    pyaudio = None

logger = logging.getLogger("TTSMicInjector")


class AudioPlayer:
    """管理音频播放到 VB-Cable 和可选的监听设备。"""

    def __init__(self, vb_device_index: int = None):
        self._stream = None
        self._monitor_stream = None
        self._pyaudio = None
        self._playing = False
        self._vb_device_index = vb_device_index

    @staticmethod
    def find_vb_cable() -> int:
        """查找 VB-Cable 输出设备（CABLE Input）并返回索引。"""
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
                        return i
            raise RuntimeError(
                "未检测到 VB-Cable 虚拟麦克风输出设备（CABLE Input）。\n"
                "请从 https://vb-audio.com/Cable/ 下载安装，\n"
                "然后在系统声音设置中将 'CABLE Input' 设为默认通信设备。"
            )
        finally:
            p.terminate()

    @staticmethod
    def list_output_devices() -> list:
        """返回所有输出设备列表 [(index, name), ...]."""
        if pyaudio is None:
            return []
        p = pyaudio.PyAudio()
        try:
            devices = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxOutputChannels"] > 0:
                    devices.append((i, info["name"]))
            return devices
        finally:
            p.terminate()

    def play(self, wav_path: str, stop_event: threading.Event,
             monitor: bool = False, monitor_device_index: int = None,
             volume_getter=None) -> bool:
        """播放 WAV 文件到 VB-Cable，可选同时输出到监听设备。
        
        返回 True 表示播放完成，False 表示被中断。
        """
        if pyaudio is None:
            raise RuntimeError("pyaudio 未安装")

        self._playing = True
        try:
            wf = wave.open(wav_path, "rb")
            p = self._pyaudio = pyaudio.PyAudio()

            if self._vb_device_index is None:
                self._vb_device_index = self.find_vb_cable()

            sampwidth = wf.getsampwidth()
            nchannels = wf.getnchannels()
            framerate = wf.getframerate()
            audio_format = p.get_format_from_width(sampwidth)

            need_downmix = False
            try:
                self._stream = p.open(
                    format=audio_format,
                    channels=nchannels,
                    rate=framerate,
                    output=True,
                    output_device_index=self._vb_device_index,
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
                        output_device_index=self._vb_device_index,
                        frames_per_buffer=1024,
                    )
                    need_downmix = True
                else:
                    raise

            if monitor:
                try:
                    kwargs = dict(
                        format=audio_format,
                        channels=nchannels,
                        rate=framerate,
                        output=True,
                        frames_per_buffer=1024,
                    )
                    if monitor_device_index is not None:
                        kwargs["output_device_index"] = monitor_device_index
                    self._monitor_stream = p.open(**kwargs)
                    logger.info("监听已启用")
                except Exception as e:
                    logger.warning(f"无法打开监听设备: {e}")
                    self._monitor_stream = None

            chunk = 1024
            data = wf.readframes(chunk)
            while data and not stop_event.is_set():
                if volume_getter:
                    vol = volume_getter()
                    if vol < 0.99:
                        data = self._adjust_chunk_volume(data, sampwidth, vol)
                if need_downmix and nchannels > 1:
                    data = self._downmix(data, sampwidth, nchannels)
                self._stream.write(data)
                if self._monitor_stream:
                    try:
                        self._monitor_stream.write(data)
                    except Exception:
                        pass
                data = wf.readframes(chunk)

            completed = not stop_event.is_set()
            return completed
        finally:
            self._cleanup()

    def _downmix(self, data: bytes, sampwidth: int, nchannels: int) -> bytes:
        """将多声道 PCM 数据降混为单声道。"""
        if nchannels == 2 and sampwidth == 2:
            n = len(data) // 4 * 2
            result = bytearray(n)
            for i in range(0, len(data), 4):
                ls = struct.unpack("<h", data[i:i + 2])[0]
                rs = struct.unpack("<h", data[i + 2:i + 4])[0]
                mono = (ls + rs) // 2
                struct.pack_into("<h", result, i // 2, mono)
            return bytes(result)
        frame_size = sampwidth * nchannels
        result = bytearray()
        for i in range(0, len(data), frame_size):
            total = 0
            for ch in range(nchannels):
                chunk_data = data[i + ch * sampwidth:i + (ch + 1) * sampwidth]
                sample = int.from_bytes(chunk_data, 'little', signed=True)
                total += sample
            mono = total // nchannels
            result.extend(mono.to_bytes(sampwidth, 'little', signed=True))
        return bytes(result)

    @staticmethod
    def _adjust_chunk_volume(data: bytes, sampwidth: int, factor: float) -> bytes:
        """实时调整 PCM chunk 音量（16-bit）。"""
        if sampwidth != 2:
            return data
        count = len(data) // 2
        result = bytearray(len(data))
        for i in range(count):
            sample = struct.unpack_from("<h", data, i * 2)[0]
            val = int(sample * factor)
            val = max(-32768, min(32767, val))
            struct.pack_into("<h", result, i * 2, val)
        return bytes(result)

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

    @property
    def is_playing(self):
        return self._playing
