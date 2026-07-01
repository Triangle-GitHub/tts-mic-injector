# -*- coding: utf-8 -*-
"""
TextHandler — 将 logging 输出路由到 Tkinter Text 控件。
"""

import tkinter as tk
import logging
from config import LOG_MAX_LINES


class TextHandler(logging.Handler):
    """将日志输出到 tkinter Text 控件。"""

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record) + "\n"
        self.text_widget.after(0, self._append, msg)

    def _append(self, msg):
        try:
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.insert(tk.END, msg)
            lines = self.text_widget.get("1.0", tk.END).split("\n")
            if len(lines) > LOG_MAX_LINES:
                self.text_widget.delete("1.0", f"{len(lines) - LOG_MAX_LINES}.0")
            self.text_widget.see(tk.END)
            self.text_widget.config(state=tk.DISABLED)
        except tk.TclError:
            pass
