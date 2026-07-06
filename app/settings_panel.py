# -*- coding: utf-8 -*-
"""
SettingsPanel — 右侧控制面板：引擎切换、音色/语速、音量、监听、
深色模式、日志、状态栏。完全使用 qfluentwidgets 组件，无 QGroupBox。
"""

import logging

import subprocess

from PyQt5.QtCore import Qt, QTimer, QEvent, pyqtSignal, QPointF, QPoint
from PyQt5.QtGui import QPen, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QMessageBox, QApplication, QSizePolicy,
)
import sys
from pathlib import Path

from qfluentwidgets import (
    PushButton, PrimaryPushButton, Slider, ComboBox,
    SwitchButton, BodyLabel, SubtitleLabel, LineEdit,
    TextEdit, Theme, setTheme, isDarkTheme,
)

from config import (
    SPEED_MIN, SPEED_MAX,
    VOLUME_DEFAULT,
    SPEED_SCALE_LENGTH, VOLUME_SCALE_LENGTH,
    EDGE_PITCH_MIN, EDGE_PITCH_MAX,
    MONITOR_ENABLED_DEFAULT,
    PANEL_MIN_WIDTH, ENGINE_SPEED_RANGES, get_theme,
    REMOTE_ENABLED, REMOTE_SERVER_URL, REMOTE_TOKEN,
    save_remote_config, get_engine_default,
)
from engines.edge import EdgeEngine
from service.tts_service import TTSService
from app.log_bridge import LogBridge
from app.utils import cfg
from installer import VBCableInstaller

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


class _VolumeSlider(Slider):
    def _drawHorizonTick(self, painter):
        r = self.handle.width() / 2
        mid = r + 0.5 * (self.width() - r * 2)
        c = QColor(255, 255, 255, 60) if isDarkTheme() else QColor(0, 0, 0, 60)
        painter.setPen(QPen(c, 1))
        painter.drawLine(QPointF(mid, r - 6), QPointF(mid, r + 6))


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
        self._vbcable_prompted = False
        self._installer = None
        self._remote_receiver = None
        self._remote_control_callback = None

        _url = REMOTE_SERVER_URL.replace("ws://", "").replace("/ws", "")
        if ":" in _url:
            _host, _port = _url.rsplit(":", 1)
        else:
            _host, _port = _url, "8765"
        self._remote_host = _host
        self._remote_port = _port
        self._remote_token = REMOTE_TOKEN

        self._status_color = "green"
        self._remote_status_color = ""
        self._mic_color = "red"

        self.setObjectName("SettingsPanel")
        self.setMinimumWidth(0)
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Ignored)
        self.setSizePolicy(sp)

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
        return self._speed_slider.value() if self._speed_slider.isEnabled() else get_engine_default(self._service.engine_name).get("speed", 175)

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

    def set_remote_receiver(self, receiver):
        self._remote_receiver = receiver
        if receiver:
            receiver.connection_changed.connect(self._on_remote_connection_changed)

    def set_remote_control_callback(self, callback):
        self._remote_control_callback = callback
        if callback and self._remote_switch.isChecked():
            callback(True)

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
        self._mic_color = "green"
        self._mic_label.setStyleSheet("color: green;")
        self._vbcable_install_btn.hide()

    def _update_status(self, text: str, color: str):
        self._status_label.setText(text)
        self._status_color = color
        self._status_label.setStyleSheet(f"color: {color};")

    def _set_mic_error(self):
        if pyaudio is None:
            self._mic_label.setText("pyaudio 未安装")
            self._mic_color = "orange"
            self._mic_label.setStyleSheet("color: orange;")
        else:
            self._mic_label.setText("未检测到")
            self._mic_color = "red"
            self._mic_label.setStyleSheet("color: red;")
        self._vbcable_install_btn.show()

        # 首次检测失败时弹出安装对话框
        if not self._vbcable_prompted and pyaudio is not None:
            self._vbcable_prompted = True
            QTimer.singleShot(500, self._show_vbcable_dialog)

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
            btn.setCursor(Qt.PointingHandCursor)
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
        self._speed_slider.setValue(get_engine_default("SAPI5").get("speed", 225))
        self._speed_slider.valueChanged.connect(self._on_speed_change)
        self._speed_slider.setMinimumWidth(SPEED_SCALE_LENGTH)
        sr.addWidget(self._speed_slider, stretch=1)
        self._speed_label = BodyLabel(str(get_engine_default("SAPI5").get("speed", 225)))
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
        self._pitch_slider.setValue(get_engine_default("Edge").get("pitch", 0))
        self._pitch_slider.valueChanged.connect(self._on_pitch_change)
        pr.addWidget(self._pitch_slider, stretch=1)
        self._pitch_label = BodyLabel(f"{get_engine_default('Edge').get('pitch', 0):+d}Hz")
        self._pitch_label.setMinimumWidth(42)
        pr.addWidget(self._pitch_label)
        self._pitch_row.hide()
        es.addWidget(self._pitch_row)

        parent.addWidget(self._engine_settings_widget)

    # ── 音量 ──

    def _build_volume_section(self, parent):
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(BodyLabel("音量"))
        self._volume_slider = _VolumeSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 200)
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
        self._monitor_combo.installEventFilter(self)
        monitor_row.addWidget(self._monitor_combo)
        parent.addLayout(monitor_row)

        if self._monitor_switch.isChecked():
            self._on_monitor_toggle(True)

        # 并行播放
        concurrent_row = QHBoxLayout()
        concurrent_row.setSpacing(6)
        concurrent_row.addWidget(BodyLabel("并行播放"))
        self._concurrent_switch = SwitchButton()
        self._concurrent_switch.checkedChanged.connect(self._on_concurrent_toggle)
        concurrent_row.addWidget(self._concurrent_switch)
        concurrent_row.addStretch()
        parent.addLayout(concurrent_row)

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

        # 远程输入
        remote_row = QHBoxLayout()
        remote_row.setSpacing(6)
        self._remote_switch = SwitchButton()
        self._remote_switch.setChecked(REMOTE_ENABLED)
        self._remote_switch.checkedChanged.connect(self._on_remote_toggle)
        remote_row.addWidget(BodyLabel("远程输入"))
        remote_row.addWidget(self._remote_switch)
        self._remote_config_btn = PushButton("配置")
        self._remote_config_btn.setFixedHeight(28)
        self._remote_config_btn.clicked.connect(self._show_remote_config_dialog)
        self._remote_config_btn.hide()
        remote_row.addWidget(self._remote_config_btn)
        remote_row.addStretch()
        parent.addLayout(remote_row)

        self._remote_status_label = BodyLabel("")

        if REMOTE_ENABLED:
            self._remote_status_label.setText("未连接")
            self._remote_status_label.setStyleSheet("color: gray;")
            self._remote_config_btn.show()

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
        row.setSpacing(6)

        self._status_label = BodyLabel("🟢 就绪")
        self._status_label.setStyleSheet("color: green;")
        row.addWidget(self._status_label)

        self._sep1 = self._make_vsep()
        row.addWidget(self._sep1)

        row.addWidget(self._remote_status_label)

        self._sep2 = self._make_vsep()
        row.addWidget(self._sep2)

        row.addStretch()

        self._mic_label = BodyLabel("🎤 未检测")
        self._mic_label.setStyleSheet("color: red;")
        row.addWidget(self._mic_label)

        self._vbcable_install_btn = PushButton("安装")
        self._vbcable_install_btn.setFixedHeight(24)
        self._vbcable_install_btn.clicked.connect(self._show_vbcable_dialog)
        self._vbcable_install_btn.hide()
        row.addWidget(self._vbcable_install_btn)

        parent.addLayout(row)

        c = "rgba(255,255,255,0.15)" if isDarkTheme() else "rgba(0,0,0,0.12)"
        for sep in (self._sep1, self._sep2):
            sep.setStyleSheet(f"QFrame {{ color: {c}; }}")

    @staticmethod
    def _make_vsep():
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Plain)
        line.setFixedWidth(1)
        return line

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
            self._update_speed_range("Edge", ENGINE_SPEED_RANGES["Edge"])

        elif name == "SAPI5":
            if pythoncom is None:
                logger.error("pywin32 未安装。请执行: pip install pywin32")
                return
            if not self._service.switch_engine("SAPI5"):
                return
            self._highlight_engine_btn("SAPI5")
            self._show_engine_settings("SAPI5")
            self._populate_voice_combo()
            self._update_speed_range("SAPI5", ENGINE_SPEED_RANGES["SAPI5"])

        elif name == "eSpeak":
            if not self._service.switch_engine("eSpeak"):
                return
            self._highlight_engine_btn("eSpeak")
            self._show_engine_settings("eSpeak")
            self._update_speed_range("eSpeak", ENGINE_SPEED_RANGES["eSpeak"])

        elif name == "Piper":
            if not self._service.switch_engine("Piper"):
                return
            self._highlight_engine_btn("Piper")
            self._show_engine_settings("Piper")
            self._populate_voice_combo()
            self._update_speed_range("Piper", ENGINE_SPEED_RANGES["Piper"])

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
            edge_pitch = get_engine_default("Edge").get("pitch", 0)
            self._pitch_slider.setValue(edge_pitch)
            self._pitch_label.setText(f"{edge_pitch:+d}Hz")

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

    def _update_speed_range(self, engine_name, range_tuple):
        if range_tuple is None:
            return
        lo, hi = range_tuple
        self._speed_slider.setRange(lo, hi)
        self._speed_slider.setEnabled(True)
        default = get_engine_default(engine_name).get("speed", (lo + hi) // 2)
        self._speed_slider.setValue(default)
        self._speed_label.setText(str(default))

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
        old_name = self._monitor_combo.text()
        devices = self._service.list_monitor_devices()
        self._monitor_devices = {name: idx for idx, name in devices}
        self._monitor_combo.clear()
        self._monitor_combo.addItems(list(self._monitor_devices.keys()))

        # 保留旧选择
        if old_name and old_name in self._monitor_devices:
            self._monitor_combo.setText(old_name)
            return

        # 旧选择已消失，优先选非 CABLE 设备
        for name in self._monitor_devices:
            if "CABLE" not in name.upper():
                self._monitor_combo.setText(name)
                return
        if self._monitor_devices:
            self._monitor_combo.setCurrentIndex(0)

    def _get_monitor_device_index(self):
        if not self._monitor_switch.isChecked():
            return None
        name = self._monitor_combo.text()
        return self._monitor_devices.get(name)

    def set_theme_change_callback(self, callback):
        self._theme_callback = callback

    def _refresh_status_colors(self):
        self._status_label.setStyleSheet(f"color: {self._status_color};")
        if self._mic_color:
            self._mic_label.setStyleSheet(f"color: {self._mic_color};")
        if self._remote_status_color:
            self._remote_status_label.setStyleSheet(f"color: {self._remote_status_color};")
        dark = isDarkTheme()
        c = "rgba(255,255,255,0.15)" if dark else "rgba(0,0,0,0.12)"
        for sep in (self._sep1, self._sep2):
            sep.setStyleSheet(f"QFrame {{ color: {c}; }}")

    def _on_theme_toggle(self, checked):
        dark = bool(checked)
        setTheme(Theme.DARK if dark else Theme.LIGHT)
        if hasattr(self, '_engine_btns') and self._service.engine_name:
            self._highlight_engine_btn(self._service.engine_name)
        if self._theme_callback:
            self._theme_callback(dark)
        self._refresh_status_colors()

    def _on_concurrent_toggle(self, checked):
        self._service.concurrent_mode = checked

    @property
    def concurrent_mode(self):
        return self._concurrent_switch.isChecked()

    @property
    def remote_server_url(self):
        return f"ws://{self._remote_host}:{self._remote_port}/ws"

    @property
    def remote_token(self):
        return self._remote_token

    def _on_remote_toggle(self, checked):
        if checked:
            self._remote_config_btn.show()
            if self._remote_control_callback:
                self._remote_control_callback(True)
        else:
            if self._remote_receiver:
                self._remote_receiver.stop()
            self._remote_config_btn.hide()
            self._remote_status_label.setText("")
            if self._remote_control_callback:
                self._remote_control_callback(False)

    def _on_remote_connection_changed(self, connected):
        if connected:
            self._remote_status_label.setText("已连接")
            self._remote_status_color = "green"
            self._remote_status_label.setStyleSheet("color: green;")
        else:
            if self._remote_switch.isChecked():
                self._remote_status_label.setText("重连中...")
                self._remote_status_color = "orange"
                self._remote_status_label.setStyleSheet("color: orange;")
            else:
                self._remote_status_label.setText("")
                self._remote_status_color = ""

    def _show_remote_config_dialog(self):
        popup = QFrame(self, Qt.Popup)
        popup.setObjectName("remoteConfigPopup")
        popup.setFrameShape(QFrame.StyledPanel)
        dark = isDarkTheme()
        bg = "#2d2d2d" if dark else "#ffffff"
        border = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.12)"
        popup.setStyleSheet(
            f"QFrame#remoteConfigPopup {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
        )

        layout = QVBoxLayout(popup)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(BodyLabel("地址:端口"))
        addr_edit = LineEdit()
        addr_edit.setText(f"{self._remote_host}:{self._remote_port}")
        addr_edit.setFixedWidth(240)
        layout.addWidget(addr_edit)

        layout.addWidget(BodyLabel("Token"))
        token_edit = LineEdit()
        token_edit.setText(self._remote_token)
        token_edit.setFixedWidth(240)
        layout.addWidget(token_edit)

        btn_row = QHBoxLayout()
        cancel_btn = PushButton("取消")
        save_btn = PushButton("保存")
        save_cfg_btn = PrimaryPushButton("保存到配置")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(save_cfg_btn)
        layout.addLayout(btn_row)

        pos = self._remote_config_btn.mapToGlobal(QPoint(0, self._remote_config_btn.height() + 4))
        popup.move(pos)
        popup.show()

        def _parse_addr():
            addr = addr_edit.text().strip()
            if ":" in addr:
                h, p = addr.rsplit(":", 1)
                self._remote_host = h or "127.0.0.1"
                self._remote_port = p or "8765"
            else:
                self._remote_host = addr or "127.0.0.1"
                self._remote_port = "8765"
            self._remote_token = token_edit.text().strip()

        def _do_save():
            _parse_addr()
            popup.close()
            if self._remote_switch.isChecked() and self._remote_control_callback:
                self._remote_control_callback(False)
                self._remote_control_callback(True)

        def _do_save_config():
            _parse_addr()
            popup.close()
            url = f"ws://{self._remote_host}:{self._remote_port}/ws"
            if not save_remote_config(url, self._remote_token, self._remote_switch.isChecked()):
                logger.error("保存远程配置失败")
            if self._remote_switch.isChecked() and self._remote_control_callback:
                self._remote_control_callback(False)
                self._remote_control_callback(True)

        cancel_btn.clicked.connect(popup.close)
        save_btn.clicked.connect(_do_save)
        save_cfg_btn.clicked.connect(_do_save_config)
        addr_edit.returnPressed.connect(_do_save)
        token_edit.returnPressed.connect(_do_save)

    # ── VB-Cable 安装 ──

    def _show_vbcable_dialog(self):
        self._vbcable_prompted = True

        msg = QMessageBox(self)
        msg.setWindowTitle("VB-Cable 驱动未安装")
        msg.setText(
            "VB-Cable 虚拟声卡用于在 Windows 上实现虚拟麦克风功能。\n"
            "是否自动安装？"
        )
        msg.setIcon(QMessageBox.Question)
        msg.setDefaultButton(None)

        install_btn = msg.addButton("一键安装", QMessageBox.AcceptRole)
        manual_btn = msg.addButton("手动下载", QMessageBox.HelpRole)
        skip_btn = msg.addButton("暂不安装", QMessageBox.RejectRole)

        msg.setEscapeButton(skip_btn)
        msg.exec_()

        clicked = msg.clickedButton()

        if clicked is install_btn:
            self._on_install_vbcable()
        elif clicked is manual_btn:
            import webbrowser
            webbrowser.open("https://vb-audio.com/Cable/")
            logger.info("用户选择手动下载 VB-Cable")
        else:
            logger.info("用户选择暂不安装 VB-Cable，使用纯监听模式")
            self._mic_label.setText("仅监听")
            self._mic_label.setStyleSheet("color: orange;")

    def _on_install_vbcable(self):
        if VBCableInstaller.is_busy():
            logger.warning("VB-Cable 安装已在进行中")
            return

        self._vbcable_install_btn.setEnabled(False)
        self._vbcable_install_btn.setText("安装中...")

        self._installer = VBCableInstaller()
        self._installer.progress.connect(self._on_install_progress)
        self._installer.finished.connect(self._on_install_result)
        self._installer.error_occurred.connect(self._on_install_error)
        self._installer.start()

    def _on_install_progress(self, msg: str):
        logger.info(f"[VB-Cable] {msg}")

    def _on_install_result(self, success: bool, message: str):
        self._vbcable_install_btn.setEnabled(True)
        self._vbcable_install_btn.setText("安装")

        if success:
            logger.info(f"VB-Cable 安装完成，正在重启: {message}")
            reply = QMessageBox.warning(
                self,
                "安装完成",
                "VB-Cable 安装完成，应用即将重启。\n\n"
                "⚠ Windows 可能将默认扬声器切换到了 VB-Cable。\n"
                "重启后若听不到声音，请在系统声音设置中\n"
                "将默认扬声器改回原来的设备。\n\n"
                "右键任务栏扬声器图标 → 声音设置 → 输出设备",
                QMessageBox.Ok,
            )
            self._restart_app()
        else:
            logger.error(f"VB-Cable 安装失败: {message}")
            self._vbcable_install_btn.show()
            QMessageBox.warning(
                self,
                "安装失败",
                f"VB-Cable 安装失败：{message}\n\n"
                "请尝试手动下载安装：\n"
                "https://vb-audio.com/Cable/",
            )

    @staticmethod
    def _restart_app():
        if getattr(sys, 'frozen', False):
            subprocess.Popen(
                [sys.executable],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
        else:
            app_dir = Path(__file__).resolve().parent.parent
            app_path = str(app_dir / "app.py")
            subprocess.Popen(
                [sys.executable, app_path],
                cwd=str(app_dir),
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
        QApplication.instance().quit()

    def _on_install_error(self, error_type: str, message: str):
        logger.error(f"[VB-Cable] {error_type}: {message}")

    # ── VB-Cable ──

    def _check_vb_cable(self):
        self._service.detect_vb_cable()

    # ── 清理 ──

    def eventFilter(self, obj, event):
        if obj is self._monitor_combo and event.type() == QEvent.MouseButtonPress:
            self._populate_monitor_devices()
        return super().eventFilter(obj, event)

    def cleanup(self):
        if hasattr(self, '_log_bridge'):
            logger.removeHandler(self._log_bridge)
        if self._installer and self._installer.isRunning():
            self._installer.quit()
            self._installer.wait(2000)
