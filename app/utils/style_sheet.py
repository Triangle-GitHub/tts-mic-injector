from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme

from .config import cfg
from config import _get_data_dir


class StyleSheet(StyleSheetBase, Enum):
    """QSS 样式表枚举。"""
    TTS_INTERFACE = "tts_interface"

    def path(self, theme=Theme.AUTO):
        theme = cfg.theme if theme == Theme.AUTO else theme
        return str(_get_data_dir() / "assets" / "qss" / theme.value.lower() / f"{self.value}.qss")
