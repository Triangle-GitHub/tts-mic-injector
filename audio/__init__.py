# -*- coding: utf-8 -*-
"""音频后端选择 + 平台无关的小工具。

Windows → audio/player.py（pyaudio + VB-Cable 虚拟声卡）
其他平台 → audio/linux_audio.py（pacat + PipeWire 虚拟麦克风）

两个后端暴露完全同形的接口，上层（service / UI）只跟本模块打交道，
不出现任何 `sys.platform` 判断。设备统一用 AudioDevice 表示。
"""

import sys

from audio.device import AudioDevice

if sys.platform == "win32":
    from audio.player import AudioPlayer  # noqa: F401
else:
    from audio.linux_audio import AudioPlayer  # noqa: F401


def find_virtual_mic() -> AudioDevice:
    """找到（必要时创建）虚拟麦克风。失败抛 RuntimeError。"""
    return AudioPlayer.find_virtual_mic()


def list_output_devices() -> list:
    """所有可用输出设备 [AudioDevice, ...]，已排除虚拟麦克风自身。"""
    return AudioPlayer.list_output_devices()


def default_output_device() -> AudioDevice:
    """系统默认输出设备。"""
    return AudioPlayer.default_output_device()


def virtual_source_name() -> str:
    """虚拟麦克风在**接收方**那边显示的名字（给用户提示用）。"""
    return AudioPlayer.virtual_source_name()


def virtual_source_desc() -> str:
    return AudioPlayer.virtual_source_desc()


__all__ = [
    "AudioPlayer",
    "AudioDevice",
    "find_virtual_mic",
    "list_output_devices",
    "default_output_device",
    "virtual_source_name",
    "virtual_source_desc",
]
