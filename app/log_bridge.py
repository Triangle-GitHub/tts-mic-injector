# -*- coding: utf-8 -*-
"""
LogBridge — 将 Python logging 输出路由到 TextEdit 控件。
线程安全：通过 Qt 信号/槽机制跨线程更新 UI。
"""

import logging

from PyQt5.QtCore import QObject, pyqtSignal


class LogSignal(QObject):
    """线程安全的日志信号。"""
    append_signal = pyqtSignal(str)


class LogBridge(logging.Handler):
    """将日志输出到 TextEdit 控件（支持 QTextEdit / QPlainTextEdit / TextEdit）。"""

    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
        self._signal = LogSignal()
        self._signal.append_signal.connect(self._append)
        self._max_lines = 200
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s", datefmt="%H:%M:%S"
        ))

    def set_max_lines(self, n: int):
        self._max_lines = n

    def emit(self, record):
        msg = self.format(record) + "\n"
        self._signal.append_signal.emit(msg)

    def _append(self, msg):
        try:
            sb = self.text_edit.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4

            self.text_edit.setReadOnly(False)

            if hasattr(self.text_edit, 'appendPlainText'):
                self.text_edit.appendPlainText(msg.rstrip("\n"))
            else:
                self.text_edit.append(msg.rstrip("\n"))

            lines = self.text_edit.toPlainText().split("\n")
            if len(lines) > self._max_lines:
                cursor = self.text_edit.textCursor()
                cursor.movePosition(cursor.Start)
                for _ in range(len(lines) - self._max_lines):
                    cursor.movePosition(cursor.Down, cursor.KeepAnchor)
                cursor.removeSelectedText()

            self.text_edit.setReadOnly(True)

            if at_bottom:
                sb.setValue(sb.maximum())
        except RuntimeError:
            pass
