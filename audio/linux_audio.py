# -*- coding: utf-8 -*-
"""
Linux 音频后端 —— 用 pacat 把 PCM 流喂给 PipeWire/Pulse 的虚拟麦克风。

Windows 侧走 audio/player.py（pyaudio + VB-Cable），本模块只在非 Windows 平台
由 audio/__init__.py 选中，对外暴露与 Windows 版**完全同形**的接口：

    AudioPlayer(device=AudioDevice(...))
    AudioPlayer.play(wav_path, stop_event, monitor=..., monitor_device=AudioDevice(...))
    AudioPlayer.stop()

    AudioPlayer.find_virtual_mic()      -> AudioDevice | None
    AudioPlayer.list_output_devices()   -> [AudioDevice, ...]

本模块**不保留任何全局设备表**：设备标识就是 sink 名本身（字符串），所以不存在
「枚举顺序变了、下标漂了」的问题。

虚拟麦克风由 platforms/linux/51-tts-mic.conf 常驻提供（pipewire drop-in）；
那份配置不在时，ensure_virtual_mic() 会退化用 pulse 模块现场造一对。

⚠️ 只用 pacat，不用 paplay：本机实测 paplay 对着 pipewire-pulse 播出来是静音的
   （同一个 WAV 文件，pacat peak 12000 / paplay peak 341），见 PITFALLS #105。
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import wave

from audio.device import AudioDevice
from audio.pcm import PULSE_FORMATS, downmix_mono, scale_volume
from config import VIRTUAL_MIC_SINK, VIRTUAL_MIC_SOURCE, VIRTUAL_MIC_DESC

logger = logging.getLogger("TTSMicInjector")

_ENV = dict(os.environ)
_ENV.setdefault("LC_ALL", "C")   # 中文 locale 下 pactl -f json 会往 stderr 吐非 ASCII 警告

_ENSURE_LOCK = threading.Lock()


# ── pactl 封装 ──

def has_tools() -> bool:
    """pacat / pactl 是否都在。"""
    return bool(shutil.which("pacat") and shutil.which("pactl"))


def _pactl(*args, timeout=6) -> str:
    """跑一条 pactl，返回 stdout 文本。失败抛 RuntimeError。"""
    try:
        p = subprocess.run(
            ["pactl", *args],
            capture_output=True, text=True, timeout=timeout, env=_ENV,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"调用 pactl 失败: {e}")
    if p.returncode != 0:
        raise RuntimeError(f"pactl {' '.join(args)} 失败: {p.stderr.strip()}")
    return p.stdout


def _pactl_json(*args) -> list:
    out = _pactl("-f", "json", *args).strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        logger.warning("pactl 返回的不是合法 JSON，按空列表处理")
        return []


def list_sinks() -> list:
    """[{index, name, description, state}, ...]（原始信息，调试用）"""
    return [
        {
            "index": s.get("index"),
            "name": s.get("name", ""),
            "description": s.get("description", "") or s.get("name", ""),
            "state": s.get("state", ""),
        }
        for s in _pactl_json("list", "sinks")
    ]


def list_sources() -> list:
    return [
        {
            "index": s.get("index"),
            "name": s.get("name", ""),
            "description": s.get("description", "") or s.get("name", ""),
        }
        for s in _pactl_json("list", "sources")
    ]


def default_sink() -> str:
    try:
        return _pactl("get-default-sink").strip()
    except RuntimeError:
        return ""


# ── 虚拟麦克风 ──

def virtual_mic_present() -> bool:
    """虚拟麦克风的 sink 和 source 是否都在。"""
    if VIRTUAL_MIC_SINK not in {s["name"] for s in list_sinks()}:
        return False
    return any(s["name"] == VIRTUAL_MIC_SOURCE for s in list_sources())


def _module_loaded(needle: str) -> bool:
    try:
        mods = _pactl("list", "short", "modules")
    except RuntimeError:
        return False
    return any(needle in line for line in mods.splitlines())


def ensure_virtual_mic() -> tuple:
    """确保虚拟麦克风存在，返回 (sink_name, source_name, created)。

    首选：platforms/linux/51-tts-mic.conf 装到 pipewire 的 drop-in 目录后常驻。
    退化：没有那份配置时，现场用 pulse 模块造一对（描述会被 modargs 截断成单 token）。
    """
    with _ENSURE_LOCK:
        if virtual_mic_present():
            return VIRTUAL_MIC_SINK, VIRTUAL_MIC_SOURCE, False

        if not has_tools():
            raise RuntimeError(
                "找不到 pactl / pacat，无法创建虚拟麦克风（请安装 pulseaudio-utils）")

        created = False
        if not _module_loaded(f"sink_name={VIRTUAL_MIC_SINK}"):
            logger.info(f"创建虚拟麦克风 sink: {VIRTUAL_MIC_SINK}")
            _pactl("load-module", "module-null-sink",
                   f"sink_name={VIRTUAL_MIC_SINK}", "rate=48000")
            created = True

        if not any(s["name"] == VIRTUAL_MIC_SOURCE for s in list_sources()):
            logger.info(f"创建虚拟麦克风 source: {VIRTUAL_MIC_SOURCE}")
            _pactl("load-module", "module-remap-source",
                   f"master={VIRTUAL_MIC_SINK}.monitor",
                   f"source_name={VIRTUAL_MIC_SOURCE}")
            created = True

        if not virtual_mic_present():
            raise RuntimeError(
                "虚拟麦克风创建失败。\n"
                f"请确认 PipeWire 正常运行，或手动检查 sink「{VIRTUAL_MIC_SINK}」/ "
                f"source「{VIRTUAL_MIC_SOURCE}」是否存在。"
            )
        return VIRTUAL_MIC_SINK, VIRTUAL_MIC_SOURCE, created


def virtual_mic_device() -> AudioDevice:
    """虚拟麦克风对应的输出设备（播放目标）。"""
    return AudioDevice(id=VIRTUAL_MIC_SINK, label=VIRTUAL_MIC_DESC, is_virtual=True)


# ── pacat 播放流 ──

class _PacatStream:
    """一个 pacat --playback 子进程的写端，行为上等价于 pyaudio 的 stream。"""

    def __init__(self, sink: str, sampwidth: int, rate: int, channels: int,
                 mono: bool = False):
        fmt = PULSE_FORMATS.get(sampwidth)
        if fmt is None:
            raise RuntimeError(f"不支持的采样宽度: {sampwidth} 字节")
        self._mono = mono
        self._sampwidth = sampwidth
        self._channels = channels
        self._sink = sink
        out_ch = 1 if mono else channels
        cmd = [
            "pacat", "--playback",
            f"--device={sink}",
            "--raw",
            f"--format={fmt}",
            f"--rate={rate}",
            f"--channels={out_ch}",
            "--latency-msec=100",
        ]
        logger.info(f"打开音频输出: {sink} ({fmt}, {rate}Hz, {out_ch}ch)")
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

    def write(self, data: bytes):
        if self._mono and self._channels > 1:
            data = downmix_mono(data, self._sampwidth, self._channels)
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            # pacat 已经退出（设备被拔 / pipewire 重启）——静默跳过，别打断播放循环
            pass

    def close(self, graceful: bool = True):
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        if graceful:
            try:
                self.proc.wait(timeout=3)
                return
            except Exception:
                pass
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass


# ── 对外接口（与 Windows 版同形） ──

class AudioPlayer:
    """Linux 播放器：注入虚拟麦克风 + 可选监听。"""

    def __init__(self, device: AudioDevice = None):
        self._device = device if device is not None else virtual_mic_device()
        self._streams = []
        self._playing = False

    # ── 设备发现（static，与 Windows 版同名） ──

    @staticmethod
    def find_virtual_mic() -> AudioDevice:
        """确保虚拟麦克风存在并返回它。失败抛 RuntimeError（带可读原因）。"""
        if not has_tools():
            raise RuntimeError("未找到 pactl/pacat（请安装 pulseaudio-utils）")

        sink_name, _source, created = ensure_virtual_mic()
        if created:
            logger.info("虚拟麦克风已就绪（本次新建）")

        for s in list_sinks():
            if s["name"] == sink_name:
                logger.info(f"找到虚拟麦克风: {s['description']}")
                return AudioDevice(id=s["name"], label=VIRTUAL_MIC_DESC,
                                   is_virtual=True)
        raise RuntimeError(f"虚拟麦克风 sink「{sink_name}」不在输出设备列表里")

    @staticmethod
    def list_output_devices() -> list:
        """所有输出设备 [AudioDevice, ...]，**排除**虚拟麦克风自身。"""
        if not has_tools():
            return []
        try:
            sinks = list_sinks()
        except RuntimeError as e:
            logger.warning(f"枚举输出设备失败: {e}")
            return []
        return [
            AudioDevice(id=s["name"], label=s["description"] or s["name"])
            for s in sinks
            if s["name"] != VIRTUAL_MIC_SINK
        ]

    @staticmethod
    def default_output_device() -> AudioDevice:
        """系统默认输出（监听默认目标）。"""
        name = default_sink()
        return AudioDevice(id=name, label=name or "默认输出")

    @staticmethod
    def virtual_source_name() -> str:
        return VIRTUAL_MIC_SOURCE

    @staticmethod
    def virtual_source_desc() -> str:
        return VIRTUAL_MIC_DESC

    # ── 播放 ──

    def play(self, wav_path: str, stop_event, monitor: bool = False,
             monitor_device: AudioDevice = None, volume_getter=None) -> bool:
        """播放 WAV 到虚拟麦克风，可选同时监听。返回 True 表示播完，False 被中断。"""
        inject_sink = self._device.id if self._device else ""
        monitor_sink = ""
        if monitor:
            monitor_device = monitor_device or self.default_output_device()
            monitor_sink = monitor_device.id if monitor_device else default_sink()

        wf = wave.open(wav_path, "rb")
        self._playing = True
        try:
            sampwidth = wf.getsampwidth()
            nchannels = wf.getnchannels()
            framerate = wf.getframerate()

            if not inject_sink and not monitor_sink:
                logger.warning("虚拟麦克风和监听都不可用，跳过播放")
                return True

            self._streams = []
            if inject_sink:
                try:
                    self._streams.append(
                        _PacatStream(inject_sink, sampwidth, framerate, nchannels))
                except Exception as e:
                    logger.info(f"虚拟麦克风不接受 {nchannels} 声道，改单声道: {e}")
                    self._streams.append(
                        _PacatStream(inject_sink, sampwidth, framerate, nchannels, mono=True))
            else:
                logger.info("虚拟麦克风不可用，跳过注入（仅监听）")

            if monitor_sink:
                try:
                    self._streams.append(
                        _PacatStream(monitor_sink, sampwidth, framerate, nchannels))
                    logger.info("监听已启用")
                except Exception as e:
                    logger.warning(f"无法打开监听设备: {e}")

            chunk = 2048
            while not stop_event.is_set():
                data = wf.readframes(chunk)
                if not data:
                    break
                if volume_getter:
                    data = scale_volume(data, sampwidth, volume_getter())
                for st in self._streams:
                    st.write(data)

            return not stop_event.is_set()
        finally:
            self._cleanup()
            wf.close()

    def stop(self):
        self._cleanup()

    def _cleanup(self):
        self._playing = False
        for st in self._streams:
            st.close(graceful=False)
        self._streams = []

    # ── 属性 ──

    @property
    def device(self) -> AudioDevice:
        return self._device

    @device.setter
    def device(self, value: AudioDevice):
        self._device = value

    @property
    def is_playing(self):
        return self._playing
