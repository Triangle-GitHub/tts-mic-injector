#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS Mic Injector
将文字通过 TTS 合成后输出到 VB-Cable 虚拟麦克风，用于微信等 VoIP 通话。
支持 eSpeak NG 和系统 TTS (pyttsx3 / SAPI5) 两种引擎。
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import subprocess
import tempfile
import os
import sys
import logging
import wave
import struct
import queue
import shutil
import re
from datetime import datetime
from pathlib import Path
import json
import base64

# ── 可选依赖 ──────────────────────────────────────────
try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

try:
    import pythoncom
except ImportError:
    pythoncom = None

try:
    import asyncio
except ImportError:
    asyncio = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import dashscope
    from dashscope.audio.qwen_tts_realtime import (
        QwenTtsRealtime, QwenTtsRealtimeCallback, AudioFormat
    )
except ImportError:
    dashscope = None
    QwenTtsRealtime = None
    QwenTtsRealtimeCallback = None
    AudioFormat = None

# ── 配置 ──────────────────────────────────────────────
ESPEAK_PATH = "espeak-ng.exe"          # 需在 PATH 或同目录
VB_CABLE_KEYWORDS = ["CABLE Input"]
SPEED_DEFAULT = 175                    # eSpeak 默认语速
SPEED_MIN = 80
SPEED_MAX = 450
VOLUME_MAX = 1.0
LOG_MAX_LINES = 200

PIPER_PATH = "piper.exe"
PIPER_MODEL_DIR = "piper_models"
EDGE_DEFAULT_VOICE = "zh-CN-YunxiNeural"
ALIYUN_CONFIG_PATH = Path(__file__).parent / "config.json"

# ── 日志 ──────────────────────────────────────────────
logger = logging.getLogger("TTSMicInjector")
logger.setLevel(logging.DEBUG)


class TextHandler(logging.Handler):
    """将日志输出到 tkinter Text 控件。"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record) + "\n"
        self.text_widget.after(0, self._append, msg)

    def _append(self, msg):
        try:
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.insert(tk.END, msg)
            # 限制行数
            lines = self.text_widget.get("1.0", tk.END).split("\n")
            if len(lines) > LOG_MAX_LINES:
                self.text_widget.delete("1.0", f"{len(lines) - LOG_MAX_LINES}.0")
            self.text_widget.see(tk.END)
            self.text_widget.config(state=tk.DISABLED)
        except tk.TclError:
            pass


# ── TTS 引擎抽象 ─────────────────────────────────────
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


class EspeakEngine(TTSEngine):
    """eSpeak NG 引擎（最快、最轻量）。"""
    name = "eSpeak"

    def __init__(self):
        self._check_exists()

    def _check_exists(self):
        """检查 espeak-ng.exe 是否存在。"""
        # 搜索 PATH 和当前目录
        search_paths = [ESPEAK_PATH]
        if not os.path.isfile(ESPEAK_PATH):
            for p in os.environ.get("PATH", "").split(os.pathsep):
                candidate = os.path.join(p, ESPEAK_PATH)
                if os.path.isfile(candidate):
                    search_paths.append(candidate)
                    break
            else:
                # 尝试不带 .exe
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
        """
        调用 eSpeak NG 生成 WAV 文件。
        speed: eSpeak 的语速值 80~450（GUI 滑块会映射）
        volume: 0.0~1.0，在 PCM 层面缩放
        """
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
        os.close(fd)
        txt_path = None

        try:
            # 将文本写入临时文件（避免 --stdin 在 Windows 上截断末尾字符）
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
            stdout, stderr = proc.communicate(timeout=10)

            if proc.returncode != 0:
                proc = subprocess.Popen(
                    [self._exe_path, "-v", "zh", "-b", "1", "-s", str(int(speed)),
                     "-w", wav_path, "-f", txt_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = proc.communicate(timeout=10)
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
        """调整 WAV 文件的音量。"""
        try:
            with wave.open(wav_path, "rb") as wf:
                params = wf.getparams()
                frames = bytearray(wf.readframes(wf.getnframes()))

            # 16-bit PCM
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


class SystemTTSEngine(TTSEngine):
    """系统 TTS 引擎 — 直接调用 SAPI5 COM 接口，绕过 pyttsx3 的 event loop 问题。
    每次合成启动独立 COM 线程，用完即销毁。
    """
    name = "SAPI5"

    # SAPI Rate 映射常量
    _SAPI_RATE_MIN, _SAPI_RATE_MAX = -10, 10
    _RATE_CENTER = 225.0  # 对应 SAPI rate=0
    _RATE_SCALE = 17.5    # (400-50)/(10-(-10)) 

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
        self._voices_done.wait(timeout=10)

        if self._error:
            raise RuntimeError(self._error)
        if not self._voice_list:
            raise RuntimeError("未找到系统语音")

        self._voices = self._voice_list
        logger.info(f"SAPI5 引擎就绪，{len(self._voices)} 个语音可用")

    def get_voices(self):
        return [(vid, name) for vid, name in self._voices]

    def set_voice(self, voice_index):
        """voice_index: 整数或可转为整数的字符串。"""
        self._current_voice_index = int(voice_index)

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_system_")
        os.close(fd)

        voice_index = self._current_voice_index
        # 映射语速: GUI 50-400 → SAPI Rate -10~10
        sapi_rate = round((speed - self._RATE_CENTER) / self._RATE_SCALE)
        sapi_rate = max(self._SAPI_RATE_MIN, min(self._SAPI_RATE_MAX, sapi_rate))
        sapi_vol = int(volume * 100)  # 0~100

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
                # 按索引设置语音
                all_voices = voice.GetVoices()
                if voice_index < all_voices.Count:
                    voice.Voice = all_voices.Item(voice_index)
                voice.Rate = sapi_rate
                voice.Volume = sapi_vol

                # 创建文件流
                stream = self._Dispatch("SAPI.SpFileStream")
                stream.Open(wav_path, 3)  # SSFMCreateForWrite
                voice.AudioOutputStream = stream

                logger.debug(f"SAPI5 Speak: rate={sapi_rate}, vol={sapi_vol}, voice={voice_index}")
                voice.Speak(text)  # 同步，等说完才返回

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
        if not done.wait(timeout=60):
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
                            config_path = None
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

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_piper_")
        os.close(fd)

        try:
            length_scale = 100.0 / max(speed, 1.0)
            length_scale = max(0.2, min(5.0, length_scale))

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
                timeout=60,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Piper 合成失败 (return code {result.returncode}):\n"
                    f"{result.stderr.decode('utf-8', errors='replace')}"
                )

            if volume < 0.99:
                self._adjust_volume(wav_path, volume)

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
            logger.warning(f"Piper 音量调整失败: {e}")

    def get_speed_range(self):
        return (50, 200)


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

    def set_pitch(self, pitch_hz: float):
        self._pitch_hz = pitch_hz

    def get_pitch_range(self):
        return (-50, 50)

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        rate_pct = int(speed - 100)
        rate_str = f"{rate_pct:+d}%"
        vol_pct = int((volume - 0.5) * 200)
        vol_str = f"{vol_pct:+d}%"
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
                ["ffmpeg", "-y", "-i", mp3_path,
                 "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
                 wav_path],
                capture_output=True, timeout=30
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


# ── Aliyun TTS ───────────────────────────────────────
class _AliyunCallback(QwenTtsRealtimeCallback):
    """收集 PCM 数据到文件。"""

    def __init__(self, pcm_path):
        super().__init__()
        self._file = open(pcm_path, "wb")
        self._complete = threading.Event()

    def on_open(self):
        logger.debug("Aliyun TTS 连接已建立")

    def on_event(self, response):
        try:
            t = response["type"]
            if t == "response.audio.delta":
                self._file.write(base64.b64decode(response["delta"]))
            elif t == "response.done":
                logger.debug(f"Aliyun TTS response done: {response.get('response', {}).get('id', '')}")
            elif t == "session.finished":
                logger.debug("Aliyun TTS session finished")
                self._file.close()
                self._complete.set()
        except Exception as e:
            logger.debug(f"Aliyun cb on_event error: {e}")

    def on_close(self, close_status_code, close_msg):
        logger.debug(f"Aliyun TTS 连接关闭: code={close_status_code}, msg={close_msg}")
        if not self._file.closed:
            self._file.close()
        if not self._complete.is_set():
            self._complete.set()

    def wait_for_finished(self, timeout=120):
        self._complete.wait(timeout=timeout)


class AliyunEngine(TTSEngine):
    """阿里云 Qwen TTS 实时引擎。"""
    name = "Aliyun"

    VOICES = [
        ("Cherry", "Cherry - 芊悦，阳光积极、亲切自然小姐姐"),
        ("Serena", "Serena - 苏瑶，温柔小姐姐"),
        ("Ethan", "Ethan - 晨煦，阳光温暖男声"),
        ("Chelsie", "Chelsie - 千雪，二次元虚拟女友"),
        ("Momo", "Momo - 茉兔，撒娇搞怪"),
        ("Vivian", "Vivian - 十三，拽拽可爱小暴躁"),
        ("Moon", "Moon - 月白，率性帅气男声"),
        ("Maia", "Maia - 四月，知性温柔"),
        ("Kai", "Kai - 凯，耳朵SPA男声"),
        ("Nofish", "Nofish - 不吃鱼，不会翘舌音设计师"),
        ("Bella", "Bella - 萌宝，小萝莉"),
        ("Jennifer", "Jennifer - 詹妮弗，电影质感美语女声"),
        ("Ryan", "Ryan - 甜茶，戏感炸裂男声"),
        ("Katerina", "Katerina - 卡捷琳娜，御姐"),
        ("Aiden", "Aiden - 艾登，美语大男孩"),
        ("Eldric Sage", "Eldric Sage - 沧明子，沉稳睿智老者"),
        ("Mia", "Mia - 乖小妹，温顺乖巧"),
        ("Mochi", "Mochi - 沙小弥，聪明伶俐小大人"),
        ("Bellona", "Bellona - 燕铮莺，字正腔圆女声"),
        ("Vincent", "Vincent - 田叔，沙哑烟嗓"),
        ("Bunny", "Bunny - 萌小姬，萌属性小萝莉"),
        ("Neil", "Neil - 阿闻，专业新闻主持人"),
        ("Elias", "Elias - 墨讲师，严谨又生动"),
        ("Arthur", "Arthur - 徐大爷，质朴嗓音"),
        ("Nini", "Nini - 邻家妹妹，又软又黏"),
        ("Seren", "Seren - 小婉，温和舒缓助眠"),
        ("Pip", "Pip - 顽屁小孩，调皮捣蛋"),
        ("Stella", "Stella - 少女阿月，甜到发腻"),
        ("Bodega", "Bodega - 博德加，热情西班牙大叔"),
        ("Sonrisa", "Sonrisa - 索尼莎，拉美大姐"),
        ("Alek", "Alek - 阿列克，战斗民族型男"),
        ("Dolce", "Dolce - 多尔切，慵懒意大利大叔"),
        ("Sohee", "Sohee - 素熙，韩国欧尼"),
        ("Ono Anna", "Ono Anna - 小野杏，鬼灵精怪青梅竹马"),
        ("Lenn", "Lenn - 莱恩，德国青年"),
        ("Emilien", "Emilien - 埃米尔安，法国大哥哥"),
        ("Andre", "Andre - 安德雷，磁性沉稳男声"),
        ("Radio Gol", "Radio Gol - 足球诗人解说"),
        ("Jada", "Jada - 上海阿珍，风风火火沪上阿姐"),
        ("Dylan", "Dylan - 北京晓东，胡同少年"),
        ("Li", "Li - 南京老李，瑜伽老师"),
        ("Marcus", "Marcus - 陕西秦川，老陕"),
        ("Roy", "Roy - 闽南阿杰，台湾哥仔"),
        ("Peter", "Peter - 天津李彼得，相声捧哏"),
        ("Sunny", "Sunny - 四川晴儿，甜到心里的川妹子"),
        ("Eric", "Eric - 四川程川，成都男子"),
        ("Rocky", "Rocky - 粤语阿强，幽默风趣"),
        ("Kiki", "Kiki - 粤语阿清，港妹闺蜜"),
    ]

    def __init__(self):
        if dashscope is None:
            raise RuntimeError("dashscope 未安装。请执行: pip install dashscope")

        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        config = self._load_config()
        if config.get("api_key"):
            api_key = config["api_key"]

        if not api_key:
            raise RuntimeError(
                "未配置 DashScope API Key。\n"
                "  方法1: 设置环境变量 DASHSCOPE_API_KEY\n"
                "  方法2: 在 config.json 中设置 api_key 字段"
            )
        dashscope.api_key = api_key

        self._model = config.get("model", "qwen3-tts-flash-realtime")
        self._voice = config.get("voice", "Ethan")
        logger.info(f"Aliyun TTS 就绪，模型: {self._model}, 语音: {self._voice}")

    def _load_config(self):
        try:
            if ALIYUN_CONFIG_PATH.exists():
                with open(ALIYUN_CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载 config.json 失败: {e}")
        return {}

    def get_voices(self):
        return [(v[0], v[1]) for v in self.VOICES]

    def set_voice(self, voice_name):
        self._voice = voice_name

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, pcm_path = tempfile.mkstemp(suffix=".pcm", prefix="tts_aliyun_")
        os.close(fd)
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_aliyun_")
        os.close(fd)

        try:
            callback = _AliyunCallback(pcm_path)

            rt = QwenTtsRealtime(model=self._model, callback=callback)
            rt.connect()
            rt.update_session(
                voice=self._voice,
                response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                mode="server_commit",
            )
            rt.append_text(text)
            rt.finish()
            callback.wait_for_finished()

            with open(pcm_path, "rb") as f:
                pcm_data = f.read()

            if volume < 0.99:
                pcm_data = self._adjust_pcm_volume(pcm_data, volume)

            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(pcm_data)

            return wav_path

        except Exception:
            if os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise
        finally:
            if os.path.exists(pcm_path):
                try:
                    os.unlink(pcm_path)
                except OSError:
                    pass

    def _adjust_pcm_volume(self, pcm_data: bytes, factor: float) -> bytes:
        count = len(pcm_data) // 2
        result = bytearray(len(pcm_data))
        for i in range(count):
            sample = struct.unpack_from("<h", pcm_data, i * 2)[0]
            val = int(sample * factor)
            val = max(-32768, min(32767, val))
            struct.pack_into("<h", result, i * 2, val)
        return bytes(result)

    def get_speed_range(self):
        return None


# ── 音频播放器 ────────────────────────────────────────
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
        """
        播放 WAV 文件到 VB-Cable，可选同时输出到默认播放设备（监听）。
        volume_getter: 无参 callable，返回 0.0~1.0 音量系数，None 则保持原样。
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

            # 尝试用原始声道数打开 VB-Cable，多声道失败则降级为单声道
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

            # 启用监听时，同时输出到指定或默认播放设备
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


# ── 主应用 ────────────────────────────────────────────
class TTSMicInjectorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TTS Mic Injector — eSpeak NG / SAPI5")
        self.root.geometry("720x620")
        self.root.minsize(600, 520)

        # 状态变量
        self._stop_event = threading.Event()
        self._current_wav = None
        self._is_playing = False
        self._playback_gen = 0  # 防止旧线程回调干扰新播放
        self._vb_device_index = None  # VB-Cable 设备索引，启动时检测
        self._monitor_enabled = tk.BooleanVar(value=True)
        self._monitor_device_var = tk.StringVar(value="")
        self._monitor_devices = {}
        self._voice_var = tk.StringVar(value="")

        # 初始化引擎
        self.engine = None
        self._init_engine()

        # 构建 GUI
        self._build_ui()

        # 绑定事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", self._on_esc)

        # 启动时检查 VB-Cable
        self.root.after(300, self._check_vb_cable)
        # 默认监听开启，初始化监听设备下拉框
        self.root.after(200, self._populate_monitor_combo)

        logger.info("应用已启动")

    def _init_engine(self):
        """初始化 eSpeak 引擎。"""
        try:
            self.engine = EspeakEngine()
            logger.info(f"引擎就绪: {self.engine.name}")
        except FileNotFoundError as e:
            logger.error(str(e))
            self.engine = None

    def _build_ui(self):
        """构建界面。"""
        main_frame = ttk.Frame(self.root, padding=8)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── 历史记录区域 ──
        hist_frame = ttk.LabelFrame(main_frame, text="历史记录", padding=4)
        hist_frame.pack(fill=tk.X, pady=(0, 6))
        hist_container = ttk.Frame(hist_frame)
        hist_container.pack(fill=tk.X)
        hist_scrollbar = ttk.Scrollbar(hist_container, orient=tk.VERTICAL)
        self._hist_listbox = tk.Listbox(hist_container, height=5,
                                        yscrollcommand=hist_scrollbar.set,
                                        selectmode=tk.SINGLE)
        hist_scrollbar.config(command=self._hist_listbox.yview)
        hist_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._hist_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._hist_listbox.bind("<ButtonRelease-1>", self._on_history_click)
        btn_frame = ttk.Frame(hist_frame)
        btn_frame.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(btn_frame, text="清空", command=self._on_clear_history).pack(side=tk.LEFT, padx=2)

        # ── 输入区域 ──
        input_frame = ttk.LabelFrame(main_frame, text="输入文字（Enter 或 ▶ 发送，ESC 停止）", padding=4)
        input_frame.pack(fill=tk.X, pady=(0, 6))
        self._input_text = tk.Text(input_frame, height=3, font=("Microsoft YaHei", 11),
                                   wrap=tk.WORD, relief=tk.SUNKEN, borderwidth=1)
        self._input_text.pack(fill=tk.X)
        self._input_text.bind("<Return>", self._on_enter)
        self._input_text.bind("<Control-Return>", self._on_ctrl_enter)
        self._input_text.focus_set()

        # ── 控制栏 ──
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.pack(fill=tk.X, pady=(0, 6))

        # 播放按钮
        self._play_btn = ttk.Button(ctrl_frame, text="▶  播放", command=self._on_play)
        self._play_btn.pack(side=tk.LEFT, padx=(0, 12))

        # 停止按钮
        self._stop_btn = ttk.Button(ctrl_frame, text="■  停止", command=self._on_stop)
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 12))

        # 语速
        ttk.Label(ctrl_frame, text="语速:").pack(side=tk.LEFT)
        self._speed_var = tk.DoubleVar(value=SPEED_DEFAULT)
        self._speed_scale = ttk.Scale(
            ctrl_frame, from_=SPEED_MIN, to=SPEED_MAX, variable=self._speed_var,
            orient=tk.HORIZONTAL, length=180, command=self._on_speed_change
        )
        self._speed_scale.pack(side=tk.LEFT, padx=4)
        self._speed_label = ttk.Label(ctrl_frame, text=f"{SPEED_DEFAULT}")
        self._speed_label.pack(side=tk.LEFT, padx=(0, 12))

        # 音量
        ttk.Label(ctrl_frame, text="音量:").pack(side=tk.LEFT)
        self._vol_var = tk.DoubleVar(value=100)
        self._vol_scale = ttk.Scale(
            ctrl_frame, from_=0, to=100, variable=self._vol_var,
            orient=tk.HORIZONTAL, length=100, command=self._on_vol_change
        )
        self._vol_scale.pack(side=tk.LEFT, padx=4)
        self._vol_label = ttk.Label(ctrl_frame, text="100%")
        self._vol_label.pack(side=tk.LEFT)

        # ── TTS 引擎选择 ──
        engine_frame = ttk.LabelFrame(main_frame, text="TTS 引擎（点击即切换，不中断当前播放）", padding=4)
        engine_frame.pack(fill=tk.X, pady=(0, 6))

        self._engine_btns = {}
        engines = [
            ("Aliyun", True),
            ("Edge", True),
            ("SAPI5", True),
            ("eSpeak", True),
            ("Piper", True),
        ]
        for name, enabled in engines:
            btn = ttk.Button(engine_frame, text=name,
                             command=lambda n=name: self._switch_engine(n))
            btn.pack(side=tk.LEFT, padx=3)
            if not enabled:
                btn.config(state=tk.DISABLED)
            self._engine_btns[name] = btn

        # 当前引擎标签
        self._engine_label = ttk.Label(engine_frame, text=" 当前: eSpeak", foreground="#2a7a2a")
        self._engine_label.pack(side=tk.RIGHT, padx=6)

        # ── 系统语音选择（仅 SAPI5 可见） ──
        self._voice_frame = ttk.LabelFrame(main_frame, text="系统语音选择", padding=4)
        self._edge_locale_combo = ttk.Combobox(self._voice_frame, state="readonly", width=40)
        self._edge_locale_combo.bind("<<ComboboxSelected>>", self._on_edge_locale_select)
        self._voice_combo = ttk.Combobox(self._voice_frame, state="readonly",
                                          textvariable=self._voice_var, width=40)
        self._voice_combo.pack(fill=tk.X, padx=2, pady=2)
        self._voice_combo.bind("<<ComboboxSelected>>", self._on_voice_select)
        # 默认隐藏，切换 SAPI5 / Piper / Edge 时显示

        # ── Edge 音调（仅 Edge 可见） ──
        self._pitch_frame = ttk.LabelFrame(main_frame, text="Edge 音调", padding=4)
        ttk.Label(self._pitch_frame, text="音调:").pack(side=tk.LEFT)
        self._pitch_var = tk.DoubleVar(value=0)
        self._pitch_scale = ttk.Scale(
            self._pitch_frame, from_=-50, to=50, variable=self._pitch_var,
            orient=tk.HORIZONTAL, length=250, command=self._on_pitch_change
        )
        self._pitch_scale.pack(side=tk.LEFT, padx=4)
        self._pitch_label = ttk.Label(self._pitch_frame, text="0Hz")
        self._pitch_label.pack(side=tk.LEFT)
        # 默认隐藏，切换 Edge 时显示

        # ── 监听 + 状态 ──
        self._bottom_frame = ttk.Frame(main_frame)
        self._bottom_frame.pack(fill=tk.X, pady=(0, 6))

        self._monitor_cb = ttk.Checkbutton(
            self._bottom_frame, text="监听",
            variable=self._monitor_enabled,
            command=self._on_monitor_toggle,
        )
        self._monitor_cb.pack(side=tk.LEFT)

        self._monitor_combo = ttk.Combobox(
            self._bottom_frame, state="readonly",
            textvariable=self._monitor_device_var, width=35,
        )
        self._monitor_combo.pack(side=tk.LEFT, padx=(4, 12))
        self._monitor_combo.bind("<<ComboboxSelected>>", lambda e: None)
        # 默认隐藏

        self._status_label = ttk.Label(self._bottom_frame, text="🟢 就绪", foreground="green")
        self._status_label.pack(side=tk.RIGHT)

        self._mic_label = ttk.Label(self._bottom_frame, text="🎤 未检测", foreground="red")
        self._mic_label.pack(side=tk.RIGHT, padx=(0, 12))

        # ── 日志 ──
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding=2)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self._log_text = scrolledtext.ScrolledText(
            log_frame, height=8, state=tk.DISABLED,
            font=("Consolas", 9), wrap=tk.WORD,
            relief=tk.SUNKEN, borderwidth=1
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)

        # 日志 handler
        text_handler = TextHandler(self._log_text)
        logger.addHandler(text_handler)
        # 也输出到控制台
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(ch)

    # ── 引擎切换 ──
    def _switch_engine(self, name):
        """切换引擎（不中断当前播放）。"""
        old_engine = self.engine

        if name == "eSpeak":
            new_engine = None
            try:
                new_engine = EspeakEngine()
            except FileNotFoundError as e:
                logger.error(str(e))
                return
            self.engine = new_engine
            self._engine_label.config(text=" 当前: eSpeak")
            self._voice_frame.pack_forget()
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._update_speed_range(new_engine.get_speed_range())
            logger.info("切换到引擎: eSpeak")
        elif name == "SAPI5":
            if pythoncom is None:
                logger.error("pywin32 未安装。请执行: pip install pywin32")
                return
            try:
                new_engine = SystemTTSEngine()
            except RuntimeError as e:
                logger.error(str(e))
                return
            self.engine = new_engine
            self._engine_label.config(text=" 当前: SAPI5")
            self._voice_frame.config(text="系统语音选择")
            self._populate_voice_combo(new_engine)
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._update_speed_range(new_engine.get_speed_range())
            logger.info("切换到引擎: SAPI5")
        elif name == "Piper":
            try:
                new_engine = PiperEngine()
            except (FileNotFoundError, RuntimeError) as e:
                logger.error(str(e))
                return
            self.engine = new_engine
            self._engine_label.config(text=" 当前: Piper")
            self._voice_frame.config(text="Piper 模型选择")
            self._populate_voice_combo(new_engine)
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._update_speed_range(new_engine.get_speed_range())
            logger.info("切换到引擎: Piper")
        elif name == "Edge":
            if edge_tts is None:
                logger.error("edge-tts 未安装。请执行: pip install edge-tts")
                return
            try:
                new_engine = EdgeEngine()
            except RuntimeError as e:
                logger.error(str(e))
                return
            self.engine = new_engine
            self._engine_label.config(text=" 当前: Edge")
            self._voice_frame.config(text="Edge 语音选择")
            self._edge_locale_combo.pack(fill=tk.X, padx=2, pady=(4, 0),
                                         before=self._voice_combo)
            self._populate_edge_locales(new_engine)
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_var.set(0)
            self._pitch_label.config(text="0Hz")
            self._update_speed_range(new_engine.get_speed_range())
            logger.info("切换到引擎: Edge")
        elif name == "Aliyun":
            if dashscope is None:
                logger.error("dashscope 未安装。请执行: pip install dashscope")
                return
            try:
                new_engine = AliyunEngine()
            except RuntimeError as e:
                logger.error(str(e))
                return
            self.engine = new_engine
            self._engine_label.config(text=" 当前: Aliyun")
            self._voice_frame.config(text="Aliyun 语音选择")
            self._populate_voice_combo(new_engine)
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._speed_scale.config(state=tk.DISABLED)
            self._speed_label.config(text="N/A")
            logger.info("切换到引擎: Aliyun")
        else:
            logger.info(f"引擎 {name} 尚未实现（预留按钮）")
            return

        if isinstance(old_engine, SystemTTSEngine):
            old_engine.stop()

    def _on_pitch_change(self, val):
        val = float(val)
        self._pitch_label.config(text=f"{int(val):+d}Hz")
        if isinstance(self.engine, EdgeEngine):
            self.engine.set_pitch(int(val))

    def _populate_edge_locales(self, engine):
        """填充 Edge 语言区域下拉列表。"""
        locales = engine.get_locales()
        self._edge_locale_combo['values'] = locales
        if "zh-CN" in locales:
            self._edge_locale_combo.set("zh-CN")
        else:
            self._edge_locale_combo.current(0)
        self._on_edge_locale_select()

        if not engine.voices_ready:
            self.root.after(500, lambda: self._refresh_edge_voices(engine))

    def _refresh_edge_voices(self, engine):
        """后台获取到完整在线语音列表后刷新 UI。"""
        if not isinstance(self.engine, EdgeEngine) or self.engine is not engine:
            return
        if engine.voices_ready:
            old_locale = self._edge_locale_combo.get()
            old_voice = self._voice_var.get()
            locales = engine.get_locales()
            self._edge_locale_combo['values'] = locales
            if old_locale in locales:
                self._edge_locale_combo.set(old_locale)
                self._on_edge_locale_select()
                if old_voice in self._voice_combo['values']:
                    self._voice_var.set(old_voice)
                    voice_id = self._voice_id_map.get(old_voice)
                    if voice_id:
                        self.engine.set_voice(voice_id)
            else:
                if "zh-CN" in locales:
                    self._edge_locale_combo.set("zh-CN")
                else:
                    self._edge_locale_combo.current(0)
                self._on_edge_locale_select()
            logger.info("Edge 语音列表已刷新")
        else:
            self.root.after(500, lambda: self._refresh_edge_voices(engine))

    def _on_edge_locale_select(self, event=None):
        """Edge 语言区域变化时，更新语音下拉列表，优先匹配引擎当前音色。"""
        locale = self._edge_locale_combo.get()
        if not locale or not isinstance(self.engine, EdgeEngine):
            return
        voices = self.engine.get_voices_for_locale(locale)
        self._voice_combo['values'] = [name for _, name in voices]
        self._voice_id_map = {name: vid for vid, name in voices}

        target_id = self.engine._current_voice
        idx = 0
        for i, (vid, _) in enumerate(voices):
            if vid == target_id:
                idx = i
                break
        self._voice_combo.current(idx)
        self._voice_var.set(voices[idx][1])
        self.engine.set_voice(voices[idx][0])

    def _populate_voice_combo(self, engine):
        """填充语音下拉列表，优先匹配引擎当前音色。"""
        voices = engine.get_voices()
        self._voice_combo['values'] = [name for _, name in voices]
        self._voice_id_map = {name: vid for vid, name in voices}

        target_id = str(getattr(engine, '_voice',
                     getattr(engine, '_current_voice_index',
                     getattr(engine, '_current_model_name', ''))))
        idx = 0
        for i, (vid, _) in enumerate(voices):
            if str(vid) == target_id:
                idx = i
                break
        self._voice_combo.current(idx)
        self._voice_var.set(voices[idx][1])

    def _on_voice_select(self, event=None):
        """语音选择回调。"""
        selected_name = self._voice_var.get()
        voice_id = self._voice_id_map.get(selected_name)
        if voice_id and hasattr(self.engine, 'set_voice'):
            self.engine.set_voice(voice_id)
            logger.info(f"语音切换为: {selected_name}")

    def _update_speed_range(self, range_tuple):
        """根据引擎更新语速滑块范围。"""
        lo, hi = range_tuple
        self._speed_scale.config(from_=lo, to=hi, state=tk.NORMAL)
        mid = (lo + hi) // 2
        self._speed_var.set(mid)
        self._speed_label.config(text=str(mid))

    def _on_monitor_toggle(self):
        """监听勾选/取消时显示/隐藏设备下拉框。"""
        if self._monitor_enabled.get():
            self._populate_monitor_combo()
            self._monitor_combo.pack(side=tk.LEFT, padx=(4, 12))
        else:
            self._monitor_combo.pack_forget()

    def _populate_monitor_combo(self):
        """填充监听设备下拉列表。"""
        devices = AudioPlayer.list_output_devices()
        self._monitor_devices = {name: idx for idx, name in devices}
        self._monitor_combo['values'] = list(self._monitor_devices.keys())
        # 默认选第一个非 CABLE 的设备
        for name in self._monitor_devices:
            if "CABLE" not in name.upper():
                self._monitor_device_var.set(name)
                break
        else:
            if self._monitor_devices:
                self._monitor_device_var.set(list(self._monitor_devices.keys())[0])

    def _get_monitor_device_index(self) -> int:
        """返回当前选中的监听设备索引，未选中返回 None。"""
        if not self._monitor_enabled.get():
            return None
        name = self._monitor_device_var.get()
        return self._monitor_devices.get(name)

    # ── 语速/音量变化 ──
    def _on_speed_change(self, val):
        val = float(val)
        self._speed_label.config(text=f"{int(val)}")
        logger.debug(f"语速: {int(val)}")

    def _on_vol_change(self, val):
        val = float(val)
        self._vol_label.config(text=f"{int(val)}%")

    # ── 历史记录 ──
    def _add_history(self, text):
        """添加一条历史记录并滚动到底部。"""
        self._hist_listbox.insert(tk.END, text)
        self._hist_listbox.see(tk.END)

    def _on_history_click(self, event):
        """单击历史记录项，使用当前引擎播放。"""
        selection = self._hist_listbox.curselection()
        if selection:
            text = self._hist_listbox.get(selection[0])
            if text:
                self._speak(text)

    def _on_clear_history(self):
        """清空所有历史记录。"""
        self._hist_listbox.delete(0, tk.END)

    # ── Enter / 播放 / ESC ──
    def _on_enter(self, event):
        """按 Enter 发送文字到麦克风，加入历史并清空输入框。"""
        text = self._input_text.get("1.0", tk.END).strip()
        if not text:
            return "break"
        self._add_history(text)
        self._input_text.delete("1.0", tk.END)
        self._do_speak(text=text)
        return "break"

    def _on_ctrl_enter(self, event):
        """按 Ctrl+Enter 发送文字到麦克风，同时保存音频到文件。"""
        text = self._input_text.get("1.0", tk.END).strip()
        if not text:
            return "break"
        self._add_history(text)
        self._input_text.delete("1.0", tk.END)
        self._do_speak(text=text, save_to_disk=True)
        return "break"

    def _on_play(self):
        """播放按钮点击。"""
        text = self._input_text.get("1.0", tk.END).strip()
        if text:
            self._add_history(text)
            self._input_text.delete("1.0", tk.END)
        self._do_speak(text=text)

    def _on_esc(self, event=None):
        """ESC 停止播放。"""
        self._on_stop()

    def _do_speak(self, text=None, save_to_disk=False):
        """从输入框取文字并发送。若不传 text 则从输入框读取。"""
        if text is None:
            text = self._input_text.get("1.0", tk.END).strip()
        if text:
            save_path = None
            if save_to_disk:
                save_path = self._make_save_path(text)
            self._speak(text, save_path=save_path)

    @staticmethod
    def _make_save_path(text: str) -> str:
        """生成保存路径：时间戳_文字前10字.wav。"""
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '', text)
        safe = re.sub(r'\s+', ' ', safe).strip()
        safe = safe[:10] if safe else "audio"
        return os.path.join(os.getcwd(), f"{ts}_{safe}.wav")

    # ── 核心 TTS 调用 ──
    def _speak(self, text: str, save_path: str = None):
        """每个播放请求创建独立 AudioPlayer，彻底隔离。"""
        if not self.engine:
            logger.error("引擎未就绪，无法合成")
            return

        if self._is_playing:
            logger.info("正在播放中，先停止当前播放")
            self._stop_playback()

        self._stop_event.clear()
        self._is_playing = True
        self._playback_gen += 1
        gen = self._playback_gen
        self._status_label.config(text="🔊 合成中...", foreground="orange")

        monitor_idx = self._get_monitor_device_index()
        player = AudioPlayer(vb_device_index=self._vb_device_index)
        threading.Thread(target=self._speak_worker,
                         args=(text, player, monitor_idx, gen, save_path), daemon=True).start()

    def _speak_worker(self, text: str, player: AudioPlayer,
                      monitor_device_index: int = None, gen: int = 0,
                      save_path: str = None):
        """后台线程：合成 + 播放。每个 worker 持有独立 AudioPlayer。"""
        wav_path = None
        try:
            speed = self._speed_var.get()
            volume = self._vol_var.get() / 100.0
            if isinstance(self.engine, EdgeEngine):
                self.engine.set_pitch(self._pitch_var.get())

            logger.info(f"合成: 「{text[:50]}{'...' if len(text)>50 else ''}」")

            wav_path = self.engine.synthesize(text, speed, volume)

            if self._stop_event.is_set() or gen != self._playback_gen:
                return

            # 保存到磁盘（Shift+Enter）
            if save_path and os.path.exists(wav_path):
                try:
                    shutil.copy2(wav_path, save_path)
                    logger.info(f"已保存: {save_path}")
                except Exception as e:
                    logger.error(f"保存失败: {e}")

            logger.info("合成完成，播放中...")
            if gen == self._playback_gen:
                self.root.after(0, lambda: self._status_label.config(
                    text="🔊 播放中...", foreground="#2a7a2a"))

            self._current_wav = wav_path
            completed = player.play(wav_path, self._stop_event,
                                     monitor=self._monitor_enabled.get(),
                                     monitor_device_index=monitor_device_index,
                                     volume_getter=lambda: self._vol_var.get() / 100.0)

            if gen == self._playback_gen:
                if completed:
                    logger.info("播放完成")
                else:
                    logger.info("播放被中断")
                self.root.after(0, self._set_idle)

        except Exception as e:
            logger.error(f"错误: {e}")
            if gen == self._playback_gen:
                self.root.after(0, lambda: self._status_label.config(
                    text=f"❌ 错误: {str(e)[:50]}", foreground="red"))
                self.root.after(0, self._set_idle)
        finally:
            if gen == self._playback_gen:
                self._is_playing = False
            player.stop()  # 清理本 worker 独占的 AudioPlayer
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            self._current_wav = None

    def _set_idle(self):
        self._status_label.config(text="🟢 就绪", foreground="green")
        self._is_playing = False

    # ── 停止 ──
    def _on_stop(self):
        """停止当前播放。"""
        logger.info("用户请求停止")
        self._stop_playback()

    def _stop_playback(self):
        """仅设停止标志，让 player 自己在线程内清理。"""
        self._stop_event.set()
        self._is_playing = False
        self._status_label.config(text="🟢 就绪", foreground="green")

    # ── VB-Cable 检测 ──
    def _check_vb_cable(self):
        """检测 VB-Cable 是否存在。"""
        try:
            if pyaudio is None:
                self._mic_label.config(text="🎤 pyaudio 未安装", foreground="orange")
                return
            idx = AudioPlayer.find_vb_cable()
            self._vb_device_index = idx
            self._mic_label.config(text="🎤 CABLE Input ✅", foreground="green")
            logger.info("VB-Cable 检测通过")
        except RuntimeError as e:
            self._mic_label.config(text="🎤 未检测到", foreground="red")
            logger.error(str(e))

    # ── 窗口关闭 ──
    def _on_close(self):
        self._stop_playback()
        if isinstance(self.engine, SystemTTSEngine):
            self.engine.stop()
        self.root.destroy()

    # ── 启动 ──
    def run(self):
        self.root.mainloop()


# ── 入口 ──────────────────────────────────────────────
if __name__ == "__main__":
    # 检查 Python 版本
    if sys.version_info < (3, 8):
        print("需要 Python 3.8+")
        sys.exit(1)

    # 检查依赖
    if pyaudio is None:
        print("=" * 50)
        print("请安装依赖:")
        print("  pip install pyaudio")
        print("=" * 50)

    if pyttsx3 is None:
        print("提示: 安装 pyttsx3 可使用系统 TTS 引擎")
        print("  pip install pyttsx3")

    app = TTSMicInjectorApp()
    app.run()