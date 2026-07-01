# -*- coding: utf-8 -*-
"""
MessageItem — 聊天气泡组件，宽度自适应消息长度，
点击重放，播放中显示右侧停止按钮。
"""

from PyQt5.QtCore import Qt, pyqtSignal, QEvent
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy, QPushButton,
)
from qfluentwidgets import BodyLabel, CaptionLabel, isDarkTheme
from config import get_theme


class MessageItem(QWidget):
    """聊天气泡：自动宽度，点击重放，播放态高亮。"""

    clicked = pyqtSignal(str)
    stop_requested = pyqtSignal(str)

    def __init__(self, msg_id: str, text: str, timestamp: str, parent=None):
        super().__init__(parent)
        self._msg_id = msg_id
        self._text = text
        self._timestamp = timestamp
        self._playing = False

        self._build()
        self._apply_style()

    # ── UI ──

    def _build(self):
        self.setObjectName(f"msgRow_{self._msg_id}")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(6)

        # ── 气泡主体 ──
        self._bubble = QWidget()
        self._bubble.setObjectName(f"bubbleBody_{self._msg_id}")
        self._bubble.setCursor(Qt.PointingHandCursor)
        self._bubble.setAttribute(Qt.WA_StyledBackground, True)
        self._bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._bubble.installEventFilter(self)

        bubble_layout = QVBoxLayout(self._bubble)
        bubble_layout.setContentsMargins(14, 8, 14, 6)
        bubble_layout.setSpacing(2)

        self._text_label = BodyLabel(self._text)
        self._text_label.setWordWrap(True)
        self._text_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        bubble_layout.addWidget(self._text_label)

        self._time_label = CaptionLabel(self._timestamp)
        bubble_layout.addWidget(self._time_label, alignment=Qt.AlignLeft)

        root.addWidget(self._bubble)

        # ── 停止按钮（气泡外右侧） ──
        self._stop_btn = QPushButton()
        self._stop_btn.setFixedSize(26, 26)
        self._stop_btn.setText("■")
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.clicked.connect(lambda: self.stop_requested.emit(self._msg_id))
        self._stop_btn.hide()
        self._apply_stop_style()
        root.addWidget(self._stop_btn, alignment=Qt.AlignTop)

        # 右侧弹簧，把气泡和按钮推向左侧
        root.addStretch()

        self._update_max_width()

    def _update_max_width(self):
        container = self.parent()
        if container is None:
            return
        padding = 8 + 8
        stop_btn_width = 26 + 6
        available = container.width() - padding - stop_btn_width
        self._text_label.setMaximumWidth(max(120, available))

    # ── 样式 ──

    def _apply_style(self, dark: bool = None):
        if dark is None:
            dark = isDarkTheme()
        t = get_theme(dark)

        bg = t["bubble_bg"]
        text_color = t["bubble_text"]
        time_color = t["bubble_time"]

        extra = ""
        if self._playing:
            bg = t["bubble_playing_bg"]
            extra = f"border: 1px solid {t['bubble_playing_border']};"

        self._bubble.setStyleSheet(
            f"QWidget#{self._bubble.objectName()} {{"
            f"  background-color: {bg};"
            f"  border-radius: 10px;"
            f"  border-top-left-radius: 3px;"
            f"  {extra}"
            f"}}"
        )
        self._text_label.setStyleSheet(f"color: {text_color}; background: transparent; border: none;")
        self._time_label.setStyleSheet(f"color: {time_color}; background: transparent; border: none;")

    def _apply_stop_style(self, dark: bool = None):
        if dark is None:
            dark = isDarkTheme()
        t = get_theme(dark)
        self._stop_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {t['stop_btn_bg']};"
            f"  color: #dc3737;"
            f"  font-size: 12px;"
            f"  font-weight: bold;"
            f"  border: 1px solid {t['stop_btn_border']};"
            f"  border-radius: 13px;"
            f"}}"
        )

    # ── 状态 ──

    def set_playing(self, playing: bool):
        if self._playing == playing:
            return
        self._playing = playing
        self._stop_btn.setVisible(playing)
        self._apply_style()

    @property
    def msg_id(self) -> str:
        return self._msg_id

    @property
    def text(self) -> str:
        return self._text

    @property
    def is_playing(self) -> bool:
        return self._playing

    # ── 点击重放 ──

    def eventFilter(self, obj, event):
        if obj is self._bubble and event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton and not self._playing:
                self.clicked.emit(self._msg_id)
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_max_width()

    def refresh_theme(self, dark: bool = None):
        self._apply_style(dark)
        self._apply_stop_style(dark)
