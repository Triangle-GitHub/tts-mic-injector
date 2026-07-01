# -*- coding: utf-8 -*-
"""
TTS Mic Injector — 新入口 (PyQt5 + Fluent Design)
将文字通过 TTS 合成后输出到 VB-Cable 虚拟麦克风。
"""

import sys
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from app.utils import cfg

try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

os.chdir(os.path.dirname(os.path.abspath(__file__)))

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

    if pyttsx3 is None:
        print("提示: 安装 pyttsx3 可使用系统 TTS 引擎")
        print("  pip install pyttsx3")

    app = QApplication(sys.argv)
    app.setApplicationName("TTSMicInjector")

    from app.main_window import MainWindow
    window = MainWindow()
    window.show()

    try:
        sys.exit(app.exec_())
    finally:
        cfg.save()


if __name__ == "__main__":
    main()
