# -*- coding: utf-8 -*-
"""
PCM 工具 —— Windows（pyaudio）和 Linux（pacat）两条播放路径共用。

都是纯函数，输入输出都是裸 PCM 字节串，不碰任何音频 API。
"""

import struct

# sampwidth -> pulse/pacat 的格式名
PULSE_FORMATS = {
    1: "u8",
    2: "s16le",
    3: "s24le",
    4: "s32le",
}


def downmix_mono(data: bytes, sampwidth: int, nchannels: int) -> bytes:
    """将多声道 PCM 数据降混为单声道。"""
    if nchannels <= 1:
        return data

    if nchannels == 2 and sampwidth == 2:
        n = len(data) // 4 * 2
        result = bytearray(n)
        for i in range(0, len(data) - 3, 4):
            ls = struct.unpack_from("<h", data, i)[0]
            rs = struct.unpack_from("<h", data, i + 2)[0]
            struct.pack_into("<h", result, i // 2, (ls + rs) // 2)
        return bytes(result)

    frame_size = sampwidth * nchannels
    result = bytearray()
    for i in range(0, len(data) - frame_size + 1, frame_size):
        total = 0
        for ch in range(nchannels):
            off = i + ch * sampwidth
            total += int.from_bytes(data[off:off + sampwidth], "little", signed=True)
        mono = total // nchannels
        result.extend(int(mono).to_bytes(sampwidth, "little", signed=True))
    return bytes(result)


def scale_volume(data: bytes, sampwidth: int, factor: float) -> bytes:
    """实时调整 PCM chunk 音量。只对 16-bit 生效（跟 Windows 侧原行为一致）。"""
    if sampwidth != 2 or abs(factor - 1.0) <= 0.01:
        return data

    count = len(data) // 2
    result = bytearray(len(data))
    for i in range(count):
        sample = struct.unpack_from("<h", data, i * 2)[0]
        val = int(sample * factor)
        val = max(-32768, min(32767, val))
        struct.pack_into("<h", result, i * 2, val)
    return bytes(result)
