# -*- coding: utf-8 -*-
"""
引擎工厂 — 根据名称创建 TTS 引擎实例，自动处理缺失依赖。
"""

from engines.base import TTSEngine


def create_engine(name: str, **kwargs):
    """根据名称创建引擎实例。
    
    参数:
        name: "eSpeak" | "SAPI5" | "Piper" | "Edge" | "Aliyun"
        kwargs: 传递给引擎构造函数的额外参数
               (Aliyun 支持 api_key, model, voice)
    
    返回:
        TTSEngine 实例
    
    异常:
        RuntimeError — 依赖缺失
        FileNotFoundError — 可执行文件未找到
    """
    if name == "eSpeak":
        from engines.espeak import EspeakEngine
        return EspeakEngine()
    elif name == "SAPI5":
        from engines.sapi5 import SystemTTSEngine
        return SystemTTSEngine()
    elif name == "Piper":
        from engines.piper import PiperEngine
        return PiperEngine()
    elif name == "Edge":
        from engines.edge import EdgeEngine
        return EdgeEngine()
    elif name == "Aliyun":
        from engines.aliyun import AliyunEngine
        return AliyunEngine(**kwargs)
    else:
        raise ValueError(f"未知引擎: {name}")
