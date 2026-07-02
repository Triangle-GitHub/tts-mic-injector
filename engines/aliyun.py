# -*- coding: utf-8 -*-
"""
AliyunEngine — 阿里云 Qwen TTS 实时引擎。
改造：api_key / model / voice 现在通过构造函数参数传入，不再直接读 config.json。
"""

import os
import json
import wave
import base64
import tempfile
import threading
import logging

from engines.base import TTSEngine
from config import load_aliyun_config, ALIYUN_SYNTH_TIMEOUT

try:
    import dashscope
    from dashscope.audio.qwen_tts_realtime import (
        QwenTtsRealtime, QwenTtsRealtimeCallback, AudioFormat
    )
except ImportError:
    dashscope = None
    QwenTtsRealtime = None
    QwenTtsRealtimeCallback = None
    AudioFormat = None

logger = logging.getLogger("TTSMicInjector")


class _AliyunCallback(QwenTtsRealtimeCallback if QwenTtsRealtimeCallback else object):
    """收集 PCM 数据到文件。"""

    def __init__(self, pcm_path):
        super().__init__()
        self._file = open(pcm_path, "wb")
        self._complete = threading.Event()

    def on_open(self):
        logger.debug("Aliyun TTS 连接已建立")

    def on_event(self, response):
        try:
            t = response["type"]
            if t == "response.audio.delta":
                self._file.write(base64.b64decode(response["delta"]))
            elif t == "response.done":
                logger.debug(f"Aliyun TTS response done: {response.get('response', {}).get('id', '')}")
            elif t == "session.finished":
                logger.debug("Aliyun TTS session finished")
                self._file.close()
                self._complete.set()
        except Exception as e:
            logger.debug(f"Aliyun cb on_event error: {e}")

    def on_close(self, close_status_code, close_msg):
        logger.debug(f"Aliyun TTS 连接关闭: code={close_status_code}, msg={close_msg}")
        if not self._file.closed:
            self._file.close()
        if not self._complete.is_set():
            self._complete.set()

    def wait_for_finished(self, timeout=ALIYUN_SYNTH_TIMEOUT):
        self._complete.wait(timeout=timeout)


class AliyunEngine(TTSEngine):
    """阿里云 Qwen TTS 实时引擎。"""
    name = "Aliyun"

    VOICES = [
        ("Cherry", "Cherry - 芊悦，阳光积极、亲切自然小姐姐"),
        ("Serena", "Serena - 苏瑶，温柔小姐姐"),
        ("Ethan", "Ethan - 晨煦，阳光温暖男声"),
        ("Chelsie", "Chelsie - 千雪，二次元虚拟女友"),
        ("Momo", "Momo - 茉兔，撒娇搞怪"),
        ("Vivian", "Vivian - 十三，拽拽可爱小暴躁"),
        ("Moon", "Moon - 月白，率性帅气男声"),
        ("Maia", "Maia - 四月，知性温柔"),
        ("Kai", "Kai - 凯，耳朵SPA男声"),
        ("Nofish", "Nofish - 不吃鱼，不会翘舌音设计师"),
        ("Bella", "Bella - 萌宝，小萝莉"),
        ("Jennifer", "Jennifer - 詹妮弗，电影质感美语女声"),
        ("Ryan", "Ryan - 甜茶，戏感炸裂男声"),
        ("Katerina", "Katerina - 卡捷琳娜，御姐"),
        ("Aiden", "Aiden - 艾登，美语大男孩"),
        ("Eldric Sage", "Eldric Sage - 沧明子，沉稳睿智老者"),
        ("Mia", "Mia - 乖小妹，温顺乖巧"),
        ("Mochi", "Mochi - 沙小弥，聪明伶俐小大人"),
        ("Bellona", "Bellona - 燕铮莺，字正腔圆女声"),
        ("Vincent", "Vincent - 田叔，沙哑烟嗓"),
        ("Bunny", "Bunny - 萌小姬，萌属性小萝莉"),
        ("Neil", "Neil - 阿闻，专业新闻主持人"),
        ("Elias", "Elias - 墨讲师，严谨又生动"),
        ("Arthur", "Arthur - 徐大爷，质朴嗓音"),
        ("Nini", "Nini - 邻家妹妹，又软又黏"),
        ("Seren", "Seren - 小婉，温和舒缓助眠"),
        ("Pip", "Pip - 顽屁小孩，调皮捣蛋"),
        ("Stella", "Stella - 少女阿月，甜到发腻"),
        ("Bodega", "Bodega - 博德加，热情西班牙大叔"),
        ("Sonrisa", "Sonrisa - 索尼莎，拉美大姐"),
        ("Alek", "Alek - 阿列克，战斗民族型男"),
        ("Dolce", "Dolce - 多尔切，慵懒意大利大叔"),
        ("Sohee", "Sohee - 素熙，韩国欧尼"),
        ("Ono Anna", "Ono Anna - 小野杏，鬼灵精怪青梅竹马"),
        ("Lenn", "Lenn - 莱恩，德国青年"),
        ("Emilien", "Emilien - 埃米尔安，法国大哥哥"),
        ("Andre", "Andre - 安德雷，磁性沉稳男声"),
        ("Radio Gol", "Radio Gol - 足球诗人解说"),
        ("Jada", "Jada - 上海阿珍，风风火火沪上阿姐"),
        ("Dylan", "Dylan - 北京晓东，胡同少年"),
        ("Li", "Li - 南京老李，瑜伽老师"),
        ("Marcus", "Marcus - 陕西秦川，老陕"),
        ("Roy", "Roy - 闽南阿杰，台湾哥仔"),
        ("Peter", "Peter - 天津李彼得，相声捧哏"),
        ("Sunny", "Sunny - 四川晴儿，甜到心里的川妹子"),
        ("Eric", "Eric - 四川程川，成都男子"),
        ("Rocky", "Rocky - 粤语阿强，幽默风趣"),
        ("Kiki", "Kiki - 粤语阿清，港妹闺蜜"),
    ]

    def __init__(self, api_key=None, model=None, voice=None):
        if dashscope is None:
            raise RuntimeError("dashscope 未安装。请执行: pip install dashscope")

        config = load_aliyun_config()

        # 优先级: 构造参数 > config.json > 环境变量
        api_key = (
            api_key
            or config.get("api_key")
            or os.environ.get("DASHSCOPE_API_KEY", "")
        )

        if not api_key:
            raise RuntimeError(
                "未配置 DashScope API Key。\n"
                "  方法1: 构造时传入 api_key 参数\n"
                "  方法2: 在 config.json 中设置 api_key 字段\n"
                "  方法3: 设置环境变量 DASHSCOPE_API_KEY"
            )
        dashscope.api_key = api_key

        self._model = model or config.get("model", "qwen3-tts-flash-realtime")
        self._voice = voice or config.get("voice", "Ethan")
        logger.info(f"Aliyun TTS 就绪，模型: {self._model}, 语音: {self._voice}")

    def get_voices(self):
        return [(v[0], v[1]) for v in self.VOICES]

    def set_voice(self, voice_name):
        self._voice = voice_name

    def get_current_voice(self):
        return self._voice

    def synthesize(self, text: str, speed: float, volume: float) -> str:
        fd, pcm_path = tempfile.mkstemp(suffix=".pcm", prefix="tts_aliyun_")
        os.close(fd)
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_aliyun_")
        os.close(fd)

        try:
            callback = _AliyunCallback(pcm_path)

            rt = QwenTtsRealtime(model=self._model, callback=callback)
            rt.connect()
            rt.update_session(
                voice=self._voice,
                response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                mode="server_commit",
            )
            rt.append_text(text)
            rt.finish()
            callback.wait_for_finished()

            with open(pcm_path, "rb") as f:
                pcm_data = f.read()

            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(pcm_data)

            return wav_path

        except Exception:
            if os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
            raise
        finally:
            if os.path.exists(pcm_path):
                try:
                    os.unlink(pcm_path)
                except OSError:
                    pass

    def get_speed_range(self):
        return None
