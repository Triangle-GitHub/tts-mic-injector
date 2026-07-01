# -*- coding: utf-8 -*-
"""
EspeakEngine — eSpeak NG 本地 TTS 引擎（最快、最轻量）。
"""

import os
import sys
import wave
import struct
import tempfile
import subprocess
import logging

from engines.base import TTSEngine
from config import ESPEAK_PATH, SPEED_MIN, SPEED_MAX, ESPEAK_SYNTH_TIMEOUT

logger = logging.getLogger("TTSMicInjector")


class EspeakEngine(TTSEngine):
    """eSpeak NG 引擎（最快、最轻量）。"""
    name = "eSpeak"

    def __init__(self):
        self._check_exists()

    def _check_exists(self):
        search_paths = [ESPEAK_PATH]
        if not os.path.isfile(ESPEAK_PATH):
            for p in os.environ.get("PATH", "").split(os.pathsep):
                candidate = os.path.join(p, ESPEAK_PATH)
                if os.path.isfile(candidate):
                    search_paths.append(candidate)
                    break
            else:
                for p in os.environ.get("PATH", "").split(os.pathsep):
                    candidate = os.path.join(p, "espeak-ng")
                    if os.path.isfile(candidate):
                        search_paths.append(candidate)
                        break

        self._exe_path = None
        for p in search_paths:
            if os.path.isfile(p):
                self._exe_path = p
                break
        if not self._exe_path:
            raise FileNotFoundError(
                "未找到 espeak-ng.exe。请从 https://github.com/espeak-ng/espeak-ng/releases 下载并放入 PATH。"
            )
        logger.info(f"eSpeak NG 路径: {self._exe_path}")

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
        os.close(fd)
        txt_path = None

        try:
            fd, txt_path = tempfile.mkstemp(suffix=".txt", prefix="tts_")
            os.write(fd, text.encode("utf-8"))
            os.close(fd)

            voice = "cmn"
            proc = subprocess.Popen(
                [self._exe_path, "-v", voice, "-b", "1", "-s", str(int(speed)),
                 "-w", wav_path, "-f", txt_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = proc.communicate(timeout=ESPEAK_SYNTH_TIMEOUT)

            if proc.returncode != 0:
                proc = subprocess.Popen(
                    [self._exe_path, "-v", "zh", "-b", "1", "-s", str(int(speed)),
                     "-w", wav_path, "-f", txt_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = proc.communicate(timeout=ESPEAK_SYNTH_TIMEOUT)
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"eSpeak NG 合成失败 (return code {proc.returncode}):\n{stderr.decode('utf-8', errors='replace')}"
                    )

            if volume < 0.99:
                self._adjust_volume(wav_path, volume)

            return wav_path

        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("eSpeak NG 合成超时")
        except Exception:
            if os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise
        finally:
            if txt_path and os.path.exists(txt_path):
                try:
                    os.unlink(txt_path)
                except OSError:
                    pass

    def _adjust_volume(self, wav_path: str, factor: float):
        try:
            with wave.open(wav_path, "rb") as wf:
                params = wf.getparams()
                frames = bytearray(wf.readframes(wf.getnframes()))

            samples = struct.iter_unpack("<h", frames)
            adjusted = bytearray()
            for (sample,) in samples:
                val = int(sample * factor)
                val = max(-32768, min(32767, val))
                adjusted.extend(struct.pack("<h", val))

            with wave.open(wav_path, "wb") as wf:
                wf.setparams(params)
                wf.writeframes(bytes(adjusted))
        except Exception as e:
            logger.warning(f"音量调整失败: {e}")

    def get_speed_range(self):
        return (SPEED_MIN, SPEED_MAX)
