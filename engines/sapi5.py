# -*- coding: utf-8 -*-
"""
SystemTTSEngine — Windows SAPI5 引擎，通过 COM 接口直接调用。
每次合成都启动独立 COM 线程，用完即销毁。
"""

import os
import threading
import tempfile
import logging

from engines.base import TTSEngine
from config import SAPI5_VOICES_TIMEOUT, SAPI5_SYNTH_TIMEOUT

try:
    import pythoncom
except ImportError:
    pythoncom = None

logger = logging.getLogger("TTSMicInjector")


class SystemTTSEngine(TTSEngine):
    """系统 TTS 引擎 — 直接调用 SAPI5 COM 接口，绕过 pyttsx3 的 event loop 问题。"""
    name = "SAPI5"

    _SAPI_RATE_MIN, _SAPI_RATE_MAX = -10, 10
    _RATE_CENTER = 225.0
    _RATE_SCALE = 17.5

    def __init__(self):
        if pythoncom is None:
            raise RuntimeError("pythoncom 未安装。请执行: pip install pywin32")

        try:
            from win32com.client import Dispatch
            self._Dispatch = Dispatch
        except ImportError:
            raise RuntimeError("win32com 未安装。请执行: pip install pywin32")

        self._current_voice_index = 0
        self._error = None
        self._voices_done = threading.Event()

        def _get_voices():
            try:
                pythoncom.CoInitialize()
            except Exception as e:
                self._error = str(e)
                self._voices_done.set()
                return
            try:
                voice = self._Dispatch("SAPI.SpVoice")
                voices = voice.GetVoices()
                self._voice_list = []
                for i in range(voices.Count):
                    v = voices.Item(i)
                    self._voice_list.append((i, v.GetDescription()))
            except Exception as e:
                self._error = str(e)
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
                self._voices_done.set()

        threading.Thread(target=_get_voices, daemon=True).start()
        self._voices_done.wait(timeout=SAPI5_VOICES_TIMEOUT)

        if self._error:
            raise RuntimeError(self._error)
        if not self._voice_list:
            raise RuntimeError("未找到系统语音")

        self._voices = self._voice_list
        logger.info(f"SAPI5 引擎就绪，{len(self._voices)} 个语音可用")

    def get_voices(self):
        return [(vid, name) for vid, name in self._voices]

    def set_voice(self, voice_index):
        self._current_voice_index = int(voice_index)

    def get_current_voice(self):
        return str(self._current_voice_index)

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_system_")
        os.close(fd)

        voice_index = self._current_voice_index
        sapi_rate = round((speed - self._RATE_CENTER) / self._RATE_SCALE)
        sapi_rate = max(self._SAPI_RATE_MIN, min(self._SAPI_RATE_MAX, sapi_rate))
        sapi_vol = int(volume * 100)

        done = threading.Event()
        error = []

        def _synth_thread():
            try:
                pythoncom.CoInitialize()
            except Exception as e:
                error.append(e)
                done.set()
                return
            try:
                voice = self._Dispatch("SAPI.SpVoice")
                all_voices = voice.GetVoices()
                if voice_index < all_voices.Count:
                    voice.Voice = all_voices.Item(voice_index)
                voice.Rate = sapi_rate
                voice.Volume = sapi_vol

                stream = self._Dispatch("SAPI.SpFileStream")
                stream.Open(wav_path, 3)
                voice.AudioOutputStream = stream

                logger.debug(f"SAPI5 Speak: rate={sapi_rate}, vol={sapi_vol}, voice={voice_index}")
                voice.Speak(text)

                stream.Close()
                logger.debug("SAPI5 Speak 完成")
            except Exception as e:
                logger.debug(f"SAPI5 合成异常: {e}")
                error.append(e)
                if os.path.exists(wav_path):
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
                done.set()

        t = threading.Thread(target=_synth_thread, daemon=True)
        t.start()
        if not done.wait(timeout=SAPI5_SYNTH_TIMEOUT):
            raise RuntimeError("SAPI5 合成超时")

        if error:
            if os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise error[0]
        return wav_path

    def stop(self):
        pass

    def get_speed_range(self):
        return (50, 400)
