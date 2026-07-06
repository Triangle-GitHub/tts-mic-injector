# -*- coding: utf-8 -*-
"""
TTSInterface — TTS 主界面，所有控件和事件绑定。
参照 ui/app.py 的原 Tkinter 行为，用 PyQt5 + qfluentwidgets 重写。
"""

import re
import os
import logging
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QPointF
from PyQt5.QtGui import QPalette, QColor, QPen
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QListWidget, QAbstractItemView, QListWidgetItem,
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, Slider, ComboBox,
    SwitchButton, BodyLabel, StrongBodyLabel, FluentIcon as FIF,
    TextEdit, Theme, setTheme, setThemeColor, isDarkTheme, qrouter,
)

from config import (
    SPEED_MIN, SPEED_MAX,
    VOLUME_DEFAULT,
    WINDOW_TITLE, WINDOW_MINSIZE,
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


def _make_text_style(dark: bool) -> str:
    """生成控件样式表，含 TextEdit / QListWidget / QGroupBox。"""
    if dark:
        return (
            "TextEdit {"
            "  border: 1px solid rgba(255,255,255,0.12);"
            "  border-radius: 4px;"
            "  background-color: rgba(255,255,255,0.06);"
            "  color: rgba(255,255,255,0.9);"
            "}"
            "QListWidget {"
            "  border: 1px solid rgba(255,255,255,0.12);"
            "  border-radius: 4px;"
            "  background-color: rgba(255,255,255,0.06);"
            "  color: rgba(255,255,255,0.9);"
            "}"
            "QListWidget::item { padding: 3px 6px; }"
            "QGroupBox {"
            "  color: rgba(255,255,255,0.85);"
            "  font-weight: bold;"
            "}"
            "QGroupBox::title {"
            "  color: rgba(255,255,255,0.85);"
            "}"
        )
    else:
        return (
            "TextEdit {"
            "  border: 1px solid rgba(0,0,0,0.12);"
            "  border-radius: 4px;"
            "  background-color: white;"
            "  color: rgba(0,0,0,0.85);"
            "}"
            "QListWidget {"
            "  border: 1px solid rgba(0,0,0,0.12);"
            "  border-radius: 4px;"
            "  background-color: white;"
            "  color: rgba(0,0,0,0.85);"
            "}"
            "QListWidget::item { padding: 3px 6px; }"
            "QGroupBox {"
            "  color: rgba(0,0,0,0.85);"
            "  font-weight: bold;"
            "}"
            "QGroupBox::title {"
            "  color: rgba(0,0,0,0.85);"
            "}"
        )


class _VolumeSlider(Slider):
    def _drawHorizonTick(self, painter):
        r = self.handle.width() / 2
        mid = r + 0.5 * (self.width() - r * 2)
        c = QColor(255, 255, 255, 60) if isDarkTheme() else QColor(0, 0, 0, 60)
        painter.setPen(QPen(c, 1))
        painter.drawLine(QPointF(mid, r - 6), QPointF(mid, r + 6))


class TTSInterface(QWidget):
    """TTS Mic Injector 主界面。"""

    ENGINE_SPEED_RANGES = {
        "eSpeak": (80, 450),
        "SAPI5": (50, 400),
        "Piper": (50, 200),
        "Edge": (50, 200),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = TTSService()

        self._monitor_enabled = MONITOR_ENABLED_DEFAULT
        self._monitor_devices = {}
        self._voice_id_map = {}
        self._current_voice_name = ""

        self.setObjectName("TTSInterface")

        self._init_getters()
        self._register_callbacks()
        self._init_ui()
        self._init_engine()
        self._setup_delayed_tasks()

        logger.info("应用已启动")

    # ── Service 状态获取器注入 ──
    def _init_getters(self):
        self._service.set_monitor_state_getter(lambda: self._monitor_enabled)
        self._service.set_monitor_device_getter(self._get_monitor_device_index)
        self._service.set_pitch_getter(lambda: self._pitch_slider.value())
        self._service.set_volume_getter(lambda: self._volume_slider.value() / 100.0)

    # ── Service 回调注册 ──
    def _register_callbacks(self):
        self._service.on("status", self._on_service_status)
        self._service.on("engine_ready", self._on_service_engine_ready)
        self._service.on("vb_cable_detected", self._on_vb_cable_detected)
        self._service.on("vb_cable_error", self._on_vb_cable_error)

    def _on_service_status(self, text, color):
        QTimer.singleShot(0, lambda: self._update_status(text, color))

    def _on_service_engine_ready(self, name):
        pass

    def _on_vb_cable_detected(self, idx):
        QTimer.singleShot(0, lambda: self._mic_label.setText("🎤 CABLE Input ✅"))
        QTimer.singleShot(0, lambda: self._mic_label.setStyleSheet("color: green;"))

    def _on_vb_cable_error(self, msg):
        QTimer.singleShot(0, self._set_mic_error)

    def _set_mic_error(self):
        if pyaudio is None:
            self._mic_label.setText("🎤 pyaudio 未安装")
            self._mic_label.setStyleSheet("color: orange;")
        else:
            self._mic_label.setText("🎤 未检测到")
            self._mic_label.setStyleSheet("color: red;")

    def _update_status(self, text, color):
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};")

    # ── 初始化引擎 ──
    def _init_engine(self):
        success = self._service.start_engine("SAPI5")
        if success:
            logger.info(f"引擎就绪: {self._service.engine.name}")
        else:
            logger.error("SAPI5 初始化失败")

    # ── 主题 ──
    def _on_theme_toggle(self, checked):
        theme = Theme.DARK if checked else Theme.LIGHT
        setTheme(theme)
        self._apply_text_styles()

    def _apply_text_styles(self):
        dark = isDarkTheme()
        style = _make_text_style(dark)
        self.setStyleSheet(style)

    # ── 构建 UI ──
    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        self._build_history_section(main_layout)
        self._build_input_section(main_layout)
        self._build_control_bar(main_layout)
        self._build_engine_section(main_layout)
        self._build_voice_section(main_layout)
        self._build_pitch_section(main_layout)
        self._build_bottom_bar(main_layout)
        self._build_log_section(main_layout)

        self._input_text.setFocus()

        self._voice_group.hide()
        self._pitch_group.hide()
        self._edge_locale_combo.hide()
        self._monitor_combo.hide()

        self._install_keyboard_shortcuts()
        self._apply_text_styles()

    # ── 历史记录区域 ──
    def _build_history_section(self, parent):
        group = QGroupBox("历史记录")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 12, 6, 6)

        self._history_list = QListWidget()
        self._history_list.setMaximumHeight(HISTORY_HEIGHT * 28)
        self._history_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._history_list.itemClicked.connect(self._on_history_click)
        layout.addWidget(self._history_list)

        btn_layout = QHBoxLayout()
        clear_btn = PushButton("清空")
        clear_btn.clicked.connect(self._on_clear_history)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        parent.addWidget(group)

    # ── 输入区域 ──
    def _build_input_section(self, parent):
        group = QGroupBox("输入文字（Enter 或 ▶ 发送，ESC 停止）")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 12, 6, 6)

        self._input_text = TextEdit()
        self._input_text.setMaximumHeight(INPUT_HEIGHT * 28)
        self._input_text.setPlaceholderText("在此输入要合成的文字...")
        layout.addWidget(self._input_text)

        parent.addWidget(group)

    # ── 控制栏 ──
    def _build_control_bar(self, parent):
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._play_btn = PrimaryPushButton("▶  播放")
        self._play_btn.clicked.connect(self._on_play)
        bar.addWidget(self._play_btn)

        self._stop_btn = PushButton("■  停止")
        self._stop_btn.clicked.connect(self._on_stop)
        bar.addWidget(self._stop_btn)

        bar.addSpacing(16)

        bar.addWidget(BodyLabel("语速:"))
        self._speed_slider = Slider(Qt.Horizontal)
        self._speed_slider.setRange(SPEED_MIN, SPEED_MAX)
        self._speed_slider.setValue(get_engine_default("SAPI5").get("speed", 225))
        self._speed_slider.setMinimumWidth(SPEED_SCALE_LENGTH)
        self._speed_slider.valueChanged.connect(self._on_speed_change)
        bar.addWidget(self._speed_slider)

        self._speed_label = BodyLabel(str(get_engine_default("SAPI5").get("speed", 225)))
        self._speed_label.setMinimumWidth(36)
        bar.addWidget(self._speed_label)

        bar.addSpacing(8)

        bar.addWidget(BodyLabel("音量:"))
        self._volume_slider = _VolumeSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 200)
        self._volume_slider.setValue(VOLUME_DEFAULT)
        self._volume_slider.setMinimumWidth(VOLUME_SCALE_LENGTH)
        self._volume_slider.valueChanged.connect(self._on_vol_change)
        bar.addWidget(self._volume_slider)

        self._volume_label = BodyLabel(f"{VOLUME_DEFAULT}%")
        self._volume_label.setMinimumWidth(36)
        bar.addWidget(self._volume_label)

        bar.addStretch()
        parent.addLayout(bar)

    # ── TTS 引擎选择 ──
    def _build_engine_section(self, parent):
        group = QGroupBox("TTS 引擎（点击即切换，不中断当前播放）")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(6, 12, 6, 6)
        layout.setSpacing(6)

        self._engine_btns = {}
        engine_names = ["Aliyun", "Edge", "SAPI5", "eSpeak", "Piper"]
        for name in engine_names:
            btn = PushButton(name)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, n=name: self._switch_engine(n))
            layout.addWidget(btn)
            self._engine_btns[name] = btn

        layout.addStretch()
        self._engine_label = StrongBodyLabel(" 当前: SAPI5")
        self._engine_label.setStyleSheet("color: #2a7a2a;")
        layout.addWidget(self._engine_label)

        parent.addWidget(group)

    # ── 语音选择 ──
    def _build_voice_section(self, parent):
        self._voice_group = QGroupBox("系统语音选择")
        layout = QVBoxLayout(self._voice_group)
        layout.setContentsMargins(6, 12, 6, 6)

        self._edge_locale_combo = ComboBox()
        self._edge_locale_combo.currentIndexChanged.connect(self._on_edge_locale_select)
        layout.addWidget(self._edge_locale_combo)

        self._voice_combo = ComboBox()
        self._voice_combo.currentIndexChanged.connect(self._on_voice_select)
        layout.addWidget(self._voice_combo)

        parent.addWidget(self._voice_group)

    # ── Edge 音调 ──
    def _build_pitch_section(self, parent):
        self._pitch_group = QGroupBox("Edge 音调")
        layout = QHBoxLayout(self._pitch_group)
        layout.setContentsMargins(6, 12, 6, 6)

        layout.addWidget(BodyLabel("音调:"))
        self._pitch_slider = Slider(Qt.Horizontal)
        self._pitch_slider.setRange(EDGE_PITCH_MIN, EDGE_PITCH_MAX)
        self._pitch_slider.setValue(PITCH_DEFAULT)
        self._pitch_slider.setMinimumWidth(PITCH_SCALE_LENGTH)
        self._pitch_slider.valueChanged.connect(self._on_pitch_change)
        layout.addWidget(self._pitch_slider)

        self._pitch_label = BodyLabel(f"{edge_pitch:+d}Hz")
        layout.addWidget(self._pitch_label)
        layout.addStretch()

        parent.addWidget(self._pitch_group)

    # ── 底部栏：监听 + 状态 + 主题切换 ──
    def _build_bottom_bar(self, parent):
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._monitor_switch = SwitchButton()
        self._monitor_switch.setChecked(MONITOR_ENABLED_DEFAULT)
        self._monitor_switch.checkedChanged.connect(self._on_monitor_toggle)
        bar.addWidget(BodyLabel("监听"))
        bar.addWidget(self._monitor_switch)

        self._monitor_combo = ComboBox()
        self._monitor_combo.setMinimumWidth(200)
        bar.addWidget(self._monitor_combo)

        bar.addStretch()

        bar.addWidget(BodyLabel("深色"))
        self._theme_switch = SwitchButton()
        self._theme_switch.setChecked(isDarkTheme())
        self._theme_switch.checkedChanged.connect(self._on_theme_toggle)
        bar.addWidget(self._theme_switch)

        bar.addSpacing(12)

        self._status_label = BodyLabel("🟢 就绪")
        self._status_label.setStyleSheet("color: green;")
        bar.addWidget(self._status_label)

        bar.addSpacing(12)

        self._mic_label = BodyLabel("🎤 未检测")
        self._mic_label.setStyleSheet("color: red;")
        bar.addWidget(self._mic_label)

        parent.addLayout(bar)

    # ── 日志 ──
    def _build_log_section(self, parent):
        group = QGroupBox("日志")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 12, 4, 4)

        self._log_text = TextEdit()
        self._log_text.setReadOnly(True)
        layout.addWidget(self._log_text)

        parent.addWidget(group, stretch=1)

        self._log_bridge = LogBridge(self._log_text)
        self._log_bridge.set_max_lines(200)
        logger.addHandler(self._log_bridge)

    # ── 键盘快捷键 ──
    def _install_keyboard_shortcuts(self):
        from PyQt5.QtWidgets import QShortcut
        from PyQt5.QtGui import QKeySequence
        self._input_text.installEventFilter(self)
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.activated.connect(self._on_stop)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self._input_text and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                modifiers = event.modifiers()
                if modifiers & Qt.ControlModifier:
                    self._on_ctrl_enter()
                else:
                    self._on_enter()
                return True
        return super().eventFilter(obj, event)

    # ── 引擎切换 ──
    def _switch_engine(self, name):
        if name == "eSpeak":
            if not self._service.switch_engine("eSpeak"):
                return
            self._engine_label.setText(" 当前: eSpeak")
            self._voice_group.hide()
            self._pitch_group.hide()
            self._edge_locale_combo.hide()
            self._update_speed_range("eSpeak", self.ENGINE_SPEED_RANGES["eSpeak"])
            logger.info("切换到引擎: eSpeak")

        elif name == "SAPI5":
            if pythoncom is None:
                logger.error("pywin32 未安装。请执行: pip install pywin32")
                return
            if not self._service.switch_engine("SAPI5"):
                return
            self._engine_label.setText(" 当前: SAPI5")
            self._voice_group.setTitle("系统语音选择")
            self._populate_voice_combo()
            self._pitch_group.hide()
            self._edge_locale_combo.hide()
            self._voice_group.show()
            self._update_speed_range("SAPI5", self.ENGINE_SPEED_RANGES["SAPI5"])
            logger.info("切换到引擎: SAPI5")

        elif name == "Piper":
            if not self._service.switch_engine("Piper"):
                return
            self._engine_label.setText(" 当前: Piper")
            self._voice_group.setTitle("Piper 模型选择")
            self._populate_voice_combo()
            self._pitch_group.hide()
            self._edge_locale_combo.hide()
            self._voice_group.show()
            self._update_speed_range("Piper", self.ENGINE_SPEED_RANGES["Piper"])
            logger.info("切换到引擎: Piper")

        elif name == "Edge":
            if edge_tts is None:
                logger.error("edge-tts 未安装。请执行: pip install edge-tts")
                return
            if not self._service.switch_engine("Edge"):
                return
            self._engine_label.setText(" 当前: Edge")
            self._voice_group.setTitle("Edge 语音选择")
            self._edge_locale_combo.show()
            self._populate_edge_locales()
            self._pitch_group.show()
            edge_pitch = get_engine_default("Edge").get("pitch", 0)
            self._pitch_slider.setValue(edge_pitch)
            self._pitch_label.setText(f"{int(edge_pitch)}Hz")
            self._voice_group.show()
            self._update_speed_range("Edge", self.ENGINE_SPEED_RANGES["Edge"])
            logger.info("切换到引擎: Edge")

        elif name == "Aliyun":
            if dashscope is None:
                logger.error("dashscope 未安装。请执行: pip install dashscope")
                return
            if not self._service.switch_engine("Aliyun"):
                return
            self._engine_label.setText(" 当前: Aliyun")
            self._voice_group.setTitle("Aliyun 语音选择")
            self._populate_voice_combo()
            self._pitch_group.hide()
            self._edge_locale_combo.hide()
            self._voice_group.show()
            self._speed_slider.setEnabled(False)
            self._speed_label.setText("N/A")
            logger.info("切换到引擎: Aliyun")

        else:
            logger.info(f"引擎 {name} 尚未实现（预留按钮）")

    def _on_pitch_change(self, val):
        self._pitch_label.setText(f"{int(val):+d}Hz")
        if isinstance(self._service.engine, EdgeEngine):
            self._service.engine.set_pitch(int(val))

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
            old_voice = self._current_voice_name
            locales = engine.get_locales()
            self._edge_locale_combo.blockSignals(True)
            self._edge_locale_combo.clear()
            self._edge_locale_combo.addItems(locales)
            if old_locale in locales:
                self._edge_locale_combo.setText(old_locale)
                self._edge_locale_combo.blockSignals(False)
                self._on_edge_locale_select()
                items = [self._voice_combo.itemText(i) for i in range(self._voice_combo.count())]
                if old_voice in items:
                    self._voice_combo.setText(old_voice)
                    voice_id = self._voice_id_map.get(old_voice)
                    if voice_id:
                        self._service.engine.set_voice(voice_id)
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
            self._current_voice_name = voice_names[idx]

        engine.set_voice(voices[idx][0])

    def _populate_voice_combo(self):
        engine = self._service.engine
        if not engine:
            return
        voices = engine.get_voices()
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

        if idx < len(voice_names):
            self._current_voice_name = voice_names[idx]

    def _on_voice_select(self, index):
        if index < 0:
            return
        selected_name = self._voice_combo.itemText(index)
        self._current_voice_name = selected_name
        voice_id = self._voice_id_map.get(selected_name)
        if voice_id and hasattr(self._service.engine, 'set_voice'):
            self._service.engine.set_voice(voice_id)
            logger.info(f"语音切换为: {selected_name}")

    def _update_speed_range(self, engine_name, range_tuple):
        if range_tuple is None:
            return
        lo, hi = range_tuple
        self._speed_slider.setRange(lo, hi)
        self._speed_slider.setEnabled(True)
        default = get_engine_default(engine_name).get("speed", (lo + hi) // 2)
        self._speed_slider.setValue(default)
        self._speed_label.setText(str(default))

    # ── 监听 ──
    def _on_monitor_toggle(self, checked):
        self._monitor_enabled = checked
        if checked:
            self._populate_monitor_combo()
            self._monitor_combo.show()
        else:
            self._monitor_combo.hide()

    def _populate_monitor_combo(self):
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
        if not self._monitor_enabled:
            return None
        name = self._monitor_combo.text()
        return self._monitor_devices.get(name)

    # ── 语速/音量变化 ──
    def _on_speed_change(self, val):
        self._speed_label.setText(str(int(val)))

    def _on_vol_change(self, val):
        self._volume_label.setText(f"{int(val)}%")

    # ── 历史记录 ──
    def _add_history(self, text):
        self._history_list.addItem(text)
        self._history_list.scrollToBottom()

    def _on_history_click(self, item):
        text = item.text()
        if text:
            speed = self._speed_slider.value()
            volume = self._volume_slider.value() / 100.0
            self._service.speak(text, speed, volume)

    def _on_clear_history(self):
        self._history_list.clear()

    # ── Enter / 播放 / Ctrl+Enter ──
    def _on_enter(self):
        text = self._input_text.toPlainText().strip()
        if not text:
            return
        self._add_history(text)
        self._input_text.clear()
        self._do_speak(text=text)

    def _on_ctrl_enter(self):
        text = self._input_text.toPlainText().strip()
        if not text:
            return
        self._add_history(text)
        self._input_text.clear()
        self._do_speak(text=text, save_to_disk=True)

    def _on_play(self):
        text = self._input_text.toPlainText().strip()
        if text:
            self._add_history(text)
            self._input_text.clear()
        self._do_speak(text=text)

    def _do_speak(self, text=None, save_to_disk=False):
        if text is None:
            text = self._input_text.toPlainText().strip()
        if text:
            save_path = None
            if save_to_disk:
                save_path = self._make_save_path(text)
            speed = self._speed_slider.value()
            volume = self._volume_slider.value() / 100.0
            if isinstance(self._service.engine, EdgeEngine):
                self._service.engine.set_pitch(self._pitch_slider.value())
            self._service.speak(text, speed, volume, save_path=save_path)

    @staticmethod
    def _make_save_path(text):
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
            QTimer.singleShot(0, self._set_mic_error)

    # ── 延时任务 ──
    def _setup_delayed_tasks(self):
        QTimer.singleShot(200, self._populate_monitor_combo)
        QTimer.singleShot(300, self._check_vb_cable)

    # ── 清理 ──
    def cleanup(self):
        self._service.stop()
        if isinstance(self._service.engine, SystemTTSEngine):
            self._service.engine.stop()
        if hasattr(self, '_log_bridge'):
            logger.removeHandler(self._log_bridge)
