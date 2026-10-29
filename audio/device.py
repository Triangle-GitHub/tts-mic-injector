# -*- coding: utf-8 -*-
"""
AudioDevice —— 音频设备的一层薄类型。

存在的理由：Windows 侧的设备标识是 pyaudio 给的整数下标，Linux 侧是
PipeWire/Pulse 的 sink 名字（字符串）。上层（service / UI）不该关心这个差别，
也不该在每次枚举之后维护一张「下标 → 名字」的映射表（那种全局可变状态在设备
插拔时会漂）。所以两端统一交出一个对象：

    AudioDevice(id=..., label=..., is_virtual=...)

- Windows: id = pyaudio 的 output_device_index（int）
- Linux:   id = pactl 的 sink 名（str）

`is_virtual` 标记「这就是我们的虚拟麦克风」，UI 据此把自己排除在监听列表外。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioDevice:
    """一个可用于播放的设备。不可变，可以安全地跨线程/跨层传递。"""

    id: object
    label: str
    is_virtual: bool = False

    def __str__(self) -> str:
        return self.label

    def __repr__(self) -> str:
        kind = "virtual" if self.is_virtual else "output"
        return f"<AudioDevice {kind} id={self.id!r} label={self.label!r}>"
