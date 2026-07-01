from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme

from .config import cfg


class StyleSheet(StyleSheetBase, Enum):
    """QSS 样式表枚举。"""
    TTS_INTERFACE = "tts_interface"

    def path(self, theme=Theme.AUTO):
        theme = cfg.theme if theme == Theme.AUTO else theme
        return f"assets/qss/{theme.value.lower()}/{self.value}.qss"
