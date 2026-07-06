# -*- coding: utf-8 -*-
"""
MainWindow — 宽窗口双栏布局，左侧聊天 + 右侧面板。
"""

import os
import re
import sys
import logging
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QBrush, QPixmap, QIcon
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QHBoxLayout, QWidget, QSplitter
from qfluentwidgets import MSFluentWindow, setTheme, setThemeColor, Theme, isDarkTheme, TransparentToolButton, FluentIcon

from service.tts_service import TTSService
from app.chat_widget import ChatWidget
from app.remote_receiver import RemoteReceiver
from app.settings_panel import SettingsPanel
from app.utils import cfg
from engines.edge import EdgeEngine
from engines.sapi5 import SystemTTSEngine
from config import (
    WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT,
    PANEL_MIN_WIDTH, get_theme,
)

_ICON_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "icon.svg")

logger = logging.getLogger("TTSMicInjector")


def _system_is_dark() -> bool:
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except Exception:
            return False
    return False


def _render_svg(path: str, size: int, color: QColor) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(path).render(p)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), color)
    p.end()
    return pm


def _paint_mic_pixmap(size: int, dark: bool = True) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    w, h = pm.rect().width(), pm.rect().height()
    mx = w / 2
    body_w = w * 0.26
    body_h = h * 0.40
    body_top = h * 0.06
    arc_size_w = h * 0.56
    arc_top = h * 0.14
    line_bottom = h * 0.88
    stem_top = body_top + body_h

    color = QColor(255, 255, 255) if dark else QColor(60, 60, 60)
    pen = QPen(color, max(1, int(w * 0.07)))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(color.lighter(160)))

    body_left = int(mx - body_w / 2)
    p.drawRoundedRect(body_left, int(body_top), int(body_w), int(body_h),
                      body_w * 0.4, body_w * 0.4)

    p.setBrush(Qt.NoBrush)
    arc_left = int(mx - arc_size_w / 2)
    p.drawArc(arc_left, int(arc_top), int(arc_size_w), int(arc_size_w), -30 * 16, -120 * 16)

    p.drawLine(int(mx), int(stem_top), int(mx), int(line_bottom))

    base_w = int(w * 0.55)
    p.drawLine(int(mx - base_w / 2), int(line_bottom),
               int(mx + base_w / 2), int(line_bottom))

    p.end()
    return pm


class MainWindow(MSFluentWindow):
    """TTS Mic Injector 主窗口 — 双栏布局。"""

    def __init__(self):
        super().__init__()

        self._system_dark = _system_is_dark()
        setTheme(Theme.DARK if self._system_dark else Theme.LIGHT)
        setThemeColor(cfg.themeColor.value)

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(cfg.windowMinsizeW.value, cfg.windowMinsizeH.value)

        self.navigationInterface.hide()

        self.titleBar.hBoxLayout.removeWidget(self.titleBar.iconLabel)
        self.titleBar.iconLabel.deleteLater()
        self.titleBar.titleLabel.setStyleSheet("margin-left: 0px; padding-left: 0px;")
        self._set_window_icon()

        self._panel_toggle = TransparentToolButton(FluentIcon.MENU, self)
        self._panel_toggle.setToolTip("显示/隐藏右侧面板")
        self._panel_toggle.clicked.connect(self._toggle_panel)
        layout = self.titleBar.hBoxLayout

        # 移除 MSFluentTitleBar 添加的 20px 左侧间距和 2px 间距
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item and item.spacerItem() is not None:
                size = item.spacerItem().sizeHint()
                if size.width() >= 20 or size.width() == 2:
                    layout.removeItem(item)

        layout.insertSpacing(0, 8)
        layout.insertWidget(1, self._panel_toggle)
        layout.insertSpacing(2, 2)

        self._service = TTSService()
        self._chat = ChatWidget(self._service)
        self._settings = SettingsPanel(self._service)

        self._remote = None
        self._settings.set_remote_control_callback(self._on_remote_control)

        self._chat.set_speak_callback(self._on_speak)
        self._settings.set_theme_change_callback(self._on_theme_changed)

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setHandleWidth(2)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._chat)
        self._splitter.addWidget(self._settings)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        default_panel_w = 384
        self._splitter.setSizes([800 - default_panel_w - 20, default_panel_w])
        self._splitter.splitterMoved.connect(self._on_splitter_moved)

        self._panel_visible = True
        self._panel_width = PANEL_MIN_WIDTH
        self._saved_sizes = None

        self._central = QWidget()
        self._central.setObjectName("centralWidget")
        self._central.setAttribute(Qt.WA_StyledBackground, True)
        hbox = QHBoxLayout(self._central)
        hbox.setContentsMargins(8, 8, 8, 8)
        hbox.setSpacing(0)
        hbox.addWidget(self._splitter)

        self.stackedWidget.addWidget(self._central)
        self.stackedWidget.setCurrentWidget(self._central)

        self._apply_theme(self._system_dark)

    def _on_theme_changed(self, dark: bool):
        self._apply_theme(dark)
        self._chat._on_theme_changed(dark)

    def _toggle_panel(self):
        margins = 8 + 8
        handle = self._splitter.handleWidth()
        chat_min = self._chat.minimumWidth()

        if self._panel_visible:
            self._saved_sizes = self._splitter.sizes()
            panel_w = self._settings.width()
            self._settings.hide()
            self._panel_visible = False
            self._panel_toggle.setToolTip("显示右侧面板")
            self.setMinimumWidth(chat_min + margins)
            self.resize(max(self.width() - panel_w - handle, chat_min + margins),
                       self.height())
        else:
            self._settings.show()
            self._panel_visible = True
            self._panel_toggle.setToolTip("隐藏右侧面板")
            if self._saved_sizes:
                panel_w = self._saved_sizes[1]
                self._splitter.setSizes(self._saved_sizes)
            else:
                panel_w = 384
                self._splitter.setSizes([self.width(), panel_w])
            self.setMinimumWidth(chat_min + panel_w + margins + handle)
            self.resize(self.width() + panel_w + handle, self.height())

    def _apply_theme(self, dark: bool):
        t = get_theme(dark)
        self.setStyleSheet(f"MSFluentWindow {{ background-color: {t['window_bg']}; }}")
        self._central.setStyleSheet(
            f"QWidget#centralWidget {{ background-color: {t['central_bg']}; }}"
        )
        if dark:
            self._splitter.setStyleSheet(
                "QSplitter::handle { background-color: rgba(255,255,255,0.08); }"
                "QSplitter::handle:hover { background-color: rgba(255,255,255,0.15); }"
            )
        else:
            self._splitter.setStyleSheet(
                "QSplitter::handle { background-color: rgba(0,0,0,0.10); }"
                "QSplitter::handle:hover { background-color: rgba(0,0,0,0.18); }"
            )

    def _on_splitter_moved(self, pos, index):
        if not self._settings.isVisible():
            return

        panel_w = self._settings.width()
        chat_min = self._chat.minimumWidth()
        margins = 8 + 8 + self._splitter.handleWidth()

        if panel_w >= PANEL_MIN_WIDTH:
            self._saved_sizes = self._splitter.sizes()
            self._panel_width = panel_w
            self.setMinimumWidth(panel_w + chat_min + margins)
        elif panel_w >= 32:
            self._splitter.blockSignals(True)
            self._splitter.setSizes([self._splitter.sizes()[0], PANEL_MIN_WIDTH])
            self._splitter.blockSignals(False)
        else:
            self._settings.hide()
            self._panel_visible = False
            self._panel_toggle.setToolTip("显示右侧面板")

    def _on_remote_control(self, enabled):
        if enabled:
            if self._remote:
                self._remote.stop()
            url = self._settings.remote_server_url
            token = self._settings.remote_token
            self._remote = RemoteReceiver(url, token, parent=self)
            self._remote.message_received.connect(self._on_remote_message)
            self._settings.set_remote_receiver(self._remote)
            self._remote.start()
        else:
            if self._remote:
                self._remote.stop()
                self._remote = None

    def _on_remote_message(self, text: str):
        self._chat.add_message(text)
        self._on_speak(text, False)

    def _on_speak(self, text: str, save_to_disk: bool = False):
        speed = self._settings.speed_value
        volume = self._settings.volume_value / 100.0
        if isinstance(self._service.engine, EdgeEngine):
            self._service.engine.set_pitch(self._settings.pitch_value)
        save_path = None
        if save_to_disk:
            save_path = self._make_save_path(text)
        self._service.speak(text, speed, volume, save_path=save_path)

    @staticmethod
    def _make_save_path(text: str) -> str:
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]', '', text)
        safe = re.sub(r'\s+', ' ', safe).strip()
        safe = safe[:10] if safe else "audio"
        out_dir = os.path.join(os.getcwd(), "output")
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, f"{ts}_{safe}.wav")

    def _set_window_icon(self):
        dark = isDarkTheme()
        ratio = max(1.0, self.devicePixelRatioF())
        abs_path = os.path.normpath(_ICON_PATH)
        color = QColor(255, 255, 255) if dark else QColor(60, 60, 60)
        icon_size = int(32 * ratio)
        if os.path.exists(abs_path):
            icon = QIcon(_render_svg(abs_path, icon_size, color))
        else:
            icon = QIcon(_paint_mic_pixmap(icon_size, dark))
        self.setWindowIcon(icon)

    def _update_title_icon(self):
        dark = isDarkTheme()
        ratio = max(1.0, self.devicePixelRatioF())
        abs_path = os.path.normpath(_ICON_PATH)
        color = QColor(255, 255, 255) if dark else QColor(60, 60, 60)
        if os.path.exists(abs_path):
            pm = _render_svg(abs_path, int(24 * ratio), color)
        else:
            pm = _paint_mic_pixmap(int(24 * ratio), dark)
        pm.setDevicePixelRatio(ratio)
        self.titleBar.iconLabel.setPixmap(pm)
        icon_size = int(32 * ratio)
        if os.path.exists(abs_path):
            icon = QIcon(_render_svg(abs_path, icon_size, color))
        else:
            icon = QIcon(_paint_mic_pixmap(icon_size, dark))
        self.setWindowIcon(icon)

    def closeEvent(self, event):
        self._chat.stop()
        if self._remote:
            self._remote.stop()
        self._service.stop()
        if isinstance(self._service.engine, SystemTTSEngine):
            self._service.engine.stop()
        self._settings.cleanup()
        event.accept()
