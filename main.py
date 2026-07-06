# -*- coding: utf-8 -*-
"""
TTS Mic Injector — 入口
将文字通过 TTS 合成后输出到 VB-Cable 虚拟麦克风，用于微信等 VoIP 通话。
"""

import sys
import logging

from ui.app import TTSMicInjectorApp
from service.tts_service import TTSService

try:
    import pyaudio
except ImportError:
    pyaudio = None

# ── 日志 ──
logger = logging.getLogger("TTSMicInjector")
logger.setLevel(logging.DEBUG)


def main():
    if sys.version_info < (3, 8):
        print("需要 Python 3.8+")
        sys.exit(1)

    if pyaudio is None:
        print("=" * 50)
        print("请安装依赖:")
        print("  pip install pyaudio")
        print("=" * 50)

    service = TTSService()
    app = TTSMicInjectorApp(service)
    app.run()


if __name__ == "__main__":
    main()
