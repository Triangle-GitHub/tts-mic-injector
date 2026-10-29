# -*- coding: utf-8 -*-
"""
TTS Mic Injector — 新入口 (PyQt5 + Fluent Design)
将文字通过 TTS 合成后输出到 VB-Cable 虚拟麦克风。
"""

import sys
import os
from io import StringIO

_stdout = sys.stdout
sys.stdout = StringIO()

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from app.utils import cfg

try:
    import pyaudio
except ImportError:
    pyaudio = None

sys.stdout = _stdout

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)


def main():
    if sys.version_info < (3, 8):
        print("需要 Python 3.8+")
        sys.exit(1)

    if pyaudio is None:
        print("=" * 50)
        print("请安装依赖:")
        print("  pip install pyaudio")
        print("=" * 50)

    app = QApplication(sys.argv)
    app.setApplicationName("TTSMicInjector")
    if sys.platform != "win32":
        # Wayland 下窗口的 app_id 决定 KWin 去关联哪个 .desktop（进而决定任务栏图标与分组）。
        # 不设的话 Qt 会退化成可执行文件名（python3.15），跟 tts-mic-injector.desktop 对不上，
        # 任务栏就只能显示通用图标。Windows 上没有这个概念，不动。
        QApplication.setDesktopFileName("tts-mic-injector")


    from app.main_window import MainWindow
    window = MainWindow()
    window.show()

    try:
        sys.exit(app.exec_())
    finally:
        cfg.save()


if __name__ == "__main__":
    main()
