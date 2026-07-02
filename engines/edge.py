# -*- coding: utf-8 -*-
"""
EdgeEngine — Microsoft Edge TTS 云端引擎。
"""

import os
import sys
import tempfile
import subprocess
import threading
import logging

from engines.base import TTSEngine
from config import EDGE_DEFAULT_VOICE, FFMPEG_PATH, EDGE_PITCH_MIN, EDGE_PITCH_MAX

_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

try:
    import asyncio
except ImportError:
    asyncio = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

logger = logging.getLogger("TTSMicInjector")


class EdgeEngine(TTSEngine):
    """Microsoft Edge TTS 云端引擎。"""
    name = "Edge"

    _FALLBACK_VOICES = [
        {"id": "zh-CN-XiaoxiaoNeural", "locale": "zh-CN", "display": "XiaoxiaoNeural (Female)", "gender": "Female"},
        {"id": "zh-CN-YunxiNeural", "locale": "zh-CN", "display": "YunxiNeural (Male)", "gender": "Male"},
        {"id": "zh-CN-YunjianNeural", "locale": "zh-CN", "display": "YunjianNeural (Male)", "gender": "Male"},
        {"id": "zh-CN-XiaoyiNeural", "locale": "zh-CN", "display": "XiaoyiNeural (Female)", "gender": "Female"},
        {"id": "zh-CN-YunyangNeural", "locale": "zh-CN", "display": "YunyangNeural (Male)", "gender": "Male"},
        {"id": "zh-CN-XiaochenNeural", "locale": "zh-CN", "display": "XiaochenNeural (Female)", "gender": "Female"},
        {"id": "zh-TW-HsiaoChenNeural", "locale": "zh-TW", "display": "HsiaoChenNeural (Female)", "gender": "Female"},
        {"id": "zh-TW-YunJheNeural", "locale": "zh-TW", "display": "YunJheNeural (Male)", "gender": "Male"},
        {"id": "zh-HK-HiuMaanNeural", "locale": "zh-HK", "display": "HiuMaanNeural (Female)", "gender": "Female"},
        {"id": "zh-HK-WanLungNeural", "locale": "zh-HK", "display": "WanLungNeural (Male)", "gender": "Male"},
        {"id": "en-US-JennyNeural", "locale": "en-US", "display": "JennyNeural (Female)", "gender": "Female"},
        {"id": "en-US-GuyNeural", "locale": "en-US", "display": "GuyNeural (Male)", "gender": "Male"},
        {"id": "en-US-AriaNeural", "locale": "en-US", "display": "AriaNeural (Female)", "gender": "Female"},
    ]

    def __init__(self):
        if edge_tts is None:
            raise RuntimeError("edge-tts 未安装。请执行: pip install edge-tts")
        if asyncio is None:
            raise RuntimeError("asyncio 不可用")

        self._voices = list(self._FALLBACK_VOICES)
        self._current_voice = EDGE_DEFAULT_VOICE
        self._pitch_hz = 0
        self._voices_ready = threading.Event()

        threading.Thread(target=self._fetch_voices_bg, daemon=True).start()
        logger.info(f"Edge TTS 就绪（离线 {len(self._voices)} 个语音，正在后台获取在线列表...）")

    @property
    def voices_ready(self):
        return self._voices_ready.is_set()

    def _fetch_voices_bg(self):
        try:
            loop = asyncio.new_event_loop()
            voices = loop.run_until_complete(self._list_voices_async())
            loop.close()
            if voices:
                self._voices = voices
            logger.info(f"Edge TTS 在线语音列表已更新，{len(self._voices)} 个语音")
        except Exception as e:
            logger.error(f"获取Edge语音列表失败: {e}")
        finally:
            self._voices_ready.set()

    async def _list_voices_async(self):
        voices = await edge_tts.list_voices()
        result = []
        for v in voices:
            sid = v["ShortName"]
            locale = v.get("Locale", "")
            role = sid.split("-", 2)[-1] if "-" in sid else sid
            result.append({
                "id": sid,
                "locale": locale,
                "display": f"{role} ({v.get('Gender', '?')})",
                "gender": v.get("Gender", ""),
            })
        return result

    def get_voices(self):
        return [(v["id"], f"{v['locale']} {v['display']}") for v in self._voices]

    def get_locales(self):
        locales = list(dict.fromkeys(v["locale"] for v in self._voices))
        priority = ["zh-CN", "en-US"]
        for p in reversed(priority):
            if p in locales:
                locales.remove(p)
                locales.insert(0, p)
        return locales

    def get_voices_for_locale(self, locale):
        return [(v["id"], v["display"]) for v in self._voices if v["locale"] == locale]

    def set_voice(self, voice_name):
        self._current_voice = voice_name

    def get_current_voice(self):
        return self._current_voice

    def set_pitch(self, pitch_hz: float):
        self._pitch_hz = pitch_hz

    def get_pitch_range(self):
        return (EDGE_PITCH_MIN, EDGE_PITCH_MAX)

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        rate_pct = int(speed - 100)
        rate_str = f"{rate_pct:+d}%"
        vol_str = "+0%"
        pitch_str = f"{int(self._pitch_hz):+d}Hz"

        logger.debug(f"Edge合成: voice={self._current_voice}, rate={rate_str}, vol={vol_str}, pitch={pitch_str}")

        mp3_path = None
        wav_path = None
        try:
            fd, mp3_path = tempfile.mkstemp(suffix=".mp3", prefix="tts_edge_")
            os.close(fd)
            fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_edge_")
            os.close(fd)

            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                self._async_synthesize(text, self._current_voice, rate_str, vol_str, pitch_str, mp3_path)
            )
            loop.close()

            self._mp3_to_wav(mp3_path, wav_path)
            return wav_path
        except Exception:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise
        finally:
            if mp3_path and os.path.exists(mp3_path):
                try:
                    os.unlink(mp3_path)
                except OSError:
                    pass

    async def _async_synthesize(self, text, voice, rate, volume, pitch, output_path):
        communicate = edge_tts.Communicate(
            text=text, voice=voice, rate=rate, volume=volume, pitch=pitch
        )
        await communicate.save(output_path)

    def _mp3_to_wav(self, mp3_path, wav_path):
        try:
            result = subprocess.run(
                [FFMPEG_PATH, "-y", "-i", mp3_path,
                 "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
                 wav_path],
                capture_output=True,
                creationflags=_CREATION_FLAGS,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg 转换失败:\n{result.stderr.decode('utf-8', errors='replace')}"
                )
        except FileNotFoundError:
            raise RuntimeError(
                "未找到 ffmpeg。Edge TTS 需要 ffmpeg 将 MP3 转换为 WAV。\n"
                "请安装 ffmpeg: https://ffmpeg.org/download.html"
            )

    def get_speed_range(self):
        return (50, 200)
