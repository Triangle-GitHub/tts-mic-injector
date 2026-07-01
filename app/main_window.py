# -*- coding: utf-8 -*-
"""
MainWindow — Frameless 主窗口（使用 MSFluentWindow）。
模仿 XJTU Toolbox 的自定义标题栏风格。
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QPen, QColor, QBrush, QPixmap, QIcon
from PyQt5.QtSvg import QSvgRenderer
from qfluentwidgets import MSFluentWindow, setTheme, setThemeColor, Theme, isDarkTheme

from app.tts_interface import TTSInterface
from app.utils import cfg

_ICON_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "icons", "icon.svg")


def _render_svg(path: str, size: int, color: QColor) -> QPixmap:
    """将 SVG 渲染为指定颜色的 QPixmap。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(path).render(p)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), color)
    p.end()
    return pm


def _paint_mic_pixmap(size: int, dark: bool = True) -> QPixmap:
    """QPainter 绘制的麦克风兜底 Pixmap。"""
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
    def __init__(self):
        super().__init__()

        theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        setTheme(theme)
        setThemeColor(cfg.themeColor.value)

        self.setWindowTitle(cfg.windowTitle.value)
        self.resize(720, 620)
        self.setMinimumSize(cfg.windowMinsizeW.value, cfg.windowMinsizeH.value)

        self.navigationInterface.hide()

        self._update_title_icon()
        cfg.themeChanged.connect(self._update_title_icon)

        self._tts_interface = TTSInterface(self)
        self.stackedWidget.addWidget(self._tts_interface)
        self.stackedWidget.setCurrentWidget(self._tts_interface)

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
        if hasattr(self, '_tts_interface'):
            self._tts_interface.cleanup()
        event.accept()
