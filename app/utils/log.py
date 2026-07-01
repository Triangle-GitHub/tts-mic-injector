# -*- coding: utf-8 -*-
"""日志配置。"""

import logging

logger = logging.getLogger("TTSMicInjector")
logger.setLevel(logging.DEBUG)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_stream_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
