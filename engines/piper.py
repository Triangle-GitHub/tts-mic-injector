# -*- coding: utf-8 -*-
"""
PiperEngine — Piper 本地神经网络 TTS 引擎。
"""

import os
import sys
import tempfile
import subprocess
import logging

from engines.base import TTSEngine
from config import PIPER_PATH, PIPER_MODEL_DIR, PIPER_SYNTH_TIMEOUT, PIPER_LENGTH_SCALE_MIN, PIPER_LENGTH_SCALE_MAX

_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

logger = logging.getLogger("TTSMicInjector")


class PiperEngine(TTSEngine):
    """Piper 本地神经网络 TTS 引擎。"""
    name = "Piper"

    def __init__(self):
        self._exe_path = None
        self._models = {}
        self._current_model_name = None
        self._current_model_path = None
        self._current_config_path = None

        self._find_exe()
        self._find_models()

        if not self._models:
            raise RuntimeError(
                "未找到 Piper 模型。请下载 .onnx 模型文件放入 piper_models/ 目录。\n"
                "下载地址: https://huggingface.co/rhasspy/piper-voices"
            )

        logger.info(f"Piper 就绪，exe: {self._exe_path}, 模型: {len(self._models)} 个")

    def _find_exe(self):
        search_paths = [PIPER_PATH]
        for p in os.environ.get("PATH", "").split(os.pathsep):
            for name in (PIPER_PATH, "piper", "piper.exe"):
                candidate = os.path.join(p, name)
                if os.path.isfile(candidate):
                    search_paths.append(candidate)

        for p in search_paths:
            if os.path.isfile(p):
                self._exe_path = p
                return

        raise FileNotFoundError(
            "未找到 piper.exe。请从 https://github.com/rhasspy/piper/releases 下载并放入 PATH。"
        )

    def _find_models(self):
        model_dirs = [PIPER_MODEL_DIR]
        if self._exe_path:
            exe_dir = os.path.dirname(self._exe_path)
            model_dirs.append(os.path.join(exe_dir, "models"))
            model_dirs.append(exe_dir)

        seen = set()
        for model_dir in model_dirs:
            if not os.path.isdir(model_dir):
                continue
            for f in sorted(os.listdir(model_dir)):
                if f.endswith(".onnx") and f not in seen:
                    seen.add(f)
                    model_path = os.path.join(model_dir, f)
                    name = f[:-5]
                    config_path = model_path + ".json"
                    if not os.path.isfile(config_path):
                        alt_config = os.path.join(model_dir, name + ".onnx.json")
                        if os.path.isfile(alt_config):
                            config_path = alt_config
                        else:
                            config_path = os.path.join(model_dir, name + ".json") if os.path.isfile(os.path.join(model_dir, name + ".json")) else None
                    self._models[name] = (model_path, config_path)

        if self._models:
            first = list(self._models.keys())[0]
            self._current_model_name = first
            self._current_model_path, self._current_config_path = self._models[first]

    def get_voices(self):
        return [(name, name) for name in self._models.keys()]

    def set_voice(self, voice_name):
        if voice_name in self._models:
            self._current_model_name = voice_name
            self._current_model_path, self._current_config_path = self._models[voice_name]

    def get_current_voice(self):
        return self._current_model_name or ""

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_piper_")
        os.close(fd)

        try:
            length_scale = 100.0 / max(speed, 1.0)
            length_scale = max(PIPER_LENGTH_SCALE_MIN, min(PIPER_LENGTH_SCALE_MAX, length_scale))

            cmd = [
                self._exe_path,
                "--model", self._current_model_path,
                "--output_file", wav_path,
                "--length_scale", f"{length_scale:.2f}",
            ]
            if self._current_config_path:
                cmd += ["--config", self._current_config_path]

            result = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=PIPER_SYNTH_TIMEOUT,
                creationflags=_CREATION_FLAGS,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Piper 合成失败 (return code {result.returncode}):\n"
                    f"{result.stderr.decode('utf-8', errors='replace')}"
                )

            return wav_path

        except subprocess.TimeoutExpired:
            raise RuntimeError("Piper 合成超时")
        except Exception:
            if os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise

    def get_speed_range(self):
        return (50, 200)
