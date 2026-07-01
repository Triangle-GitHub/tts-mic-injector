# -*- coding: utf-8 -*-
"""
TTSEngine — 所有 TTS 引擎的抽象基类。
"""


class TTSEngine:
    """所有引擎的基类。"""
    name = "base"

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        """合成语音并返回 WAV 文件路径。返回 None 表示失败。"""
        raise NotImplementedError

    def get_speed_range(self):
        return (0.5, 2.0)

    def get_volume_supported(self):
        return True

    def get_voices(self):
        return []

    def set_voice(self, voice_id):
        pass

    def set_pitch(self, pitch_hz: float):
        pass

    def get_current_voice(self):
        return ""
