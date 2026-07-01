# TTS Mic Injector

将任意文字通过 TTS 合成后注入虚拟麦克风，在微信、QQ、钉钉等 VoIP 通话中使用。

> **适用场景**：语音通话中不方便打字、游戏开黑不想开麦、语言障碍辅助等。

![](https://img.shields.io/badge/python-3.8+-blue)
![](https://img.shields.io/badge/platform-Windows-lightgrey)
![](https://img.shields.io/badge/license-MIT-green)

## 功能

| 功能 | 说明 |
|------|------|
| 🎙️ 语音注入 | 合成后的音频直接输出到 VB-Cable 虚拟麦克风 |
| 💬 聊天式操作 | 左侧聊天面板，消息气泡可点击重放，播放中高亮 |
| 🎛️ 5 大引擎 | 阿里云 / Edge / SAPI5 / eSpeak / Piper 一键切换 |
| 🔊 实时监听 | 同时在扬声器播放，确认合成效果 |
| 🌗 深色模式 | 启动跟随系统主题，手动切换实时生效 |
| ⚡ 全局快捷键 | ESC 停止所有播放，Enter 发送，Ctrl+Enter 保存为文件 |
| 📝 消息历史 | 所有消息保留在聊天列表，点击即可重放 |
| 🔀 同时播放 | 可选允许多条消息同时播放 |

## 支持的 TTS 引擎

| 引擎 | 类型 | 音色数 | 语速调节 | 需要额外安装 |
|------|------|--------|----------|-------------|
| **SAPI5** | Windows 本地 | 系统安装的语音 | ✅ | pywin32 |
| **Edge** | 微软云端 (免费) | 数百种 | ✅ | edge-tts, ffmpeg |
| **eSpeak** | 本地开源 | 1 (中文) | ✅ | espeak-ng.exe |
| **Piper** | 本地神经网络 | 取决于.onnx模型 | ✅ | piper.exe + 模型 |
| **Aliyun** | 阿里云 Qwen TTS | 40+ | ❌ | dashscope + API Key |

## 安装

### 1. 安装 VB-Cable 虚拟声卡

从 [vb-audio.com/Cable](https://vb-audio.com/Cable/) 下载安装，系统声音设置中将「CABLE Input」设为默认通信设备。

### 2. 安装 Python 依赖

```bash
pip install pyaudio pywin32 edge-tts
pip install PyQt5 qfluentwidgets

# 可选
pip install dashscope          # 阿里云引擎
pip install pyttsx3            # 备用系统 TTS
```

### 3. 安装外部程序（按需）

| 引擎 | 下载地址 |
|------|---------|
| eSpeak NG | [github.com/espeak-ng/espeak-ng/releases](https://github.com/espeak-ng/espeak-ng/releases) |
| Piper | [github.com/rhasspy/piper/releases](https://github.com/rhasspy/piper/releases) |
| Piper 模型 | [huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) |
| ffmpeg | [ffmpeg.org/download.html](https://ffmpeg.org/download.html) |

将 `espeak-ng.exe`、`piper.exe`、`ffmpeg.exe` 放入 PATH 或项目根目录，`.onnx` 模型放入 `piper_models/`。

## 配置

编辑项目根目录的 `config.json`：

```json
{
    "aliyun": {
        "api_key": "sk-你的密钥",
        "model": "qwen3-tts-flash-realtime",
        "voice": "Ethan"
    },
    "paths": {
        "espeak": "espeak-ng.exe",
        "piper": "piper.exe",
        "piper_models": "piper_models",
        "ffmpeg": "ffmpeg"
    },
    "theme": {
        "dark": { "bubble_bg": "#3a3a3a", ... },
        "light": { "bubble_bg": "#f0f0f0", ... }
    }
}
```

完整配置项见 [config.json](config.json)。

阿里云 API Key 也可以通过环境变量设置：`DASHSCOPE_API_KEY=sk-xxx`。

## 运行

```bash
python app.py
```

## 界面

```
┌─────────────────────────────────────────────────┐
│  🎤  TTS Mic Injector              ─  □  ×     │
├──────────────────────┬──────────────────────────┤
│                      │  TTS 引擎                 │
│  ┌────聊天气泡───┐  │  [Aliyun][Edge][SAPI5]…  │
│  │ 你好,世界  ⏹ │  │  音色 [下拉选择      ▼]   │
│  │      14:32:01  │  │  语速 [═══════slider]     │
│  └──────────────┘  │  音量 [═══════slider]       │
│  ┌──────聊天─────┐ │  监听 [switch]             │
│  │ 这是另一条消息 │ │  深色模式 [switch]         │
│  │      14:32:15  │ │  同时播放 [switch]         │
│  └──────────────┘  │  ──────────────             │
│                      │  日志                      │
│  ┌───────────────┐  │  [滚动日志区域]            │
│  │ 输入消息...   │  │                            │
│  └───────────────┘▶ │  🟢 就绪  CABLE Input ✅  │
└──────────────────────┴──────────────────────────┘
```

## 项目结构

```
├── app.py                  # 入口
├── config.json             # 用户配置
├── config.py               # 配置加载中心
├── app/                    # UI 层 (PyQt5 + qfluentwidgets)
│   ├── main_window.py      # 主窗口，双栏布局
│   ├── chat_widget.py      # 左侧聊天面板
│   ├── message_item.py     # 聊天气泡组件
│   ├── settings_panel.py   # 右侧控制面板
│   ├── log_bridge.py       # 日志 → UI 桥接
│   └── utils/              # QConfig、主题工具
├── service/
│   └── tts_service.py      # TTS 核心编排层
├── engines/                # 5 个 TTS 引擎
│   ├── aliyun.py           # 阿里云 Qwen TTS
│   ├── edge.py             # Microsoft Edge TTS
│   ├── sapi5.py            # Windows SAPI5
│   ├── espeak.py           # eSpeak NG
│   └── piper.py            # Piper 神经网络
├── audio/
│   └── player.py           # PyAudio 播放到 VB-Cable
├── assets/
│   ├── icons/icon.svg
│   └── qss/
└── tests/                  # 86 个单元测试
```

## 打包为 exe

```bash
python build_qt.py
```

## 依赖

| 包 | 用途 |
|----|------|
| `PyQt5` | GUI 框架 |
| `qfluentwidgets` | Fluent Design 组件库 |
| `pyaudio` | 音频设备操作 |
| `pywin32` | Windows COM (SAPI5 引擎) |
| `edge-tts` | Microsoft Edge TTS |
| `dashscope` | 阿里云 TTS (可选) |

## License

MIT
