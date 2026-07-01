# -*- coding: utf-8 -*-
"""测试 TextHandler — 日志 → Tkinter Text 控件。"""

import os
import sys
import logging
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.log_handler import TextHandler


class TestTextHandler(unittest.TestCase):
    """TextHandler 的格式化、截断、异常处理。"""

    def setUp(self):
        self.mock_widget = MagicMock()
        self.handler = TextHandler(self.mock_widget)
        self.logger = logging.getLogger("test_text_handler")
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def test_formatter_present(self):
        self.assertIsNotNone(self.handler.formatter)
        fmt = self.handler.formatter._fmt
        self.assertIn("asctime", fmt)
        self.assertIn("message", fmt)

    def test_emit_schedules_via_after(self):
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "hello", (), None
        )
        self.handler.emit(record)
        self.mock_widget.after.assert_called()
        args = self.mock_widget.after.call_args
        self.assertEqual(args[0][0], 0)

    def test_append_writes_to_widget(self):
        self.handler._append("test message\n")
        self.mock_widget.config.assert_any_call(state="normal")  # tk.NORMAL
        self.mock_widget.insert.assert_called()
        self.mock_widget.see.assert_called()
        self.mock_widget.config.assert_any_call(state="disabled")  # tk.DISABLED

    def test_append_truncates_when_over_limit(self):
        from config import LOG_MAX_LINES

        many_lines = "\n".join(["line " + str(i) for i in range(LOG_MAX_LINES + 50)]) + "\n"
        self.mock_widget.get.return_value = many_lines

        self.handler._append("new line\n")
        self.mock_widget.delete.assert_called()

    def test_append_no_truncate_under_limit(self):
        from config import LOG_MAX_LINES

        few_lines = "\n".join(["line " + str(i) for i in range(50)]) + "\n"
        self.mock_widget.get.return_value = few_lines

        self.handler._append("new line\n")
        self.mock_widget.delete.assert_not_called()

    def test_tcl_error_silently_swallowed(self):
        import tkinter as tk
        self.mock_widget.config.side_effect = tk.TclError()
        self.handler._append("test\n")


class TestTextHandlerIntegration(unittest.TestCase):
    """端到端：真实 tk.Tk 上的 TextHandler。"""

    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()  # 隐藏窗口
        self.text = tk.Text(self.root, height=4, width=40)
        self.text.pack()
        self.handler = TextHandler(self.text)
        self.logger = logging.getLogger("test_integration")
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self):
        self.logger.removeHandler(self.handler)
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_log_appears_in_widget(self):
        self.logger.info("integration test message")
        self.root.update()  # 处理 after 队列

        content = self.text.get("1.0", "end-1c")
        self.assertIn("integration test message", content)

    def test_multiple_logs(self):
        for i in range(5):
            self.logger.info(f"message {i}")
        self.root.update()

        content = self.text.get("1.0", "end-1c")
        for i in range(5):
            self.assertIn(f"message {i}", content)
