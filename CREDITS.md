# 参考来源与致谢（CREDITS）

> 这份文档回答一个问题：**鲸鱼娘 bot 的每一个环节，分别参考/使用了谁的项目？**
>
> 标注方式：
> - **直接依赖** = 代码里 import 或起进程调用，仓库不包含其源码；
> - **移植/改写** = 把别处的方法落地成本项目代码（注明原项目）；
> - **只借鉴设计** = 看了思路、参数、结构，**没有复制代码**；
> - **仅调研未采用** = 评估过，最终没用，但结论影响了选型。
>
> 许可证一栏是 **2026-10-19 通过 GitHub API / PyPI / npm 实际查到的**；
> 查不到或仓库没写许可证的，标「未声明」而不是猜。

---

## 0. 一张总表

| # | 环节 | 主要参考/依赖 | 许可证 | 参考方式 |
|---|---|---|---|---|
| 1 | 项目底座 | [linxisama/deepseekgirl](https://github.com/linxisama/deepseekgirl) | **未声明** | 直接依赖（整包二次开发） |
| 2 | 微信接入 | [cluic/wxauto](https://github.com/cluic/wxauto)、wechatauto-replica、wxauto4、[Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) | Apache-2.0 / Apache-2.0 / 未声明 / Apache-2.0 | 直接依赖 |
| 3 | 对话 | DeepSeek API（OpenAI 兼容）、Anthropic 兼容格式 | 服务条款 | 直接调用 |
| 4 | 参与判定 | [feder-cr/jev](https://github.com/feder-cr/jev)（jevos）+ OpenVINO int8 模型 | MIT / Apache-2.0 | 直接依赖（本地服务） |
| 5 | 语音识别 | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) + `base` 模型 | MIT | 直接依赖 |
| 6 | 说话 | [rany2/edge-tts](https://github.com/rany2/edge-tts)、SAPI5（pyttsx3/pywin32） | LGPL-3.0（`srt_composer.py` 为 MIT） | 直接依赖 |
| 7 | 唱歌 | [ace-step/ACE-Step](https://github.com/ace-step/ACE-Step)、[Soul-AILab/SoulX-Singer](https://github.com/Soul-AILab/SoulX-Singer) | Apache-2.0 / Apache-2.0 | 直接依赖（子进程/HTTP） |
| 8 | 真唱翻唱 | SoulX-Singer **SVC** + [facebookresearch/demucs](https://github.com/facebookresearch/demucs) + [librosa](https://github.com/librosa/librosa) + RMVPE | Apache-2.0 / MIT / ISC | 直接依赖 + 自写胶水 |
| 9 | 点歌原曲 | [洛雪音乐 LX Music](https://github.com/lyswhut/lx-music-desktop) 的 Web 同步服务 | Apache-2.0 | 直接依赖（HTTP 接口） |
| 10 | 表情包 | [lmy414/ai-girl-stickers](https://github.com/lmy414/ai-girl-stickers)、[梗鲸 aigengtu.com](https://aigengtu.com/) | MIT / 未声明 | 数据来源 + 直接依赖 |
| 11 | 梗图向量召回 | [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) | MIT（模型卡） | 直接依赖 |
| 12 | 图片搜索 | 360 图片（image.so.com）公开检索接口 | 非官方接口 | 直接调用 |
| 13 | 算命排盘 | [6tail/lunar-python](https://github.com/6tail/lunar-python)、[SylarLong/iztro](https://github.com/SylarLong/iztro) / iztro-py | MIT / MIT | 直接依赖 |
| 14 | 塔罗 | [@cometpisces/tarot-kit](https://www.npmjs.com/package/@cometpisces/tarot-kit) + tarot-kit-images | MIT | 数据来源（已转简体） |
| 15 | 讲笑话 | 梗鲸 aigengtu.com 的「大肥鱼」梗库、社区名台词 | 未声明 | 内容来源 |
| 16 | 人设与行为设计 | Pallas-Bot、AstrBot、personification、mio、SillyTavern、角色卡 V2 等（详见 §4） | 多为 AGPL/GPL/MIT | **只借鉴设计** |
| 17 | 研究与规范 | 人格漂移、谄媚、指令层级等论文；Llama Guard | 论文/许可 | 论据 |
| 18 | Web / 桌面 / 基础库 | FastAPI、uvicorn、websockets、PySide6、httpx、loguru、Pillow 等 | MIT/BSD/LGPL | 直接依赖 |

---

## 1. 项目底座

| 项目 | 作者 | 许可证 | 在本项目里的角色 |
|---|---|---|---|
| **[linxisama/deepseekgirl](https://github.com/linxisama/deepseekgirl)** | linxisama（B 站「西莲寺紫不是败犬」） | **仓库未附 LICENSE** | 整个机器人本体：三端接入、`message_handler`、`ai_engine`、`web` 控制台、人设锁定、看门狗，全部来自这里 |
| 起源视频 | B 站 BV1poaS6FEyK | — | 项目获取途径 |

**说明**：本仓库是在其发布的 zip 基础上做的二次开发，**没有把上游改为自己的作品**，
也没有改动它的人设锁定机制（上游明确要求人设不可修改）。上游没有声明许可证，
所以本仓库同样**不声明任何许可证**，见 [NOTICE.md](./NOTICE.md)。

---

## 2. 微信接入与消息收发

| 项目 | 许可证 | 角色 | 备注 |
|---|---|---|---|
| **[cluic/wxauto](https://github.com/cluic/wxauto)** | Apache-2.0 | 微信 UI 自动化的基础库（发消息、取会话、图片/文件发送） | 上游 `requirements` 里的 `wxauto @ main.zip` |
| **wechatauto-replica** (PyPI) | Apache-2.0 | 微信 **4.x** 分支的自动化实现（本机主用） | `requirements.windows.txt` 固定 `1.2.2.6` |
| **wxauto4** (PyPI) | 未声明 | 微信 4.x 的另一种后端，`backend: auto` 时备用 | 实测不如 replica 稳，保留作回退 |
| **[Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)** | Apache-2.0 | UIA 树遍历、控件驱动输入（`uiautomation==2.0.29`） | 文本发送的快路径靠它 |
| **WinRT OCR（winsdk）** | MIT | 聊天区截图识别（OCR 兼容模式） | 跑在独立进程 `ocr_worker.py`，避免卡事件循环 |
| **微信本地数据库读取** | — | 发送后回读 `WeChatDB` 校验是否落地 | 自写，基于 SQLCipher 库文件 + `winsdk` 解密 |

**本项目的原创改动**（这部分不是抄的）：
- 内容区边缘锚定坐标（`VOICE_EDGE` / `SEND_EDGE` / `CANCEL_EDGE` / `EMOJI_LEFT`），
  解决「窗口 11px 内缩导致点击偏 11px」；
- 「绿色发送键 ±18px 统计绿色像素」的录音态判据；
- UIA 15 秒冷却重试 + 后台重连；热激活脚本 `scripts/_probe_uia.py`；
- 发送后回读数据库、语音 `create_time` 基准回退 5 秒的校验方式。

---

## 3. 对话大模型

| 来源 | 角色 |
|---|---|
| **DeepSeek API**（`https://api.deepseek.com/v1/chat/completions`） | 默认模型 `deepseek-chat`；OpenAI 兼容协议 |
| OpenAI Chat Completions 协议 | `ai_engine.py` 按该协议实现，可换任何兼容中转 |
| Anthropic Messages 协议 | `ai_engine.py` 同时支持该格式（`api_style: auto` 自动识别） |

Token 用量记账（`data/ai_usage.json`）与 WebSocket 实时推送是自写的。

---

## 4. 参与判定（该不该接话）

| 项目 | 许可证 | 角色 |
|---|---|---|
| **[feder-cr/jev](https://github.com/feder-cr/jev)**（jevos） | MIT | 「这句话是不是在跟我说话 / 该不该回」的本地 yes-no 判定模型，`jev.exe serve` 在 `127.0.0.1:8017`，线格式 `POST /v1/systemone` |
| **jev-os OpenVINO int8 构建** | Apache-2.0（OpenVINO）等 | 官方 TypeSafe Jev API 在本机返回 HTTP 451（地域封锁），改用这个开源等价实现 |
| jev 的第三方依赖 | MIT/Apache-2.0 | OpenVINO、oneTBB、llama.cpp、cpp-httplib、JSON for Modern C++（见其 `THIRD_PARTY_NOTICES.md`） |

**实测结论**（决定了怎么用）：`addressed` 维度可靠（点名 0.59–0.90，@别人 0.28–0.49，阈值 0.55）；
`should_reply` 在中文上方向是反的，**只做次要门控**，绝不让它单独决定回不回。
判定服务挂掉时回退到原来的概率/规则，绝不因为服务挂了就不回话。这部分经验写在
`src/jev_judge.py` 的模块注释里。

---

## 5. 语音识别（ASR）

| 项目 | 许可证 | 角色 |
|---|---|---|
| **[SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)** | MIT | 本地语音转文字，`base` 模型 int8，CPU |
| **Systran/faster-whisper-base**（HF 模型） | MIT | 模型权重，首次运行自动下载 |
| **hf-mirror.com** | 镜像 | 国内下载模型必须走镜像（`asr.hf_endpoint`） |
| **silk-python** | 见包内 LICENSE | 微信语音的 SILK 解码 |

---

## 6. 说话（TTS）

| 项目 | 许可证 | 角色 |
|---|---|---|
| **[rany2/edge-tts](https://github.com/rany2/edge-tts)** | LGPL-3.0（`src/edge_tts/srt_composer.py` 为 MIT） | 主 TTS 后端，音色 `zh-CN-XiaoxiaoNeural` |
| **Microsoft Edge 在线语音服务** | 服务条款 | 音源 |
| **SAPI5 / pyttsx3** | MPL-2.0（pyttsx3） | edge-tts 失败时的离线兜底 |
| **pywin32** | PSF-2.0 | 调 SAPI 与窗口消息 |

「念唱」的参数（`sing_pitch: +35Hz`、`sing_rate: +8%`）是自调的，属于**念**不是真唱。

---

## 7. 唱歌（端到端出歌 / 零样本歌声）

| 项目 | 作者 | 许可证 | 在本项目里的角色 |
|---|---|---|---|
| **[ace-step/ACE-Step](https://github.com/ace-step/ACE-Step)** | ACE Studio / StepFun | Apache-2.0 | 给歌词 → 直接出带编曲的成品歌（48 kHz），以 FastAPI 服务跑在 `127.0.0.1:8001`，`src/acestep_service.py` 负责探活/拉起/下载兜底 |
| **[Soul-AILab/SoulX-Singer](https://github.com/Soul-AILab/SoulX-Singer)** | Soul AI Lab | Apache-2.0 | 零样本歌声合成（SVS）与翻唱（SVC）；独立 conda 环境 `soulxsinger`（Py 3.10 + torch cu121），以子进程 `sing_once.py` 调用 |
| **SoulX-Singer 预训练模型** | 同上 | Apache-2.0 | HF 下载，国内必须 `HF_ENDPOINT=https://hf-mirror.com` |
| **RMVPE**（SoulX-Singer 仓库自带） | 随仓库 | Apache-2.0 | 提取 f0；SVC 要求 **50fps（24k/hop480）** 的 npy |
| **SoulX-Singer 公开测评集音色** | 同上 | 随仓库 | 当前真唱用的参考音色 `ZH_SPK_11_0.wav`（5.9 秒女声）——**这是借来的音色，见 README「能力边界」** |

**本项目在 SoulX-Singer 外面加的东西**（自写脚本，思路来自其 README 与 issue）：
`sing_once.py`（歌词+曲调→wav 的桥接）、`sing_lab.py`（批量对比）、`sing_verify.py`（pyin 反算基频
按音程比对）、`melody_from_audio.py`（demucs 分人声→pyin 取基频→切音符）、
`mix_song.py`（纯 DSP 混音）、`preprocess/tools/midi_parser.py`（MIDI→标注）。

**踩坑结论**（写进代码注释，供后来者）：melody 与 score 是两条独立音高通道不能混用；
`note_type=3` 是同字延音不是句末长音；唱字时值 ≥0.3 秒；`auto_shift` 默认开启会把整段旋律平移，
同调必须关掉；子进程 cwd 不同导致产物路径必须绝对。

---

## 8. 真唱翻唱（SVC）

| 项目 | 许可证 | 角色 |
|---|---|---|
| **SoulX-Singer SVC**（`model-svc.pt`，2.66 GB） | Apache-2.0 | 拿原唱人声换音色，旋律/节奏/咬字沿用原唱 |
| **[facebookresearch/demucs](https://github.com/facebookresearch/demucs)** | MIT | 分出人声/伴奏（`htdemucs`），跑在 acestep 环境 |
| **[librosa](https://github.com/librosa/librosa)** | ISC | 基频分析（yin/pyin）、`--pitch_shift` 的相位声码器移调 |
| **soundfile / numpy / scipy** | BSD / BSD / BSD | 读写与重采样 |
| **ffmpeg**（imageio-ffmpeg / 本机 ffmpeg） | LGPL/GPL | 转码、拼接、loudnorm |
| **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** 思路 | Unlicense | 抓 B 站音频时用了直链接口思路（本项目脚本直接调 B 站 player/playurl） |

**关掉 `auto_shift`、整首移调到 ~260 Hz、伴奏同步移调** 这三条是实测出来的，
不是上游文档里的用法；推导过程见 `docs/点歌真唱-接入说明.md`。

---

## 9. 点歌原曲下载

| 项目 | 许可证 | 角色 |
|---|---|---|
| **[lyswhut/lx-music-desktop](https://github.com/lyswhut/lx-music-desktop)**（洛雪音乐） | Apache-2.0 | `src/music_provider.py` 对接其 Web 同步服务的 `/api/music/auth|search|url|download` |

接口契约是自测的（lxserver v2.0.0），上游没有公开 API 文档。

---

## 10. 表情包

| 来源 | 许可证 | 角色 |
|---|---|---|
| **[lmy414/ai-girl-stickers](https://github.com/lmy414/ai-girl-stickers)** | MIT | 「蓝色大肥鱼 / AI 娘」二创表情包开放档案，按 `deepseek` 类目取图（上游 `dist/submissions/originals/` 共 811 张） |
| **[梗鲸 aigengtu.com](https://aigengtu.com/)** | 未声明 | 1400+ 张「大肥鱼」梗图 + 中英双语梗科普，`stickers.json` 当召回索引；CDN 规则 `https://img.aigengtu.com/meme/NNN.webp` |
| **[the-beating-light-of-the-nail/deepseek-chan-meme-pack](https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack)** | 未声明（87★） | 梗年表与名场面清单，用于写笑话与标签 |
| **[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)** | MIT（模型卡） | `sticker_recall.py` 的向量召回模型（CPU） |
| **Pillow / imageio-ffmpeg** | MIT-CMU / BSD | WebP→PNG/JPEG 转码（微信只认稳当格式） |

**说明**：表情包图片**没有进本仓库**（5.4 GB，且多为二创作品的再分发），
只保留标签与召回代码；需要时按上面的来源自行拉取。

---

## 11. 图片搜索

| 来源 | 角色 |
|---|---|
| **360 图片（image.so.com）** | `src/image_search.py` 的联网找图，公开检索页解析，非官方接口 |

---

## 12. 算命

### 12.1 直接采用

| 项目 | 许可证 | 角色 |
|---|---|---|
| **[6tail/lunar-python](https://github.com/6tail/lunar-python)** `1.4.8` | MIT | 农历/干支/十神/藏干/纳音/地势/大运流年/黄历/节气，纯 Python 零依赖；`src/divination/bazi.py` 的盘面全部来自它 |
| **[SylarLong/iztro](https://github.com/SylarLong/iztro)** | MIT | 紫微斗数排盘标准库（JS/TS） |
| **iztro-py** `0.5.0`（PyPI，iztro 的 Python 移植） | MIT（上游） | `src/divination/ziwei.py`；注意它的星名/宫名默认是 i18n key，必须手工映射中文名 |

### 12.2 调研并借鉴（未复制代码）

| 项目 | ★ | 借鉴了什么 |
|---|---|---|
| [china-testing/bazi](https://github.com/china-testing/bazi) | 1.5k | 断语口径、冲刑合会可视化 |
| [cantian-ai/bazi-mcp](https://github.com/cantian-ai/bazi-mcp) | 436 | 「排盘做成工具、解读留给模型」的结构 |
| [ChesterRa/mingpan](https://github.com/ChesterRa/mingpan) | 119 | 只出事实不解读的接口划分 |
| [ChiShengChen/bazaar-of-fates](https://github.com/ChiShengChen/bazaar-of-fates) | — | 多术数确定性引擎 + 解读分层的设计 |
| [jinchenma94/bazi-skill](https://github.com/jinchenma94/bazi-skill) | 3.5k | 四柱分析的**结构化 prompt** 写法 |
| [learnwithu/mingli-master](https://github.com/learnwithu/mingli-master) | 754 | 排盘 + 可视化命盘的产出方式 |
| [Sudo-Biao/Chinese-Metaphysics-Platform](https://github.com/Sudo-Biao/Chinese-Metaphysics-Platform) | — | 计算引擎与知识库分离、BM25 检索 |
| [hhszzzz/taibu](https://github.com/hhszzzz/taibu) | — | 「八字/紫微/六爻/奇门/塔罗」全家桶的接口划分 |
| [dzcmemory-web/bazi-ziwei-skills](https://github.com/dzcmemory-web/bazi-ziwei-skills) | 85 | 「算法精准排盘，不靠 LLM 猜」的分工原则 |

### 12.3 六爻（**评估后未采用**，列入下期）

| 项目 | ★ | 为什么没做 |
|---|---|---|
| [xiongdun8/liuyao](https://github.com/xiongdun8/liuyao) | 13 | 世应/六亲/用神/月建日辰是一整套体系，没有师承硬写会断错 |
| [shubhaviatiningsih-byte/fortune-liuyao-skill](https://github.com/shubhaviatiningsih-byte/fortune-liuyao-skill) | 72 | 同上 |
| [1eakkkk/liuyao-tool](https://github.com/1eakkkk/liuyao-tool) | 13 | 同上 |

### 12.4 学习材料（B 站）

- 「这可能是最不玄学的八字工具：赛博八字，盲派实战」av117460627887443 —— 「工具归工具、断命归断命」
- 「八字紫微小六壬+8 家 AI 解读，浏览器打开即用，MIT 开源」BV1R6Ex61EeH —— 模块拆分方式
- 「道士被炼化成了 Skill：AI 道士」av117319632292823 —— Skill 化封装，与「快路径 + 提示词」同源
- 「手把手教你 DeepSeek 算命提问方法」av114775602236447 —— 纯 prompt 路线的上限（用来对照）
- 「周易八字排盘工具合集」av117382496522438 —— 功能边界清单

**共识结论**：没有「训练好的算命大模型」；能用的都是「确定性排盘 + 可审计规则 + LLM 组织语言」。
本项目的 `src/divination/rules.py` 每条断语都带 `basis` 字段，就是按这个原则写的。

---

## 13. 塔罗

| 来源 | 许可证 | 角色 |
|---|---|---|
| **[npm @cometpisces/tarot-kit](https://www.npmjs.com/package/@cometpisces/tarot-kit)** `0.2.0` | MIT | 78 张 Rider-Waite 体系牌义（名/描述/核心词/正逆位/四维），繁体已用 `zhconv` 转简体，译名对齐大陆叫法 |
| **npm @cometpisces/tarot-kit-images** `0.2.0` | 代码 MIT | 78 张牌面图（宽 600 JPEG），`塔罗牌/images/` |
| **A.E. Waite《The Pictorial Key to the Tarot》(1910)** | 公有领域 | 牌义文本的原始出处 |
| **Rider-Waite-Smith 牌面 (1909)** | 公有领域 | 牌面图版权已过期；改名对应见 `_build_tarot_data.py` |
| 备选：[tarotapi.dev](https://tarotapi.dev)（`ekelen/tarot-api`） | MIT | 最终没用（npm 包更全，且本机连不上 Wikimedia） |

---

## 14. 讲笑话

| 来源 | 角色 |
|---|---|
| **梗鲸 aigengtu.com** + deepseek-chan-meme-pack | 「大肥鱼 / 蓝色大肥鱼」的梗与名台词：`真当我是便宜货啊`、`你就是一只吃白饭的鲸鱼`、`用户别走，我上错模型了`、`已思考用时3秒`、`token太多用不完啦`、`得加钱`、`DeepSleep` 等 |
| 本项目 `data/jokes.json` | 42 条（deepseek / whale / cold 三类）+ 开场与收尾池，自写 |

---

## 15. 人设与行为设计（只借鉴设计，未复制代码）

这一节是 2026-10-16「人格与行为改造」调研的产物。**没有任何一行代码来自下列项目**，
借鉴的是决策链、参数区间、提示词分层与评测方法；AGPL/GPL 项目的代码一行都没抄。

| 项目 | 许可证 | 借鉴了什么 |
|---|---|---|
| **[PallasBot/Pallas-Bot](https://github.com/PallasBot/Pallas-Bot)** ＋ [架构文档](https://pallasbot.github.io/Pallas-Bot-Docs/developer/architecture/llm-output-guardrails) | AGPL-3.0 | **与本项目问题最贴合**：决策链（PASS / 低投入出口 / QUOTE 冷却）、两层输出护栏、四态收敛、`behavior_patterns`、`reply_shape`、离线自然度评测矩阵、A/B 熔断 —— `src/output_guard.py` 的四态（allow/retry/fallback/block）直接照这个模型重写 |
| **[AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot)** ＋ [文档](https://docs.astrbot.app/dev/astrbot-config.html) | AGPL-3.0 | 拟人参数表：分段 1.5–3.5 s、rate_limit 60 s / 30 条、主动发言默认关且 10% 概率、输出侧审核 |
| **[luojisama/personification](https://github.com/luojisama/personification)** | 未声明 | 参与概率、拟人发送、人设分层、脱敏回放集、语义路由静态门禁 |
| **[HuanCheng65/mio](https://github.com/HuanCheng65/mio)** | MIT | 沉默优先、结构化动作输出、humanize 发送、四层 prompt、三层记忆 |
| **[SillyTavern/SillyTavern-Docs](https://github.com/SillyTavern/SillyTavern-Docs)** | 文档 | `post_history_instructions` 的优先级；「正向指令优于否定指令」（这条直接决定了把「严禁复读」改写成正向行为要求） |
| **[character-card-spec-v2](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)** | 规范 | 永久层 vs 可裁层的划分 |
| [docs.chub.ai](https://docs.chub.ai/docs/the-basics/character-creation) | 文档 | 角色定义字段的落地方式 |
| [wiki.pygmalion.chat（Ali:Chat）](https://wiki.pygmalion.chat/bot-creation/trappu/introduction) | 文档 | 用示例对话**强化**特质 |
| **[zhayujie/CowAgent](https://github.com/zhayujie/CowAgent)**（原 chatgpt-on-wechat） | MIT | 中文生态多端接入的对照（旧 `character_desc` 单字段人设的局限） |
| **[langbot-app/LangBot](https://github.com/langbot-app/LangBot)** | Apache-2.0 | 人设走流水线（Pipeline）的结构 |
| **[KroMiose/nonebot_plugin_naturel_gpt](https://github.com/KroMiose/nonebot_plugin_naturel_gpt)** | Apache-2.0 | 人格自定义/切换 + 聊天记忆 |
| **[Pancakes-Labs/astrbot_plugin_proactive_chat](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat)** | AGPL-3.0 | 状态驱动的主动消息（情绪 + 日程 + 免打扰时段），而不是定时发一条 |
| **[menglimi/astrbot_plugin_private_companion](https://github.com/menglimi/astrbot_plugin_private_companion)** | 未声明 | 拟人状态 / 生活日程 / 低频主动消息 |
| **[69gg/Undefined](https://github.com/69gg/Undefined)** | MIT | 认知记忆架构 |
| **[Archeb/CyberGroupmate](https://github.com/Archeb/CyberGroupmate)** | AGPL-3.0 | Code-Driven Social Agent 的组织方式 |
| **[Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot)** | GPL-3.0 | 数字生命式智能体的原则「最像而不是好」 |
| **[NapCatQQ](https://github.com/NapNeko/NapCatQQ)** / **[LLOneBot](https://github.com/LLOneBot/LLOneBot)** | 未声明 / GPL-2.0 | QQ 协议端的能力边界（`set_input_status` 之类的拟人 API 其实靠上层框架） |
| **[OpenClaw 文档](https://docs.openclaw.ai/concepts/messages)** | 文档 | 群聊行为工程规格：`humanDelay` 800–2500 ms、`NO_REPLY`、`requireMention`、ambient 模式、分块断点优先级；人格指南「Charm over cruelty / Sharp is good. Annoying is not.」 |

---

## 16. 研究与规范（论据）

| 来源 | 支撑了什么 |
|---|---|
| [arXiv:2402.10962](https://arxiv.org/abs/2402.10962)（人格漂移量化） | 8 轮内出现显著 drift → 为什么要做人设分层与回归 |
| [OpenAI：sycophancy in GPT-4o](https://openai.com/index/sycophancy-in-gpt-4o/) / [expanding on sycophancy](https://openai.com/index/expanding-on-sycophancy/) | 「无上界的镜像型风格指令」是谄媚元凶 → 风格指令要设上限 |
| [arXiv:2310.13548](https://arxiv.org/abs/2310.13548)（谄媚的系统性研究） | 同上 |
| [arXiv:2404.13208](https://arxiv.org/abs/2404.13208)（指令层级） | 群友输入应视为低优先级不可信内容 → 提示词注入防护 |
| **[meta-llama/PurpleLlama](https://github.com/meta-llama/PurpleLlama)**（Llama Guard 3） | 输出侧分类的可选方案（本项目最终用轻量正则，未接 Llama Guard） |

---

## 17. Web / 桌面 / 基础依赖

| 组件 | 许可证 | 用途 |
|---|---|---|
| FastAPI / uvicorn | MIT / BSD-3-Clause | Web 控制台与 API |
| websockets | BSD-3-Clause | WebSocket 实时推送（Token、日志、事件） |
| PySide6 | LGPL-3.0 | 桌面窗口与托盘 |
| httpx | BSD-3-Clause | 全部 HTTP 调用 |
| loguru | MIT | 日志 |
| PyYAML | MIT | 配置 |
| cryptography | Apache-2.0 / BSD-3-Clause | Fernet 加密 API Key |
| Pillow | MIT-CMU | 图片处理 |
| psutil | BSD-3-Clause | 进程/系统状态 |
| pywin32 | PSF-2.0 | 窗口、剪贴板、消息 |
| comtypes / pyperclip / PyAutoGUI | MIT / BSD / BSD | 输入与剪贴板 |
| zhconv | MIT | 繁体转简体（塔罗牌义） |
| comfy/其他未列出项 | 见各包内 LICENSE | — |

---

## 18. 语料与数据集（仅用于风格实验，未随仓库分发）

| 数据集 | 用途 |
|---|---|
| HF `Hanversion/Tieba-SomeInteresting`（mujian / renkou / ruozhiba） | 贴吧语气实验 |
| HF `roycehe/tieba` | 同上 |
| HF `silver/lccc` | 中文闲聊对照 |
| HF `LooksJuicy/ruozhiba`、`LooksJuicy/Chinese-Roleplay-SingleTurn` | 弱智吧/角色扮演语料对照 |

这些语料**只用于离线评测**，没有进仓库（体积与许可都不合适）。风格成品在 `风格语料/吐槽库.json`（174 条 / 8 场景），
是自写/自筛的。

---

## 19. 本项目的原创部分

为避免「全是抄的」误解，明确列出本仓库里**从零写**的东西：

- `src/divination/`（八字规则、64 卦、小六壬、梅花、塔罗牌阵与出图）—— 排盘用 lunar-python/iztro，**规则与提示词自写**；
- `src/cover_service.py`（曲库索引、意图解析、GPU 串行队列、缓存键）与 `cover.py` 的桥接改造；
- `src/joke_service.py`、`data/jokes.json`、表情包标签体系与 `sticker_library` 的硬条件挑选；
- `src/output_guard.py` 的具体规则表（复读、助手腔、自称模型、越界攻击）；
- `src/voice_sender.py` 的坐标锚定与录音态判据；
- `src/web/` 的 Token 实时推送、`/api/text/send`、`/api/image/send`；
- `_botctl.ps1` 一键启停（含微信 UIA 热激活）、`_probe_uia.py`；
- `docs/` 下的全部实测文档。

---

## 20. 如果漏了谁

这份清单是照「功能环节」逐个翻代码与文档整理的，但仍可能有疏漏。
如果这里漏掉了你的项目，请开 issue，我会补上——**这个仓库的每一块几乎都站在别人肩上，
署名不是客套，是基本事实。**
