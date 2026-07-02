# -*- coding: utf-8 -*-
"""
ChatWidget — 左侧聊天面板：消息列表 + 圆角输入框 + 发送按钮。
支持点击消息重放、播放中高亮、全局 ESC 停止。
"""

from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QEvent, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QShortcut,
    QSpacerItem, QSizePolicy,
)

from qfluentwidgets import (
    PrimaryPushButton, LineEdit, SmoothScrollArea, isDarkTheme,
)

from service.tts_service import TTSService
from app.message_item import MessageItem
from config import get_theme


class _ChatContainer(QWidget):
    """容器 widget，允许滚动区域将宽度缩至小于子控件首选宽度。"""
    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(80)
        return hint


class ChatWidget(QWidget):
    """聊天面板：左侧消息区 + 底部发送区。"""

    _sig_clear_playing = pyqtSignal()

    def __init__(self, service: TTSService, parent=None):
        super().__init__(parent)
        self._service = service
        self._playing_msg_id = None
        self._msg_counter = 0
        self._msg_data = {}
        self._items = {}
        self._pending_playback = False

        self._on_speak = None

        self.setObjectName("ChatWidget")
        self._sig_clear_playing.connect(self._clear_playing)
        self._build_ui()
        self._register_service_callbacks()

    # ── 公开接口 ──

    def set_speak_callback(self, callback):
        self._on_speak = callback

    def add_message(self, text: str) -> str:
        msg_id = str(self._msg_counter)
        self._msg_counter += 1
        self._msg_data[msg_id] = text

        ts = datetime.now().strftime("%H:%M:%S")
        item = MessageItem(msg_id, text, ts)

        item.clicked.connect(self._on_message_clicked)
        item.stop_requested.connect(self._on_message_stop)

        self._items[msg_id] = item
        self._msg_list_layout.insertWidget(self._msg_list_layout.count() - 1, item)

        vp_w = self._scroll.viewport().width()
        if vp_w > 0:
            item.set_max_text_width(vp_w)

        QTimer.singleShot(50, self._scroll_to_bottom)
        return msg_id

    def stop(self):
        self._pending_playback = False
        self._service.stop()
        self._set_playing(None)

    # ── UI 构建 ──

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_message_list(layout)
        self._build_input_bar(layout)

        # 全局 ESC 停止
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.activated.connect(self.stop)

    def _build_message_list(self, parent):
        self._scroll = SmoothScrollArea()
        self._scroll.setObjectName("chatScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.viewport().installEventFilter(self)
        self._apply_scroll_style(self._scroll)

        container = _ChatContainer()
        container.setObjectName("ChatContainer")
        self._msg_list_layout = QVBoxLayout(container)
        self._msg_list_layout.setContentsMargins(8, 8, 8, 8)
        self._msg_list_layout.setSpacing(4)

        # 顶部弹簧：消息少时靠上
        spacer = QSpacerItem(20, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self._msg_list_layout.addSpacerItem(spacer)

        self._scroll.setWidget(container)
        parent.addWidget(self._scroll, stretch=1)

    def _build_input_bar(self, parent):
        bar = QWidget()
        bar.setFixedHeight(56)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 8, 8, 8)
        bar_layout.setSpacing(8)

        self._input = LineEdit()
        self._input.setPlaceholderText("输入消息...")
        self._input.setClearButtonEnabled(True)
        self._input.installEventFilter(self)
        self._input.returnPressed.connect(self._on_send_enter)
        bar_layout.addWidget(self._input, stretch=1)

        self._send_btn = PrimaryPushButton("发送")
        self._send_btn.clicked.connect(lambda: self._do_send(save_to_disk=False))
        bar_layout.addWidget(self._send_btn)

        parent.addWidget(bar)

    # ── 样式 ──

    def _apply_scroll_style(self, scroll, dark=None):
        if dark is None:
            dark = isDarkTheme()
        t = get_theme(dark)
        bg = t["chat_bg"]
        scroll.setStyleSheet(
            f"#chatScrollArea {{"
            f"  background-color: {bg};"
            f"  border: none;"
            f"  border-radius: 8px;"
            f"}}"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

    def _register_service_callbacks(self):
        self._service.on("status", self._on_service_status)

    def _on_service_status(self, text, color):
        if "合成中" in text or "播放中" in text:
            self._pending_playback = False
        elif "就绪" in text and not self._pending_playback:
            self._sig_clear_playing.emit()

    # ── 发送 ──

    def eventFilter(self, obj, event):
        if hasattr(self, '_input') and obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if event.modifiers() & Qt.ControlModifier:
                    self._do_send(save_to_disk=True)
                    return True
        elif hasattr(self, '_scroll') and obj is self._scroll.viewport() and event.type() == QEvent.Resize:
            self._sync_msg_max_widths()
        return super().eventFilter(obj, event)

    def _on_send_enter(self):
        self._do_send(save_to_disk=False)

    def _do_send(self, save_to_disk: bool = False):
        text = self._input.text().strip()
        if not text:
            return
        if not self._service.concurrent_mode:
            self._service.stop()
        msg_id = self.add_message(text)
        self._input.clear()
        self._set_playing(msg_id)
        self._start_playback()
        if self._on_speak:
            self._on_speak(text, save_to_disk)

    # ── 消息点击重放 ──

    def _on_message_clicked(self, msg_id: str):
        if msg_id == self._playing_msg_id:
            return
        text = self._msg_data.get(msg_id)
        if text:
            if not self._service.concurrent_mode:
                self._service.stop()
            self._set_playing(msg_id)
            self._start_playback()
            if self._on_speak:
                self._on_speak(text, False)

    def _on_message_stop(self, msg_id: str):
        self._pending_playback = False
        self._service.stop()
        self._set_playing(None)

    def _start_playback(self):
        self._pending_playback = True
        QTimer.singleShot(500, lambda: setattr(self, '_pending_playback', False))

    # ── 播放状态 ──

    def _set_playing(self, msg_id: str | None):
        self._playing_msg_id = msg_id
        for mid, item in self._items.items():
            item.set_playing(mid == msg_id)

    def _clear_playing(self):
        self._set_playing(None)

    def _on_theme_changed(self, dark: bool = None):
        if dark is None:
            dark = isDarkTheme()
        if hasattr(self, '_scroll'):
            self._apply_scroll_style(self._scroll, dark)
        for item in self._items.values():
            item.refresh_theme(dark)

    def _sync_msg_max_widths(self):
        vp_w = self._scroll.viewport().width()
        if vp_w <= 0:
            return
        for item in self._items.values():
            item.set_max_text_width(vp_w)

    # ── 辅助 ──

    def _scroll_to_bottom(self):
        if hasattr(self, '_scroll'):
            sb = self._scroll.verticalScrollBar()
            sb.setValue(sb.maximum())
