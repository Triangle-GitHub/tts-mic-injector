# -*- coding: utf-8 -*-
"""日志配置。"""

import os
import logging
from datetime import datetime

logger = logging.getLogger("TTSMicInjector")
logger.setLevel(logging.DEBUG)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_stream_handler)

_log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_name = datetime.now().strftime("%y-%m-%d_%H-%M-%S") + ".log"
_file_handler = logging.FileHandler(
    os.path.join(_log_dir, _log_name),
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
