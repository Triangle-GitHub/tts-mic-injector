# -*- coding: utf-8 -*-
"""
SettingsPanel — 右侧控制面板：引擎切换、音色/语速、音量、监听、
深色模式、日志、状态栏。完全使用 qfluentwidgets 组件，无 QGroupBox。
"""

import logging

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
)

from qfluentwidgets import (
    PushButton, Slider, ComboBox, SwitchButton,
    BodyLabel, SubtitleLabel,
    TextEdit, Theme, setTheme, isDarkTheme,
)

from config import (
    SPEED_DEFAULT, SPEED_MIN, SPEED_MAX,
    VOLUME_DEFAULT, PITCH_DEFAULT,
    SPEED_SCALE_LENGTH, VOLUME_SCALE_LENGTH,
    EDGE_PITCH_MIN, EDGE_PITCH_MAX,
    MONITOR_ENABLED_DEFAULT,
    PANEL_MIN_WIDTH, ENGINE_SPEED_RANGES, get_theme,
)
from engines.edge import EdgeEngine
from service.tts_service import TTSService
from app.log_bridge import LogBridge
from app.utils import cfg

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


class SettingsPanel(QWidget):
    """右侧设置面板。"""

    _sig_status = pyqtSignal(str, str)
    _sig_mic_ok = pyqtSignal()
    _sig_mic_err = pyqtSignal()

    def __init__(self, service: TTSService, parent=None):
        super().__init__(parent)
        self._service = service
        self._voice_id_map = {}
        self._monitor_devices = {}
        self._theme_callback = None

        self.setObjectName("SettingsPanel")
        self.setMinimumWidth(PANEL_MIN_WIDTH)

        self._sig_status.connect(self._update_status)
        self._sig_mic_ok.connect(self._on_mic_ok)
        self._sig_mic_err.connect(self._set_mic_error)

        self._inject_service_getters()
        self._build_ui()
        self._register_service_callbacks()
        self._init_engine()

        QTimer.singleShot(200, self._populate_monitor_devices)
        QTimer.singleShot(300, self._check_vb_cable)

    # ── 公开属性（MainWindow 读取当前值） ──

    @property
    def speed_value(self):
        return self._speed_slider.value() if self._speed_slider.isEnabled() else SPEED_DEFAULT

    @property
    def volume_value(self):
        return self._volume_slider.value()

    @property
    def pitch_value(self):
        return self._pitch_slider.value()

    # ── Service getter 注入 ──

    def _inject_service_getters(self):
        self._service.set_monitor_state_getter(lambda: self._monitor_switch.isChecked())
        self._service.set_monitor_device_getter(self._get_monitor_device_index)
        self._service.set_pitch_getter(lambda: self._pitch_slider.value())
        self._service.set_volume_getter(lambda: self._volume_slider.value() / 100.0)

    # ── Service 回调 ──

    def _register_service_callbacks(self):
        self._service.on("status", self._on_service_status)
        self._service.on("engine_ready", self._on_service_engine_ready)
        self._service.on("vb_cable_detected", self._on_vb_cable_detected)
        self._service.on("vb_cable_error", self._on_vb_cable_error)

    def _on_service_status(self, text, color):
        self._sig_status.emit(text, color)

    def _on_service_engine_ready(self, name):
        pass

    def _on_vb_cable_detected(self, idx):
        self._sig_mic_ok.emit()

    def _on_vb_cable_error(self, msg):
        self._sig_mic_err.emit()

    def _on_mic_ok(self):
        self._mic_label.setText("CABLE Input ✅")
        self._mic_label.setStyleSheet("color: green;")

    def _update_status(self, text: str, color: str):
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};")

    def _set_mic_error(self):
        if pyaudio is None:
            self._mic_label.setText("pyaudio 未安装")
            self._mic_label.setStyleSheet("color: orange;")
        else:
            self._mic_label.setText("未检测到")
            self._mic_label.setStyleSheet("color: red;")

    # ── 初始化引擎 ──

    def _init_engine(self):
        success = self._service.start_engine("SAPI5")
        if success:
            logger.info(f"引擎就绪: {self._service.engine.name}")
            self._highlight_engine_btn("SAPI5")
            self._show_engine_settings("SAPI5")
            self._populate_voice_combo()
        else:
            logger.error("SAPI5 初始化失败")

    # ── 构建 UI ──

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self._build_engine_section(layout)
        self._build_engine_settings(layout)
        layout.addWidget(self._sep())
        self._build_volume_section(layout)
        self._build_toggles(layout)
        layout.addWidget(self._sep())
        self._build_log_section(layout)
        self._build_status_bar(layout)

    # ── 引擎切换按钮 ──

    def _build_engine_section(self, parent):
        parent.addWidget(SubtitleLabel("TTS 引擎"))

        row = QHBoxLayout()
        row.setSpacing(4)
        self._engine_btns = {}

        for name in ["Aliyun", "Edge", "SAPI5", "eSpeak", "Piper"]:
            btn = PushButton(name)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda checked, n=name: self._on_engine_switch(n))
            row.addWidget(btn)
            self._engine_btns[name] = btn

        parent.addLayout(row)

    def _highlight_engine_btn(self, name: str):
        dark = isDarkTheme()
        t = get_theme(dark)

        selected = (
            f"PushButton {{"
            f"  background-color: {t['engine_btn_selected_bg']};"
            f"  color: {t['engine_btn_selected_fg']};"
            f"  border: 1px solid {t['engine_btn_border_selected']};"
            f"  border-radius: 6px;"
            f"  padding: 5px 12px;"
            f"}}"
        )
        normal = (
            f"PushButton {{"
            f"  background-color: {t['engine_btn_normal_bg']};"
            f"  color: {t['engine_btn_normal_fg']};"
            f"  border: 1px solid {t['engine_btn_border_normal']};"
            f"  border-radius: 6px;"
            f"  padding: 5px 12px;"
            f"}}"
        )
        for n, btn in self._engine_btns.items():
            btn.setStyleSheet(selected if n == name else normal)

    # ── 引擎特有设置区域 ──

    def _build_engine_settings(self, parent):
        self._engine_settings_widget = QWidget()
        es = QVBoxLayout(self._engine_settings_widget)
        es.setContentsMargins(0, 0, 0, 0)
        es.setSpacing(6)

        # -- 地区选择（仅 Edge） --
        self._edge_locale_row = QWidget()
        elr = QHBoxLayout(self._edge_locale_row)
        elr.setContentsMargins(0, 0, 0, 0)
        elr.addWidget(BodyLabel("地区:"))
        self._edge_locale_combo = ComboBox()
        self._edge_locale_combo.currentIndexChanged.connect(self._on_edge_locale_select)
        elr.addWidget(self._edge_locale_combo, stretch=1)
        self._edge_locale_row.hide()
        es.addWidget(self._edge_locale_row)

        # -- 音色选择 --
        self._voice_row = QWidget()
        vr = QHBoxLayout(self._voice_row)
        vr.setContentsMargins(0, 0, 0, 0)
        vr.addWidget(BodyLabel("音色:"))
        self._voice_combo = ComboBox()
        self._voice_combo.currentIndexChanged.connect(self._on_voice_select)
        vr.addWidget(self._voice_combo, stretch=1)
        self._voice_row.hide()
        es.addWidget(self._voice_row)

        # -- 语速 --
        self._speed_row = QWidget()
        sr = QHBoxLayout(self._speed_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(BodyLabel("语速:"))
        self._speed_slider = Slider(Qt.Horizontal)
        self._speed_slider.setRange(SPEED_MIN, SPEED_MAX)
        self._speed_slider.setValue(SPEED_DEFAULT)
        self._speed_slider.valueChanged.connect(self._on_speed_change)
        self._speed_slider.setMinimumWidth(SPEED_SCALE_LENGTH)
        sr.addWidget(self._speed_slider, stretch=1)
        self._speed_label = BodyLabel(str(SPEED_DEFAULT))
        self._speed_label.setMinimumWidth(36)
        sr.addWidget(self._speed_label)
        es.addWidget(self._speed_row)

        # -- 音调（仅 Edge） --
        self._pitch_row = QWidget()
        pr = QHBoxLayout(self._pitch_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.addWidget(BodyLabel("音调:"))
        self._pitch_slider = Slider(Qt.Horizontal)
        self._pitch_slider.setRange(EDGE_PITCH_MIN, EDGE_PITCH_MAX)
        self._pitch_slider.setValue(PITCH_DEFAULT)
        self._pitch_slider.valueChanged.connect(self._on_pitch_change)
        pr.addWidget(self._pitch_slider, stretch=1)
        self._pitch_label = BodyLabel(f"{PITCH_DEFAULT:+d}Hz")
        self._pitch_label.setMinimumWidth(42)
        pr.addWidget(self._pitch_label)
        self._pitch_row.hide()
        es.addWidget(self._pitch_row)

        parent.addWidget(self._engine_settings_widget)

    # ── 音量 ──

    def _build_volume_section(self, parent):
        parent.addWidget(BodyLabel("音量"))

        row = QHBoxLayout()
        row.setSpacing(6)
        self._volume_slider = Slider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(VOLUME_DEFAULT)
        self._volume_slider.setMinimumWidth(VOLUME_SCALE_LENGTH)
        self._volume_slider.valueChanged.connect(self._on_vol_change)
        row.addWidget(self._volume_slider, stretch=1)
        self._volume_label = BodyLabel(f"{VOLUME_DEFAULT}%")
        self._volume_label.setMinimumWidth(36)
        row.addWidget(self._volume_label)
        parent.addLayout(row)

    # ── 开关行 ──

    def _build_toggles(self, parent):
        # 监听
        monitor_row = QHBoxLayout()
        monitor_row.setSpacing(6)
        monitor_row.addWidget(BodyLabel("监听"))
        self._monitor_switch = SwitchButton()
        self._monitor_switch.setChecked(MONITOR_ENABLED_DEFAULT)
        self._monitor_switch.checkedChanged.connect(self._on_monitor_toggle)
        monitor_row.addWidget(self._monitor_switch)
        monitor_row.addStretch()
        self._monitor_combo = ComboBox()
        self._monitor_combo.setMinimumWidth(160)
        self._monitor_combo.hide()
        monitor_row.addWidget(self._monitor_combo)
        parent.addLayout(monitor_row)

        # 深色模式
        theme_row = QHBoxLayout()
        theme_row.setSpacing(6)
        theme_row.addWidget(BodyLabel("深色模式"))
        self._theme_switch = SwitchButton()
        self._theme_switch.setChecked(isDarkTheme())
        self._theme_switch.checkedChanged.connect(self._on_theme_toggle)
        theme_row.addWidget(self._theme_switch)
        theme_row.addStretch()
        parent.addLayout(theme_row)

        # 同时播放
        concurrent_row = QHBoxLayout()
        concurrent_row.setSpacing(6)
        concurrent_row.addWidget(BodyLabel("同时播放"))
        self._concurrent_switch = SwitchButton()
        self._concurrent_switch.checkedChanged.connect(self._on_concurrent_toggle)
        concurrent_row.addWidget(self._concurrent_switch)
        concurrent_row.addStretch()
        parent.addLayout(concurrent_row)

    # ── 日志 ──

    def _build_log_section(self, parent):
        parent.addWidget(SubtitleLabel("日志"))

        self._log_text = TextEdit()
        self._log_text.setReadOnly(True)
        parent.addWidget(self._log_text, stretch=1)

        self._log_bridge = LogBridge(self._log_text)
        logger.addHandler(self._log_bridge)

    # ── 状态栏 ──

    def _build_status_bar(self, parent):
        row = QHBoxLayout()
        row.setSpacing(8)

        self._status_label = BodyLabel("🟢 就绪")
        self._status_label.setStyleSheet("color: green;")
        row.addWidget(self._status_label)

        row.addStretch()

        self._mic_label = BodyLabel("🎤 未检测")
        self._mic_label.setStyleSheet("color: red;")
        row.addWidget(self._mic_label)

        parent.addLayout(row)

    @staticmethod
    def _sep():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet("QFrame { color: rgba(128,128,128,0.3); }")
        line.setFixedHeight(1)
        return line

    # ── 引擎切换 ──

    def _on_engine_switch(self, name: str):
        if name == "Aliyun":
            if dashscope is None:
                logger.error("dashscope 未安装。请执行: pip install dashscope")
                return
            if not self._service.switch_engine("Aliyun"):
                return
            self._highlight_engine_btn("Aliyun")
            self._show_engine_settings("Aliyun")
            self._populate_voice_combo()
            self._speed_slider.setEnabled(False)
            self._speed_label.setText("N/A")

        elif name == "Edge":
            if edge_tts is None:
                logger.error("edge-tts 未安装。请执行: pip install edge-tts")
                return
            if not self._service.switch_engine("Edge"):
                return
            self._highlight_engine_btn("Edge")
            self._show_engine_settings("Edge")
            self._populate_edge_locales()
            self._update_speed_range(ENGINE_SPEED_RANGES["Edge"])

        elif name == "SAPI5":
            if pythoncom is None:
                logger.error("pywin32 未安装。请执行: pip install pywin32")
                return
            if not self._service.switch_engine("SAPI5"):
                return
            self._highlight_engine_btn("SAPI5")
            self._show_engine_settings("SAPI5")
            self._populate_voice_combo()
            self._update_speed_range(ENGINE_SPEED_RANGES["SAPI5"])

        elif name == "eSpeak":
            if not self._service.switch_engine("eSpeak"):
                return
            self._highlight_engine_btn("eSpeak")
            self._show_engine_settings("eSpeak")
            self._update_speed_range(ENGINE_SPEED_RANGES["eSpeak"])

        elif name == "Piper":
            if not self._service.switch_engine("Piper"):
                return
            self._highlight_engine_btn("Piper")
            self._show_engine_settings("Piper")
            self._populate_voice_combo()
            self._update_speed_range(ENGINE_SPEED_RANGES["Piper"])

        else:
            logger.info(f"引擎 {name} 尚未实现")

    def _show_engine_settings(self, name: str):
        voice_engines = {"Aliyun", "SAPI5", "Piper", "Edge"}
        edge_only = {"Edge"}

        self._voice_row.setVisible(name in voice_engines)
        self._edge_locale_row.setVisible(name in edge_only)
        self._speed_row.setVisible(name != "Aliyun")
        self._pitch_row.setVisible(name in edge_only)

        if name != "Aliyun":
            self._speed_slider.setEnabled(True)

        if name in edge_only:
            self._pitch_slider.setValue(PITCH_DEFAULT)
            self._pitch_label.setText(f"{PITCH_DEFAULT:+d}Hz")

    # ── 语音选择 ──

    def _populate_voice_combo(self):
        engine = self._service.engine
        if not engine:
            return
        voices = engine.get_voices()
        if not voices:
            return

        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        voice_names = [name for _, name in voices]
        self._voice_combo.addItems(voice_names)
        self._voice_id_map = {name: vid for vid, name in voices}

        target_id = str(getattr(engine, '_voice',
                         getattr(engine, '_current_voice_index',
                         getattr(engine, '_current_model_name', ''))))
        idx = 0
        for i, (vid, _) in enumerate(voices):
            if str(vid) == target_id:
                idx = i
                break
        self._voice_combo.setCurrentIndex(idx)
        self._voice_combo.blockSignals(False)

    def _on_voice_select(self, index):
        if index < 0:
            return
        selected_name = self._voice_combo.itemText(index)
        voice_id = self._voice_id_map.get(selected_name)
        if voice_id and hasattr(self._service.engine, 'set_voice'):
            self._service.engine.set_voice(voice_id)
            logger.info(f"语音切换为: {selected_name}")

    # ── Edge 地区 ──

    def _populate_edge_locales(self):
        engine = self._service.engine
        if not isinstance(engine, EdgeEngine):
            return
        locales = engine.get_locales()
        self._edge_locale_combo.blockSignals(True)
        self._edge_locale_combo.clear()
        self._edge_locale_combo.addItems(locales)
        if "zh-CN" in locales:
            self._edge_locale_combo.setText("zh-CN")
        else:
            self._edge_locale_combo.setCurrentIndex(0)
        self._edge_locale_combo.blockSignals(False)
        self._on_edge_locale_select()

        if not engine.voices_ready:
            QTimer.singleShot(500, lambda: self._refresh_edge_voices(engine))

    def _refresh_edge_voices(self, engine):
        if not isinstance(self._service.engine, EdgeEngine) or self._service.engine is not engine:
            return
        if engine.voices_ready:
            old_locale = self._edge_locale_combo.text()
            locales = engine.get_locales()
            self._edge_locale_combo.blockSignals(True)
            self._edge_locale_combo.clear()
            self._edge_locale_combo.addItems(locales)
            if old_locale in locales:
                self._edge_locale_combo.setText(old_locale)
                self._edge_locale_combo.blockSignals(False)
                self._on_edge_locale_select()
            else:
                if "zh-CN" in locales:
                    self._edge_locale_combo.setText("zh-CN")
                else:
                    self._edge_locale_combo.setCurrentIndex(0)
                self._edge_locale_combo.blockSignals(False)
                self._on_edge_locale_select()
            logger.info("Edge 语音列表已刷新")
        else:
            QTimer.singleShot(500, lambda: self._refresh_edge_voices(engine))

    def _on_edge_locale_select(self, index=None):
        locale = self._edge_locale_combo.text()
        if not locale or not isinstance(self._service.engine, EdgeEngine):
            return
        engine = self._service.engine
        voices = engine.get_voices_for_locale(locale)
        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        voice_names = [name for _, name in voices]
        self._voice_combo.addItems(voice_names)
        self._voice_id_map = {name: vid for vid, name in voices}

        target_id = engine._current_voice
        idx = 0
        for i, (vid, _) in enumerate(voices):
            if vid == target_id:
                idx = i
                break
        self._voice_combo.setCurrentIndex(idx)
        self._voice_combo.blockSignals(False)

        if idx < len(voice_names):
            engine.set_voice(voices[idx][0])

    # ── 语速 / 音量 / 音调 ──

    def _update_speed_range(self, range_tuple):
        if range_tuple is None:
            return
        lo, hi = range_tuple
        self._speed_slider.setRange(lo, hi)
        self._speed_slider.setEnabled(True)
        mid = (lo + hi) // 2
        self._speed_slider.setValue(mid)
        self._speed_label.setText(str(mid))

    def _on_speed_change(self, val):
        self._speed_label.setText(str(int(val)))

    def _on_vol_change(self, val):
        self._volume_label.setText(f"{int(val)}%")

    def _on_pitch_change(self, val):
        self._pitch_label.setText(f"{int(val):+d}Hz")
        if isinstance(self._service.engine, EdgeEngine):
            self._service.engine.set_pitch(int(val))

    # ── 监听 / 主题 ──

    def _on_monitor_toggle(self, checked):
        if checked:
            self._populate_monitor_devices()
            self._monitor_combo.show()
        else:
            self._monitor_combo.hide()

    def _populate_monitor_devices(self):
        devices = self._service.list_monitor_devices()
        self._monitor_devices = {name: idx for idx, name in devices}
        self._monitor_combo.clear()
        self._monitor_combo.addItems(list(self._monitor_devices.keys()))
        for name in self._monitor_devices:
            if "CABLE" not in name.upper():
                self._monitor_combo.setText(name)
                break
        else:
            if self._monitor_devices:
                self._monitor_combo.setCurrentIndex(0)

    def _get_monitor_device_index(self):
        if not self._monitor_switch.isChecked():
            return None
        name = self._monitor_combo.text()
        return self._monitor_devices.get(name)

    def set_theme_change_callback(self, callback):
        self._theme_callback = callback

    def _on_theme_toggle(self, checked):
        dark = bool(checked)
        setTheme(Theme.DARK if dark else Theme.LIGHT)
        if hasattr(self, '_engine_btns') and self._service.engine_name:
            self._highlight_engine_btn(self._service.engine_name)
        if self._theme_callback:
            self._theme_callback(dark)

    def _on_concurrent_toggle(self, checked):
        self._service.concurrent_mode = checked

    @property
    def concurrent_mode(self):
        return self._concurrent_switch.isChecked()

    # ── VB-Cable ──

    def _check_vb_cable(self):
        self._service.detect_vb_cable()

    # ── 清理 ──

    def cleanup(self):
        if hasattr(self, '_log_bridge'):
            logger.removeHandler(self._log_bridge)
