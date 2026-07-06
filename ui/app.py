# -*- coding: utf-8 -*-
"""
TTSMicInjectorApp — Tkinter GUI 层（纯 UI + 事件绑定）。
所有 TTS 业务逻辑委托给 TTSService。
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import logging
import threading
import re
import os
from datetime import datetime

from config import (
    SPEED_MIN, SPEED_MAX,
    VOLUME_DEFAULT,
    WINDOW_TITLE, WINDOW_GEOMETRY, WINDOW_MINSIZE,
    INPUT_FONT, INPUT_HEIGHT,
    LOG_FONT, LOG_HEIGHT,
    HISTORY_HEIGHT,
    SPEED_SCALE_LENGTH, VOLUME_SCALE_LENGTH, PITCH_SCALE_LENGTH,
    EDGE_PITCH_MIN, EDGE_PITCH_MAX,
    MONITOR_ENABLED_DEFAULT, get_engine_default,
)
from engines.edge import EdgeEngine
from engines.sapi5 import SystemTTSEngine
from service.tts_service import TTSService
from ui.log_handler import TextHandler

try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    import pythoncom
except ImportError:
    pythoncom = None

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import dashscope
except ImportError:
    dashscope = None

logger = logging.getLogger("TTSMicInjector")


class TTSMicInjectorApp:
    """TTS Mic Injector 主应用 UI。"""

    def __init__(self, service: TTSService):
        self._service = service

        # 注入 UI 状态获取器到 service（避免 service 直接依赖 tkinter）
        self._service.set_monitor_state_getter(lambda: self._monitor_enabled.get())
        self._service.set_monitor_device_getter(self._get_monitor_device_index)
        self._service.set_pitch_getter(lambda: self._pitch_var.get())
        self._service.set_volume_getter(lambda: self._vol_var.get() / 100.0)

        # 注册 service 回调
        self._service.on("status", self._on_service_status)
        self._service.on("engine_ready", self._on_service_engine_ready)
        self._service.on("vb_cable_detected", self._on_vb_cable_detected)
        self._service.on("vb_cable_error", self._on_vb_cable_error)

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(*WINDOW_MINSIZE)

        self._monitor_enabled = tk.BooleanVar(value=MONITOR_ENABLED_DEFAULT)
        self._monitor_device_var = tk.StringVar(value="")
        self._monitor_devices = {}
        self._voice_var = tk.StringVar(value="")
        self._voice_id_map = {}

        self._init_engine()

        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", self._on_esc)

        self.root.after(200, self._populate_monitor_combo)
        self.root.after(300, self._check_vb_cable)

        logger.info("应用已启动")

    # ── Service 回调（通过 root.after 调度到主线程） ──
    def _on_service_status(self, text, color):
        self.root.after(0, lambda: self._status_label.config(text=text, foreground=color))

    def _on_service_engine_ready(self, name):
        # UI 侧的引擎切换后处理由 _switch_engine 完成
        pass

    def _on_vb_cable_detected(self, idx):
        self.root.after(0, lambda: self._mic_label.config(text="🎤 CABLE Input ✅", foreground="green"))
        logger.info("VB-Cable 检测通过")

    def _on_vb_cable_error(self, msg):
        self.root.after(0, self._set_mic_error)

    def _set_mic_error(self):
        if pyaudio is None:
            self._mic_label.config(text="🎤 pyaudio 未安装", foreground="orange")
        else:
            self._mic_label.config(text="🎤 未检测到", foreground="red")

    # ── 初始化引擎 ──
    def _init_engine(self):
        success = self._service.start_engine("eSpeak")
        if success:
            logger.info(f"引擎就绪: {self._service.engine.name}")
        else:
            logger.error("eSpeak 初始化失败")

    # ── 构建 UI ──
    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=8)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── 历史记录区域 ──
        hist_frame = ttk.LabelFrame(main_frame, text="历史记录", padding=4)
        hist_frame.pack(fill=tk.X, pady=(0, 6))
        hist_container = ttk.Frame(hist_frame)
        hist_container.pack(fill=tk.X)
        hist_scrollbar = ttk.Scrollbar(hist_container, orient=tk.VERTICAL)
        self._hist_listbox = tk.Listbox(hist_container, height=HISTORY_HEIGHT,
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
        self._input_text = tk.Text(input_frame, height=INPUT_HEIGHT, font=INPUT_FONT,
                                   wrap=tk.WORD, relief=tk.SUNKEN, borderwidth=1)
        self._input_text.pack(fill=tk.X)
        self._input_text.bind("<Return>", self._on_enter)
        self._input_text.bind("<Control-Return>", self._on_ctrl_enter)
        self._input_text.focus_set()

        # ── 控制栏 ──
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.pack(fill=tk.X, pady=(0, 6))

        self._play_btn = ttk.Button(ctrl_frame, text="▶  播放", command=self._on_play)
        self._play_btn.pack(side=tk.LEFT, padx=(0, 12))

        self._stop_btn = ttk.Button(ctrl_frame, text="■  停止", command=self._on_stop)
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl_frame, text="语速:").pack(side=tk.LEFT)
        self._speed_var = tk.DoubleVar(value=get_engine_default("eSpeak").get("speed", 175))
        self._speed_scale = ttk.Scale(
            ctrl_frame, from_=SPEED_MIN, to=SPEED_MAX, variable=self._speed_var,
            orient=tk.HORIZONTAL, length=SPEED_SCALE_LENGTH, command=self._on_speed_change
        )
        self._speed_scale.pack(side=tk.LEFT, padx=4)
        self._speed_label = ttk.Label(ctrl_frame, text=f"{get_engine_default('eSpeak').get('speed', 175)}")
        self._speed_label.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl_frame, text="音量:").pack(side=tk.LEFT)
        self._vol_var = tk.DoubleVar(value=VOLUME_DEFAULT)
        self._vol_scale = ttk.Scale(
            ctrl_frame, from_=0, to=100, variable=self._vol_var,
            orient=tk.HORIZONTAL, length=VOLUME_SCALE_LENGTH, command=self._on_vol_change
        )
        self._vol_scale.pack(side=tk.LEFT, padx=4)
        self._vol_label = ttk.Label(ctrl_frame, text=f"{VOLUME_DEFAULT}%")
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

        self._engine_label = ttk.Label(engine_frame, text=" 当前: eSpeak", foreground="#2a7a2a")
        self._engine_label.pack(side=tk.RIGHT, padx=6)

        # ── 语音选择（仅 SAPI5 / Piper / Edge / Aliyun 可见） ──
        self._voice_frame = ttk.LabelFrame(main_frame, text="系统语音选择", padding=4)
        self._edge_locale_combo = ttk.Combobox(self._voice_frame, state="readonly", width=40)
        self._edge_locale_combo.bind("<<ComboboxSelected>>", self._on_edge_locale_select)
        self._voice_combo = ttk.Combobox(self._voice_frame, state="readonly",
                                          textvariable=self._voice_var, width=40)
        self._voice_combo.pack(fill=tk.X, padx=2, pady=2)
        self._voice_combo.bind("<<ComboboxSelected>>", self._on_voice_select)

        # ── Edge 音调（仅 Edge 可见） ──
        self._pitch_frame = ttk.LabelFrame(main_frame, text="Edge 音调", padding=4)
        ttk.Label(self._pitch_frame, text="音调:").pack(side=tk.LEFT)
        edge_pitch = get_engine_default("Edge").get("pitch", 0)
        self._pitch_var = tk.DoubleVar(value=edge_pitch)
        self._pitch_scale = ttk.Scale(
            self._pitch_frame, from_=EDGE_PITCH_MIN, to=EDGE_PITCH_MAX, variable=self._pitch_var,
            orient=tk.HORIZONTAL, length=PITCH_SCALE_LENGTH, command=self._on_pitch_change
        )
        self._pitch_scale.pack(side=tk.LEFT, padx=4)
        self._pitch_label = ttk.Label(self._pitch_frame, text=f"{edge_pitch}Hz")
        self._pitch_label.pack(side=tk.LEFT)

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

        self._status_label = ttk.Label(self._bottom_frame, text="🟢 就绪", foreground="green")
        self._status_label.pack(side=tk.RIGHT)

        self._mic_label = ttk.Label(self._bottom_frame, text="🎤 未检测", foreground="red")
        self._mic_label.pack(side=tk.RIGHT, padx=(0, 12))

        # ── 日志 ──
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding=2)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self._log_text = scrolledtext.ScrolledText(
            log_frame, height=LOG_HEIGHT, state=tk.DISABLED,
            font=LOG_FONT, wrap=tk.WORD,
            relief=tk.SUNKEN, borderwidth=1
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)

        text_handler = TextHandler(self._log_text)
        logger.addHandler(text_handler)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(ch)

    # ── 引擎切换 ──
    def _switch_engine(self, name):
        """切换引擎（不中断当前播放）。"""

        if name == "eSpeak":
            success = self._service.switch_engine("eSpeak")
            if not success:
                return
            self._engine_label.config(text=" 当前: eSpeak")
            self._voice_frame.pack_forget()
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._update_speed_range("eSpeak", self._service.get_speed_range())
            logger.info("切换到引擎: eSpeak")

        elif name == "SAPI5":
            if pythoncom is None:
                logger.error("pywin32 未安装。请执行: pip install pywin32")
                return
            success = self._service.switch_engine("SAPI5")
            if not success:
                return
            self._engine_label.config(text=" 当前: SAPI5")
            self._voice_frame.config(text="系统语音选择")
            self._populate_voice_combo()
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._update_speed_range("SAPI5", self._service.get_speed_range())
            logger.info("切换到引擎: SAPI5")

        elif name == "Piper":
            success = self._service.switch_engine("Piper")
            if not success:
                return
            self._engine_label.config(text=" 当前: Piper")
            self._voice_frame.config(text="Piper 模型选择")
            self._populate_voice_combo()
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._update_speed_range("Piper", self._service.get_speed_range())
            logger.info("切换到引擎: Piper")

        elif name == "Edge":
            if edge_tts is None:
                logger.error("edge-tts 未安装。请执行: pip install edge-tts")
                return
            success = self._service.switch_engine("Edge")
            if not success:
                return
            self._engine_label.config(text=" 当前: Edge")
            self._voice_frame.config(text="Edge 语音选择")
            self._edge_locale_combo.pack(fill=tk.X, padx=2, pady=(4, 0),
                                         before=self._voice_combo)
            self._populate_edge_locales()
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            edge_pitch = get_engine_default("Edge").get("pitch", 0)
            self._pitch_var.set(edge_pitch)
            self._pitch_label.config(text=f"{int(edge_pitch)}Hz")
            self._update_speed_range("Edge", self._service.get_speed_range())
            logger.info("切换到引擎: Edge")

        elif name == "Aliyun":
            if dashscope is None:
                logger.error("dashscope 未安装。请执行: pip install dashscope")
                return
            success = self._service.switch_engine("Aliyun")
            if not success:
                return
            self._engine_label.config(text=" 当前: Aliyun")
            self._voice_frame.config(text="Aliyun 语音选择")
            self._populate_voice_combo()
            self._voice_frame.pack(fill=tk.X, pady=(0, 6), before=self._bottom_frame)
            self._pitch_frame.pack_forget()
            self._edge_locale_combo.pack_forget()
            self._speed_scale.config(state=tk.DISABLED)
            self._speed_label.config(text="N/A")
            logger.info("切换到引擎: Aliyun")

        else:
            logger.info(f"引擎 {name} 尚未实现（预留按钮）")

    def _on_pitch_change(self, val):
        val = float(val)
        self._pitch_label.config(text=f"{int(val):+d}Hz")
        if isinstance(self._service.engine, EdgeEngine):
            self._service.engine.set_pitch(int(val))

    def _populate_edge_locales(self):
        engine = self._service.engine
        if not isinstance(engine, EdgeEngine):
            return
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
        if not isinstance(self._service.engine, EdgeEngine) or self._service.engine is not engine:
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
                        self._service.engine.set_voice(voice_id)
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
        locale = self._edge_locale_combo.get()
        if not locale or not isinstance(self._service.engine, EdgeEngine):
            return
        engine = self._service.engine
        voices = engine.get_voices_for_locale(locale)
        self._voice_combo['values'] = [name for _, name in voices]
        self._voice_id_map = {name: vid for vid, name in voices}

        target_id = engine._current_voice
        idx = 0
        for i, (vid, _) in enumerate(voices):
            if vid == target_id:
                idx = i
                break
        self._voice_combo.current(idx)
        self._voice_var.set(voices[idx][1])
        engine.set_voice(voices[idx][0])

    def _populate_voice_combo(self):
        engine = self._service.engine
        if not engine:
            return
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
        selected_name = self._voice_var.get()
        voice_id = self._voice_id_map.get(selected_name)
        if voice_id and hasattr(self._service.engine, 'set_voice'):
            self._service.engine.set_voice(voice_id)
            logger.info(f"语音切换为: {selected_name}")

    def _update_speed_range(self, engine_name, range_tuple):
        if range_tuple is None:
            return
        lo, hi = range_tuple
        self._speed_scale.config(from_=lo, to=hi, state=tk.NORMAL)
        default = get_engine_default(engine_name).get("speed", (lo + hi) // 2)
        self._speed_var.set(default)
        self._speed_label.config(text=str(default))

    def _on_monitor_toggle(self):
        if self._monitor_enabled.get():
            self._populate_monitor_combo()
            self._monitor_combo.pack(side=tk.LEFT, padx=(4, 12))
        else:
            self._monitor_combo.pack_forget()

    def _populate_monitor_combo(self):
        devices = self._service.list_monitor_devices()
        self._monitor_devices = {name: idx for idx, name in devices}
        self._monitor_combo['values'] = list(self._monitor_devices.keys())
        for name in self._monitor_devices:
            if "CABLE" not in name.upper():
                self._monitor_device_var.set(name)
                break
        else:
            if self._monitor_devices:
                self._monitor_device_var.set(list(self._monitor_devices.keys())[0])

    def _get_monitor_device_index(self) -> int:
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
        self._hist_listbox.insert(tk.END, text)
        self._hist_listbox.see(tk.END)

    def _on_history_click(self, event):
        selection = self._hist_listbox.curselection()
        if selection:
            text = self._hist_listbox.get(selection[0])
            if text:
                speed = self._speed_var.get()
                volume = self._vol_var.get() / 100.0
                self._service.speak(text, speed, volume)

    def _on_clear_history(self):
        self._hist_listbox.delete(0, tk.END)

    # ── Enter / 播放 / ESC ──
    def _on_enter(self, event):
        text = self._input_text.get("1.0", tk.END).strip()
        if not text:
            return "break"
        self._add_history(text)
        self._input_text.delete("1.0", tk.END)
        self._do_speak(text=text)
        return "break"

    def _on_ctrl_enter(self, event):
        text = self._input_text.get("1.0", tk.END).strip()
        if not text:
            return "break"
        self._add_history(text)
        self._input_text.delete("1.0", tk.END)
        self._do_speak(text=text, save_to_disk=True)
        return "break"

    def _on_play(self):
        text = self._input_text.get("1.0", tk.END).strip()
        if text:
            self._add_history(text)
            self._input_text.delete("1.0", tk.END)
        self._do_speak(text=text)

    def _on_esc(self, event=None):
        self._on_stop()

    def _do_speak(self, text=None, save_to_disk=False):
        if text is None:
            text = self._input_text.get("1.0", tk.END).strip()
        if text:
            save_path = None
            if save_to_disk:
                save_path = self._make_save_path(text)
            speed = self._speed_var.get()
            volume = self._vol_var.get() / 100.0
            if isinstance(self._service.engine, EdgeEngine):
                self._service.engine.set_pitch(self._pitch_var.get())
            self._service.speak(text, speed, volume, save_path=save_path)

    @staticmethod
    def _make_save_path(text: str) -> str:
        from datetime import datetime
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '', text)
        safe = re.sub(r'\s+', ' ', safe).strip()
        safe = safe[:10] if safe else "audio"
        return os.path.join(os.getcwd(), f"{ts}_{safe}.wav")

    # ── 停止 ──
    def _on_stop(self):
        logger.info("用户请求停止")
        self._service.stop()

    # ── VB-Cable 检测 ──
    def _check_vb_cable(self):
        if not self._service.detect_vb_cable():
            self.root.after(0, self._set_mic_error)

    # ── 窗口关闭 ──
    def _on_close(self):
        self._service.stop()
        if isinstance(self._service.engine, SystemTTSEngine):
            self._service.engine.stop()
        self.root.destroy()

    # ── 启动 ──
    def run(self):
        self.root.mainloop()
