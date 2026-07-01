# TTS Mic Injector — 原始行为规格说明

> 本文档记录 `code2.py`（1742 行）的全部运行期行为，作为重构的目标基准。
> 重构后代码必须通过本文档中描述的每一项行为校验。

---

## 1. 模块级配置与常量

### 1.1 路径常量

| 常量 | 值 | 用途 |
|---|---|---|
| `ESPEAK_PATH` | `"espeak-ng.exe"` | eSpeak 可执行文件名，会去 PATH 和当前目录搜索 |
| `PIPER_PATH` | `"piper.exe"` | Piper 可执行文件名 |
| `PIPER_MODEL_DIR` | `"piper_models"` | Piper .onnx 模型文件存放目录 |
| `EDGE_DEFAULT_VOICE` | `"zh-CN-YunxiNeural"` | Edge 引擎默认语音 |
| `ALIYUN_CONFIG_PATH` | 脚本同目录下的 `config.json` | 阿里云 API Key 配置文件路径 |

### 1.2 数值常量

| 常量 | 值 | 说明 |
|---|---|---|
| `SPEED_DEFAULT` | 175 | 启动时语速滑块默认值 |
| `SPEED_MIN` | 80 | 语速滑块最小（eSpeak 专用） |
| `SPEED_MAX` | 450 | 语速滑块最大（eSpeak 专用） |
| `VOLUME_MAX` | 1.0 | 音量上限系数（未实际使用） |
| `LOG_MAX_LINES` | 200 | 日志面板最多保留行数 |

### 1.3 VB-Cable 检测关键字

```
VB_CABLE_KEYWORDS = ["CABLE Input"]
```

检测逻辑：遍历所有输出设备名称，大小写不敏感地搜索关键字，第一个匹配即返回。

### 1.4 `config.json` 的读取行为

- 仅 **AliyunEngine 初始化时**读取，读取路径为 `ALIYUN_CONFIG_PATH`
- 文件中可包含的字段：
  - `api_key`（字符串）：阿里云 DashScope API 密钥
  - `model`（字符串）：模型名称，默认 `"qwen3-tts-flash-realtime"`
  - `voice`（字符串）：默认语音名，默认 `"Ethan"`
- 读取失败（文件不存在、JSON 解析异常）→ **不抛异常**，返回空字典 `{}`
- 如果文件中的 `api_key` 为空且环境变量 `DASHSCOPE_API_KEY` 也为空 → 抛出 `RuntimeError` 并提示两种配置方法

---

## 2. 依赖降级策略

### 2.1 import 防护

| 模块 | 导入失败时的行为 |
|---|---|
| `pyaudio` | 设为 `None`，入口处打印警告信息 |
| `pyttsx3` | 设为 `None`，入口处打印提示 |
| `pythoncom` | 设为 `None` |
| `asyncio` | 设为 `None` |
| `edge_tts` | 设为 `None` |
| `dashscope` + `QwenTtsRealtime` 等 | 全部设为 `None` |

### 2.2 运行时检查点

- **EspeakEngine.__init__**：不检查依赖，仅检查 espeak-ng.exe 是否存在
- **SystemTTSEngine.__init__**：检查 `pythoncom` 和 `win32com.client.Dispatch`，缺失则 `RuntimeError`
- **PiperEngine.__init__**：检查 piper.exe 存在 → 检查模型文件存在
- **EdgeEngine.__init__**：检查 `edge_tts` 和 `asyncio`，缺失则 `RuntimeError`
- **AliyunEngine.__init__**：检查 `dashscope`，检查 API Key
- **AudioPlayer**：`play()` 调用时检查 `pyaudio`
- **入口检查**：`pyaudio is None` 时打印警告横幅，`pyttsx3 is None` 时打印提示

---

## 3. 日志系统

### 3.1 Logger 配置

```python
logger = logging.getLogger("TTSMicInjector")
logger.setLevel(logging.DEBUG)  # 全局 DEBUG 级别，不过滤
```

### 3.2 TextHandler（日志 → Tkinter Text 控件）

**构造**：
- 绑定一个 `tk.Text` 或 `ScrolledText` 控件
- 格式：`"%(asctime)s %(message)s"`，时间格式 `"%H:%M:%S"`

**`emit(record)`**：
- 格式化日志消息，末尾加换行符
- 通过 `text_widget.after(0, ...)` 调度到主线程（线程安全）

**`_append(msg)`**：
1. 将 Text 控件设为 `NORMAL` 状态
2. 在末尾插入消息
3. 读取全部文本，按换行分割
4. 如果行数 > `LOG_MAX_LINES`（200），删除最前面的多余行（**注意**：删除方式是 `delete("1.0", f"{超出行数}.0")`，这会在第 N 行和第 N+1 行之间切断，可能导致第一行保留部分残留）
5. 滚动到底部（`see(tk.END)`）
6. 恢复 `DISABLED` 状态
7. 如果发生 `tk.TclError`（控件已销毁），静默吞下

### 3.3 控制台 Handler

- 额外注册一个 `StreamHandler`（输出到 stderr）
- 同样格式：`"%(asctime)s %(message)s"`, `"%H:%M:%S"`

---

## 4. TTS 引擎详细行为

### 4.1 TTSEngine 基类

- `name = "base"`（类属性，子类覆盖）
- `synthesize(text, speed, volume) → str`：子类必须实现，返回 WAV 路径
- `get_speed_range() → tuple | None`：默认 `(0.5, 2.0)`，返回 `None` 表示不支持调速
- `get_volume_supported() → bool`：默认 `True`

### 4.2 EspeakEngine（eSpeak NG）

**名称**：`"eSpeak"`

**构造器行为**：
1. 调用 `_check_exists()`
2. `_check_exists()` 的搜索逻辑：
   - 先检查 `ESPEAK_PATH`（即 `"espeak-ng.exe"`）是否在当前目录存在
   - 如果不存在，遍历 `PATH` 环境变量，拼接 `"espeak-ng.exe"` 
   - 如果还找不到，再遍历 PATH 拼接 `"espeak-ng"`（不带 .exe）
   - 搜索到的路径都存入 `search_paths`，逐个检查 `os.path.isfile`
   - 第一个存在的路径作为 `self._exe_path`
   - 全部找不到 → 抛出 `FileNotFoundError`（提示去 GitHub 下载）
3. 成功时记录 `logger.info(f"eSpeak NG 路径: {self._exe_path}")`

**`synthesize(text, speed, volume) → wav_path`**：
1. 创建临时 WAV 文件：`tempfile.mkstemp(suffix=".wav", prefix="tts_")`
2. 将 text 写入临时 TXT 文件（UTF-8 编码），**注释说这样可以避免 `--stdin` 在 Windows 上截断末尾字符**
3. 第一次尝试：`espeak-ng -v cmn -b 1 -s {int(speed)} -w {wav} -f {txt}`
   - `-v cmn`：普通话
   - `-b 1`：8-bit 输出？（应该是 16-bit）  
   - `-s`：语速，取整
4. 如果返回码非 0 → 第二次尝试：将 `-v cmn` 改为 `-v zh`（中文备选语音）
5. 如果再次失败 → 抛出 `RuntimeError`，附带 stderr 输出
6. 两次尝试的 **timeout 均为 10 秒**（`subprocess.Popen.communicate(timeout=10)`）
7. 超时 → `proc.kill()` → 抛出 `RuntimeError("eSpeak NG 合成超时")`
8. 如果 `volume < 0.99` → 调用 `_adjust_volume(wav_path, volume)` 修改 WAV 文件
9. 异常时清理 WAV 文件；**finally 中清理 TXT 文件**
10. 返回 WAV 文件路径

**`_adjust_volume(wav_path, factor)`**：
- 读取 WAV → 16-bit PCM 逐采样乘以 factor → 钳位到 [-32768, 32767] → 写回
- 失败时 `logger.warning`（不中断流程）

**`get_speed_range()`**：返回 `(80, 450)`（`SPEED_MIN, SPEED_MAX`）

### 4.3 SystemTTSEngine（Windows SAPI5）

**名称**：`"SAPI5"`

**内部常量**：
- `_SAPI_RATE_MIN = -10`, `_SAPI_RATE_MAX = 10`
- `_RATE_CENTER = 225.0`（SAPI rate=0 对应的 GUI 语速值）
- `_RATE_SCALE = 17.5`（映射斜率：`(400-50) / (10-(-10))`）

**构造器行为**：
1. 检查 `pythoncom` 可用
2. 尝试导入 `win32com.client.Dispatch`
3. 启动**后台线程**获取可用语音列表：
   - 初始化 COM（`pythoncom.CoInitialize()`）
   - 创建 `SAPI.SpVoice` 对象
   - 获取 `voice.GetVoices()`，遍历得到 `(index, description)` 列表
   - 反初始化 COM（`pythoncom.CoUninitialize()`）
   - 通过 `threading.Event` 同步等待，**timeout 10 秒**
4. 如果 COM 或 Dispatch 导入失败 → `RuntimeError`
5. 如果语音列表为空 → `RuntimeError("未找到系统语音")`
6. 成功：记录 `f"SAPI5 引擎就绪，{len(self._voices)} 个语音可用"`

**`get_voices()`**：返回 `[(vid, description), ...]`，vid 是整数索引

**`set_voice(voice_index)`**：接受整数或可转为整数的字符串，存入 `_current_voice_index`

**`synthesize(text, speed, volume) → wav_path`**：
1. 创建临时 WAV 文件
2. **语速映射**：`sapi_rate = round((speed - 225.0) / 17.5)`，钳位到 [-10, 10]
3. **音量映射**：`sapi_vol = int(volume * 100)`，范围 0~100
4. 启动**独立线程**进行合成（因为需要 COM 上下文）：
   - `pythoncom.CoInitialize()`
   - 创建 `SAPI.SpVoice`
   - 按索引设置语音：`voice.Voice = all_voices.Item(voice_index)`（仅当 index < Count）
   - 设置 Rate 和 Volume
   - 创建 `SAPI.SpFileStream`，`Open(wav_path, 3)`（3 = SSFMCreateForWrite）
   - 设置 `voice.AudioOutputStream = stream`
   - **同步调用** `voice.Speak(text)`（阻塞直到说完）
   - `stream.Close()`
   - `pythoncom.CoUninitialize()`
5. 通过 `threading.Event` 等待线程完成，**timeout 60 秒**
6. 合成失败 → 清理 WAV → 抛出异常
7. 返回 WAV 路径

**`stop()`**：空方法（pass），供 `_on_close` 调用

**`get_speed_range()`**：返回 `(50, 400)`

### 4.4 PiperEngine（Piper 本地神经网络）

**名称**：`"Piper"`

**构造器行为**：
1. `_find_exe()`：搜索 `"piper.exe"` 或 `"piper"`，先在 `PIPER_PATH` 找，再遍历 PATH
2. `_find_models()`：
   - 搜索目录：`PIPER_MODEL_DIR`（`"piper_models"`）、piper.exe 所在目录下的 `models/`、piper.exe 所在目录
   - 遍历 `.onnx` 文件，配套查找 `.json` 配置文件（`{name}.onnx.json` 或 `{name}.json`）
   - 去重（按文件名 `seen` set）
   - 将第一个模型设为默认（`self._current_model_name`）
3. 如果没有找到任何模型 → `RuntimeError`

**`get_voices()`**：返回 `[(model_name, model_name), ...]`

**`set_voice(voice_name)`**：如果 voice_name 在 `_models` 字典中，更新 `_current_model_path` 和 `_current_config_path`

**`synthesize(text, speed, volume) → wav_path`**：
1. 创建临时 WAV 文件
2. **length_scale 计算**：`100.0 / max(speed, 1.0)`，钳位到 [0.2, 5.0]
3. 命令：`piper.exe --model {model} --output_file {wav} --length_scale {scale}`，可选 `--config`
4. 通过 **stdin** 传入文本（`input=text.encode("utf-8")`）
5. `subprocess.run` 同步执行，**timeout 60 秒**
6. 返回码非 0 → `RuntimeError`，附带 stderr
7. 如果 `volume < 0.99` → `_adjust_volume()`（和 eSpeak 相同的实现，但写在 Piper 类里）
8. 异常时清理 WAV

**`get_speed_range()`**：返回 `(50, 200)`

### 4.5 EdgeEngine（Microsoft Edge 云端 TTS）

**名称**：`"Edge"`

**离线兜底语音列表**：13 个预定义语音（zh-CN/zh-TW/zh-HK/en-US），如果无法联网获取则使用此列表

**构造器行为**：
1. 检查 `edge_tts` 和 `asyncio` 可用
2. 将离线语音列表设为初始 `_voices`
3. 启动**后台线程**异步获取在线语音列表：
   - 创建新 event loop → `run_until_complete(_list_voices_async())` → 关闭 loop
   - `_list_voices_async()` 调用 `edge_tts.list_voices()`，提取 ShortName/Locale/Gender
   - 如果在线获取成功 → 替换 `_voices`
   - 如果失败 → 保留离线列表
   - 无论成功失败都 `_voices_ready.set()`
4. 记录日志："Edge TTS 就绪（离线 {len} 个语音，正在后台获取在线列表...）"

**`get_locales()`**：返回去重后的语言区域列表，**强制 zh-CN 和 en-US 排在最前面**

**`get_voices_for_locale(locale)`**：过滤 `_voices` 中匹配 locale 的项

**`set_pitch(pitch_hz)`**：存储到 `self._pitch_hz`

**`get_pitch_range()`**：返回 `(-50, 50)`

**`synthesize(text, speed, volume) → wav_path`**：
1. **语速映射**：`rate_pct = int(speed - 100)`，格式化为 `"+N%"` 或 `"-N%"`
2. **音量映射**：`vol_pct = int((volume - 0.5) * 200)`，格式化为 `"+N%"` 或 `"-N%"`
3. **音调映射**：`pitch_str = f"{int(self._pitch_hz):+d}Hz"`（在调用前由 UI 层设置好）
4. 创建临时 MP3 文件和 WAV 文件
5. 创建新 event loop → `run_until_complete(_async_synthesize(...))`
6. `_async_synthesize` 使用 `edge_tts.Communicate(text, voice, rate, volume, pitch)` → `await communicate.save(output_path)`
7. 调用 **ffmpeg** 将 MP3 转为 WAV：`ffmpeg -y -i {mp3} -acodec pcm_s16le -ar 24000 -ac 1 {wav}`
   - 24000Hz 采样率、单声道、16-bit PCM
8. 如果 ffmpeg 未找到 → `RuntimeError` 提示安装
9. 返回 WAV 路径
10. 异常时清理 WAV，**finally 中清理 MP3**

**`get_speed_range()`**：返回 `(50, 200)`

**重要**：EdgeEngine 不执行音量调整——`volume` 参数被映射为 edge-tts 的 volume 参数（在合成时由 edge-tts 服务端处理），而非在 PCM 层面做后处理。但代码中的 `vol_pct` 映射公式 `int((volume - 0.5) * 200)` 存在疑问：volume 传入时已经是 0.0~1.0 的范围（来自 `_vol_var.get() / 100.0`），所以实际传给 edge-tts 的 volume 是 `(volume_ratio - 0.5) * 200`，相当于 `(0~1 - 0.5) * 200 = -100~+100`。

### 4.6 AliyunEngine（阿里云 Qwen TTS）

**名称**：`"Aliyun"`

**内置 47 个语音**，硬编码在 `VOICES` 类属性中

**构造器行为**：
1. 检查 `dashscope` 已导入
2. 确定 API Key 优先级：**环境变量 `DASHSCOPE_API_KEY` 先读，config.json 的 `api_key` 覆盖之**
3. API Key 为空 → `RuntimeError` 提示两种配置方法
4. 设置 `dashscope.api_key`
5. 读取 config.json 的 `model`（默认 `"qwen3-tts-flash-realtime"`）和 `voice`（默认 `"Ethan"`）
6. 记录日志

**`_load_config()`**：
- 检查 `ALIYUN_CONFIG_PATH` 是否存在
- 用 UTF-8 打开并 `json.load`
- 任何异常 → `logger.error` → 返回 `{}`

**`synthesize(text, speed, volume) → wav_path`**：
1. 创建临时 PCM 和 WAV 文件
2. 创建 `_AliyunCallback(pcm_path)` 回调
3. 创建 `QwenTtsRealtime(model, callback)`
4. `rt.connect()`
5. `rt.update_session(voice=..., response_format=PCM_24000HZ_MONO_16BIT, mode="server_commit")`
6. `rt.append_text(text)` → `rt.finish()`
7. `callback.wait_for_finished(timeout=120)` 等待 120 秒
8. 读取 PCM 数据
9. 如果 `volume < 0.99` → `_adjust_pcm_volume()`
10. 将 PCM 包装成 WAV（24000Hz, mono, 16-bit）
11. 返回 WAV 路径
12. 异常时清理 WAV，**finally 中清理 PCM**

**`_AliyunCallback`**：
- `on_open()`：记录 debug 日志
- `on_event(response)`：处理 `response.audio.delta`（base64 解码写入文件）、`response.done`（记录 ID）、`session.finished`（关闭文件 + set Event）
- `on_close()`：确保文件关闭 + Event 设置
- `wait_for_finished(timeout=120)`：等待 Event

**`get_speed_range()`**：返回 `None`（阿里云不支持语速调节）

**`_adjust_pcm_volume()`**：原始 PCM 逐采样乘以 factor，钳位到 [-32768, 32767]

---

## 5. AudioPlayer（音频播放器）

### 5.1 构造器

- `__init__(vb_device_index=None)`：存储 VB-Cable 设备索引，其余成员初始化为 None/False

### 5.2 `find_vb_cable()` 静态方法

1. 检查 pyaudio 可用
2. 获取 PyAudio 实例
3. 遍历所有设备，跳过 `maxOutputChannels == 0` 的设备
4. 对每个输出设备，检查名称是否**大小写不敏感**地包含 `VB_CABLE_KEYWORDS` 中的任一关键字（默认 `"CABLE Input"`）
5. 找到 → 记录日志 → 返回设备索引
6. 未找到 → `RuntimeError`
7. finally 中 `p.terminate()`

### 5.3 `list_output_devices()` 静态方法

1. 检查 pyaudio 可用
2. 返回所有 `maxOutputChannels > 0` 的设备 `[(index, name), ...]`
3. finally 中 `p.terminate()`

### 5.4 `play()` — 核心播放逻辑

**参数**：
- `wav_path`：WAV 文件路径
- `stop_event`：`threading.Event`，外部设信号即可中断播放
- `monitor`：bool，是否启用监听（同时输出到扬声器）
- `monitor_device_index`：监听设备索引，None=默认
- `volume_getter`：无参 callable，返回 0.0~1.0 音量系数，用于实时音量调节

**流程**：
1. 检查 pyaudio 可用
2. 用 `wave.open` 打开 WAV
3. 获取 PyAudio 实例
4. 如果 `_vb_device_index` 为 None，调用 `find_vb_cable()` 获取
5. **声道兼容性检查**：
   - 先用原始声道数打开 VB-Cable 流
   - 如果 `OSError`（设备不支持多声道）且原始声道数 > 1：
     - 记录日志
     - 改用 1 声道重新打开 VB-Cable 流
     - 设置 `need_downmix = True`
   - 如果是单声道 OSError → 直接 raise
6. **监听流**（可选）：
   - 如果 `monitor=True`，用相同参数打开监听设备流
   - 监听流打开失败 → `logger.warning`（不影响主流程），设为 None
7. **逐帧播放**（chunk=1024）：
   - 循环读取 WAV chunk
   - 每帧检查 `stop_event.is_set()`
   - 如果 `volume_getter` 存在且返回值 `< 0.99` → `_adjust_chunk_volume()`
   - 如果 `need_downmix` → `_downmix()`
   - 写入 VB-Cable 流
   - 如果监听流存在 → 写入监听流（异常则静默跳过）
8. 返回 `True`（播放完成）或 `False`（被中断）
9. finally 中调用 `_cleanup()`

### 5.5 `_downmix(data, sampwidth, nchannels)`

- **2 声道 16-bit**：逐对采样求平均值
- **其他情况**：逐采样点累加所有声道后除以声道数
- 返回单声道 PCM 数据

### 5.6 `_adjust_chunk_volume(data, sampwidth, factor)`

- 仅支持 16-bit（`sampwidth == 2`），否则原样返回
- 逐采样乘以 factor，钳位到 [-32768, 32767]

### 5.7 `_cleanup()`

- 设置 `_playing = False`
- 停止并关闭 `_stream` → 捕获所有异常（静默）
- 停止并关闭 `_monitor_stream` → 捕获所有异常（静默）
- `_pyaudio.terminate()` → 捕获所有异常（静默）
- 全部设为 None

### 5.8 `stop()`

- 直接调用 `_cleanup()`

---

## 6. UI 控件树与布局

### 6.1 窗口属性

- 标题：`"TTS Mic Injector — eSpeak NG / SAPI5"`（注意：标题硬编码，不会随引擎切换变化）
- 大小：`720x620`
- 最小尺寸：`600x520`

### 6.2 控件层级（从上到下 pack 顺序）

```
main_frame (padding=8)
├── hist_frame          "历史记录"        fill=X, pady=(0,6)
│   ├── hist_container                    fill=X
│   │   ├── hist_scrollbar                RIGHT, fill=Y
│   │   └── _hist_listbox (height=5)      LEFT, fill=X+expand
│   └── btn_frame                         fill=X, pady=(2,0)
│       └── "清空" Button                 LEFT
├── input_frame         "输入文字..."     fill=X, pady=(0,6)
│   └── _input_text (height=3)            fill=X
├── ctrl_frame                            fill=X, pady=(0,6)
│   ├── "▶  播放" Button                  LEFT, padx=(0,12)
│   ├── "■  停止" Button                  LEFT, padx=(0,12)
│   ├── "语速:" Label                     LEFT
│   ├── _speed_scale (length=180)         LEFT, padx=4
│   ├── _speed_label                      LEFT, padx=(0,12)
│   ├── "音量:" Label                     LEFT
│   ├── _vol_scale (length=100)           LEFT, padx=4
│   └── _vol_label                        LEFT
├── engine_frame        "TTS 引擎..."     fill=X, pady=(0,6)
│   ├── 5 个引擎 Button                    LEFT, padx=3
│   └── _engine_label "当前: eSpeak"      RIGHT, padx=6
├── [_voice_frame 默认 pack_forget()]
│   ├── [_edge_locale_combo 默认隐藏]      fill=X, padx=2, pady=(4,0)
│   └── _voice_combo                      fill=X, padx=2, pady=2
├── [_pitch_frame 默认 pack_forget()]
│   ├── "音调:" Label                     LEFT
│   ├── _pitch_scale (length=250)         LEFT, padx=4
│   └── _pitch_label                      LEFT
├── _bottom_frame                         fill=X, pady=(0,6)
│   ├── _monitor_cb "监听"                LEFT
│   ├── [_monitor_combo 默认隐藏]          LEFT, padx=(4,12)
│   ├── _status_label "🟢 就绪"           RIGHT
│   └── _mic_label "🎤 未检测"            RIGHT, padx=(0,12)
└── log_frame          "日志"             fill=BOTH, expand
    └── _log_text (height=8, DISABLED)    fill=BOTH, expand
```

### 6.3 初始可见性

| 控件 | 启动时状态 |
|---|---|
| `_voice_frame` | **隐藏**（pack_forget） |
| `_edge_locale_combo` | **隐藏** |
| `_pitch_frame` | **隐藏** |
| `_monitor_combo` | **隐藏** |
| `_input_text` | **获得焦点** |

### 6.4 引擎切换时的 UI 可见性变化

| 引擎 | voice_frame | pitch_frame | edge_locale_combo | voice_frame 标签 | speed_scale |
|---|---|---|---|---|---|
| eSpeak | hide | hide | hide | — | NORMAL, 范围 `(80,450)` |
| SAPI5 | show, `before=bottom_frame` | hide | hide | "系统语音选择" | NORMAL, 范围 `(50,400)` |
| Piper | show, `before=bottom_frame` | hide | hide | "Piper 模型选择" | NORMAL, 范围 `(50,200)` |
| Edge | show, `before=bottom_frame` | show, `before=bottom_frame` | show, `before=voice_combo` | "Edge 语音选择" | NORMAL, 范围 `(50,200)` |
| Aliyun | show, `before=bottom_frame` | hide | hide | "Aliyun 语音选择" | **DISABLED** |

### 6.5 默认参数值

| 控件 | 初始值 |
|---|---|
| `_speed_var` | `SPEED_DEFAULT` = 175 |
| `_vol_var` | 100（百分比） |
| `_pitch_var` | 0 |
| `_monitor_enabled` | `True` |
| `_monitor_device_var` | 第一个不含 "CABLE" 的设备的名称 |
| `_voice_var` | `""`（空字符串，由引擎切换时填充） |

---

## 7. 事件流与状态机

### 7.1 启动流程

```
TTSMicInjectorApp.__init__()
  ├─ root = tk.Tk()
  ├─ 初始化状态变量（_stop_event, _is_playing=False, _playback_gen=0 ...）
  ├─ _init_engine() → EspeakEngine() (可能失败 → self.engine = None)
  ├─ _build_ui() → 构建所有控件 → 注册 TextHandler + StreamHandler
  ├─ root.bind("<Escape>", _on_esc)
  ├─ root.protocol("WM_DELETE_WINDOW", _on_close)
  ├─ root.after(200, _populate_monitor_combo)    # 延时填充监听设备
  ├─ root.after(300, _check_vb_cable)             # 延时检测 VB-Cable
  └─ logger.info("应用已启动")
```

### 7.2 播放请求的完整调用链

```
触发方式:
  Enter 键  → _on_enter(event)
  Ctrl+Enter → _on_ctrl_enter(event)
  ▶ 播放按钮 → _on_play()
  历史记录单击 → _on_history_click(event)
  
_on_enter / _on_ctrl_enter / _on_play:
  ├─ 从 _input_text 取文字（.strip()）
  ├─ 空文字 → return "break" 或空返回
  ├─ _add_history(text) → 插入 Listbox + see(END)
  ├─ _input_text.delete("1.0", tk.END)  # 清空输入框
  └─ _do_speak(text=text, save_to_disk=Ctrl+Enter时True)

_do_speak(text, save_to_disk):
  ├─ text 为 None → 从 _input_text 重新读取
  ├─ 生成 save_path（如果 save_to_disk=True）
  └─ _speak(text, save_path)

_on_history_click:
  ├─ 获取选中项文本
  └─ 直接调用 _speak(text)  # 不经过 _do_speak，不加入历史

_speak(text, save_path):
  ├─ engine is None → log error + return
  ├─ _is_playing == True → _stop_playback()  # 先中断上一个
  ├─ _stop_event.clear()
  ├─ _is_playing = True
  ├─ _playback_gen += 1  (gen 用于防竞态)
  ├─ UI: status → "🔊 合成中..." orange
  ├─ 创建 AudioPlayer(vb_device_index)
  └─ 开 daemon thread → _speak_worker(text, player, monitor_idx, gen, save_path)
```

### 7.3 `_speak_worker` 的后台线程行为

```
1. 主线程读取 speed, volume, pitch（通过 _speed_var.get(), _vol_var.get()）
   - 注意：这些值是在线程启动后立即读取的，而非在合成或播放过程中动态读取
   - 但 volume 在播放过程中通过 volume_getter 实时获取（见步骤 5）
2. 如果 engine 是 EdgeEngine → engine.set_pitch(pitch)
3. logger.info("合成: ...")
4. wav_path = engine.synthesize(text, speed, volume)  # 可能耗时
5. 检查 _stop_event.is_set() 或 gen != _playback_gen → return（被新请求覆盖）
6. 如果 save_path 存在 → shutil.copy2(wav, save_path)
7. logger.info("合成完成，播放中...")
8. 如果 gen == _playback_gen → UI 更新: status → "🔊 播放中..." green
9. player.play(wav_path, stop_event, monitor, monitor_device_index,
              volume_getter=lambda: self._vol_var.get() / 100.0)
   - volume_getter 传入的是 lambda，每次 chunk 播放时实时读取当前音量滑块值
10. 如果 gen == _playback_gen:
    - completed=True → "播放完成"
    - completed=False → "播放被中断"
    - root.after(0, _set_idle)
11. 异常: logger.error → UI 状态 "❌ 错误: ..." red → _set_idle
12. finally:
    - 如果 gen == _playback_gen → _is_playing = False
    - player.stop()  # 清理该 player 的 PyAudio 资源
    - 删除临时 WAV 文件
    - _current_wav = None
```

### 7.4 停止流程

```
_on_stop() / _on_esc():
  └─ _stop_playback()
       ├─ _stop_event.set()        # 信号给 worker 线程
       ├─ _is_playing = False
       └─ UI: status → "🟢 就绪" green
```

**关键细节**：`_stop_playback()` **不会** kill 线程，只是设置 Event。worker 线程会在下一个 chunk 循环时检测到并自行退出。

### 7.5 `_playback_gen` 防竞态机制

- 每次新的 `_speak()` 调用都会 `_playback_gen += 1`
- worker 线程持有自己的 `gen`（捕获时的快照）
- worker 在合成后、播放中、播放后三个检查点都判断 `gen == self._playback_gen`
- 如果 gen 已过期（新请求覆盖），worker 的 UI 更新被跳过，但**旧线程本身不会被主动杀死**——它会继续播放直到 stop_event 触发或播放完毕再退出

### 7.6 Edge 语音在线刷新的轮询机制

- EdgeEngine 构造时启动后台线程获取在线列表
- `_populate_edge_locales` 检测 `engine.voices_ready` 状态
- 如果未就绪，启动 `root.after(500, _refresh_edge_voices)`
- `_refresh_edge_voices` 中：
  - 如果 engine 已不是当前 Edge 引擎 → 直接 return
  - 如果 voices_ready → 刷新 UI 下拉列表，**尝试恢复旧选择**
  - 如果仍 not ready → 再次 `root.after(500, ...)` 递归轮询
- 旧选择恢复逻辑：
  - 保存旧的 locale 和 voice 名称
  - 如果旧 locale 仍存在于新列表 → 恢复
  - 如果旧 voice 名称仍在 voice_combo 中 → 恢复并 set_voice
  - 否则 fallback 到 "zh-CN" 或第一个

### 7.7 VB-Cable 检测

- 启动后 300ms 异步执行
- pyaudio 未安装 → 显示 "🎤 pyaudio 未安装" orange
- 找到 VB-Cable → 存储索引 → 显示 "🎤 CABLE Input ✅" green
- 找不到 → 显示 "🎤 未检测到" red

---

## 8. 引擎切换完整行为

### 8.1 `_switch_engine(name)` 通用逻辑

1. 保存 `old_engine = self.engine`
2. 尝试创建新引擎，如果失败（FileNotFoundError / RuntimeError）→ `logger.error` + 保留旧引擎 + return
3. 更新 `self.engine = new_engine`
4. 更新 `_engine_label` 文本和颜色
5. 调整语音/音调面板可见性（见 6.4 表）
6. 调整语速滑块范围和当前值
7. 记录日志 `f"切换到引擎: {name}"`
8. **如果旧引擎是 SystemTTSEngine → 调用 `old_engine.stop()`**

### 8.2 Aliyun 引擎特殊处理

- 语速滑块设为 `DISABLED` 状态
- 语速标签显示 `"N/A"`
- 不调用 `_update_speed_range()`（因为 `get_speed_range()` 返回 None）

### 8.3 引擎按钮的 lambda 闭包

```python
command=lambda n=name: self._switch_engine(n)
```
`n=name` 是默认参数捕获技巧，避免 for 循环中的闭包延迟绑定问题。

---

## 9. 边缘行为与特殊处理

### 9.1 窗口关闭

```
_on_close():
  ├─ _stop_playback()   # 设 stop_event
  ├─ 如果 engine 是 SystemTTSEngine → engine.stop()  # 空方法
  └─ root.destroy()
```

### 9.2 ESC 键

- 绑定在 root 窗口（全局）
- 调用 `_on_stop()` → `_stop_playback()`
- 注意：返回值为 `None`（不像 `_on_enter` 返回 `"break"`）

### 9.3 Enter 键的 "break" 返回值

- `_on_enter` 和 `_on_ctrl_enter` 返回 `"break"`
- 这阻止了 Tkinter Text 控件插入换行符的默认行为
- 因此**没有 Shift+Enter 换行**——输入框只能输入单行内容（尽管 height=3 支持显示多行，但实际无法通过键盘插入换行）

### 9.4 `_make_save_path` 文件名生成规则

1. 取时间戳：`YYYYMMDD_HHMMSS`
2. 清理文字中的非法文件名字符（`\ / : * ? " < > |` 和换行/制表符）→ 替换为空
3. 空白符合并为一个空格
4. 取前 10 个字符
5. 如果为空 → 使用 `"audio"`
6. 保存到当前工作目录（`os.getcwd()`）
7. 格式：`{时间戳}_{文字前10字}.wav`

### 9.5 监听设备默认选择

`_populate_monitor_combo()` 中：
- 遍历设备列表，选第一个名称中**不包含 "CABLE"（大小写不敏感）** 的设备
- 如果所有设备都包含 CABLE（或设备列表为空）→ 选第一个

### 9.6 语速滑块切换时的中值设置

`_update_speed_range(range_tuple)` 中：
- 将滑块设为新范围
- 将当前值设为**范围的中点**（`(lo + hi) // 2`），**覆盖用户之前设置的值**
- 标签同步更新为中点值

### 9.7 Edge 音调滑块

- 范围 -50 ~ +50 Hz
- `_on_pitch_change(val)` 实时更新标签和引擎
- 切换到 Edge 时 `_pitch_var` **重置为 0**，标签重置为 `"0Hz"`

### 9.8 临时文件前缀

| 引擎 | WAV 前缀 | 额外文件 |
|---|---|---|
| eSpeak | `tts_` | `tts_*.txt` |
| SAPI5 | `tts_system_` | — |
| Piper | `tts_piper_` | — |
| Edge | `tts_edge_` | `tts_edge_*.mp3` |
| Aliyun | `tts_aliyun_` | `tts_aliyun_*.pcm` |

所有临时文件在 `synthesize()` 完成后由 worker 的 finally 清理（或异常时由引擎自身清理）。

### 9.9 连续快速发送的行为

1. 第一次 Enter → `_speak("hello")`, gen=1, 开线程 A
2. 第二次 Enter（线程 A 还在合成）→ `_speak("world")`
   - `_is_playing=True` → 先调 `_stop_playback()`，设 stop_event
   - gen=2, 开线程 B
3. 线程 A 检测到 stop_event 或 gen 不匹配 → return
4. 线程 B 正常播放

结果：上一个播放被中断，新播放立即开始。

### 9.10 从历史记录播放

- `_on_history_click` 直接调用 `_speak(text)`
- **不**经过 `_do_speak`，因此：
  - 不会重复加入历史记录
  - 不会清空输入框
  - 不支持 Ctrl+Enter 保存到磁盘
- 如果正在播放其他内容，会先停止再播放

### 9.11 引擎未就绪时

- `self.engine is None` 时调用 `_speak()` → 仅记录 `logger.error("引擎未就绪，无法合成")`，**不抛异常**

### 9.12 音量调整阈值

- 所有引擎在 `volume < 0.99` 时才执行音量调整
- `_adjust_chunk_volume` 同样 `vol < 0.99` 时才处理
- 为什么是 0.99 而不是 1.0？可能是浮点精度容差

---

## 10. 线程模型

### 10.1 线程概览

| 线程 | 创建者 | 生命周期 | daemon |
|---|---|---|---|
| 主线程 | Python 解释器 | 持续到 mainloop 退出 | 否 |
| GUI 事件循环 | Tkinter | 随 root.mainloop() | — |
| SAPI5 语音获取 | SystemTTSEngine.__init__ | 获取完成后退出 | 是 |
| SAPI5 合成 | synthesize() 每次调用 | 60s 超时或完成 | 是 |
| Edge 在线语音获取 | EdgeEngine.__init__ | 获取完成后退出 | 是 |
| TTS worker (播放) | _speak() 每次调用 | 播放完成或中断后退出 | 是 |

### 10.2 线程安全措施

- **UI 更新**：worker 线程通过 `self.root.after(0, callback)` 将所有 UI 更新调度到主线程
- **停止信号**：使用 `threading.Event` 在线程间传递停止请求
- **竞态保护**：使用 `_playback_gen` 整数计数器，worker 持有快照进行比较
- **PyAudio 隔离**：每个播放请求创建独立 `AudioPlayer` 实例
- **COM 隔离**：SAPI5 在每个线程内独立初始化/反初始化 COM

---

## 11. 入口点行为

```python
if __name__ == "__main__":
    1. 检查 Python >= 3.8，否则 print + sys.exit(1)
    2. 如果 pyaudio is None → 打印安装提示（==== 边框）
    3. 如果 pyttsx3 is None → 打印提示
    4. app = TTSMicInjectorApp()
    5. app.run()
```

注意：入口点的 print 不会出现在 GUI 中，仅输出到控制台。

---

## 附录 A：各引擎 synthesize 的超时/限制汇总

| 引擎 | 超时 | 输出格式 | 采样率 |
|---|---|---|---|
| eSpeak | 10s (subprocess) | WAV | 由 espeak 决定 |
| SAPI5 | 60s (thread join) | WAV | 由 SAPI 决定 |
| Piper | 60s (subprocess) | WAV | 由 piper 决定 |
| Edge | 无显式超时 | MP3→WAV | 24000Hz mono 16bit |
| Aliyun | 120s (callback.wait) | PCM→WAV | 24000Hz mono 16bit |

## 附录 B：语速参数语义

| 引擎 | speed 输入含义 | 映射目标 |
|---|---|---|
| eSpeak | 直接作为 espeak 的 `-s` 参数 | 80~450 |
| SAPI5 | GUI 滑块值 → `(speed - 225) / 17.5` → SAPI Rate | -10~+10 |
| Piper | `100.0 / speed` → length_scale | 0.2~5.0 |
| Edge | `speed - 100` → 百分比字符串 | "+N%" / "-N%" |
| Aliyun | **不使用**（speed 参数被忽略） | — |

## 附录 C：音量参数语义

| 引擎 | volume 输入含义 | 处理方式 |
|---|---|---|
| eSpeak | 0.0~1.0 | PCM 后处理（<0.99 时） |
| SAPI5 | 0.0~1.0 → `int(volume * 100)` | SAPI Volume 0~100 |
| Piper | 0.0~1.0 | PCM 后处理（<0.99 时） |
| Edge | 0.0~1.0 → `(volume-0.5)*200`% | edge-tts volume 参数 |
| Aliyun | 0.0~1.0 | PCM 后处理（<0.99 时） |
| AudioPlayer | 0.0~1.0（实时读取） | PCM chunk 后处理（<0.99 时） |
