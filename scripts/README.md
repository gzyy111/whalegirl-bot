# 回归脚本

> 这些脚本是在真机上调 bug 时留下的**回归探针**，不是单元测试框架。
> 大部分需要：Windows + 微信已登录 + `.venv` 里的依赖 + 机器人配置**已经填好**
> （`config.yaml` 存在且 API Key 已通过 WebUI 保存）。
>
> 共同约定：**改完代码先跑离线脚本（不碰微信），再跑真机脚本（只在文件传输助手或测试群）**。

## 离线 / 不碰微信

| 脚本 | 用途 |
|---|---|
| `_test_divination.py` | 算命意图解析 15+ 条用例、紫微十二宫、小六壬、梅花模块用例，**不调模型** |
| `_test_joke.py` | 笑话意图 27 条用例 + 去重 + 分类 + 配图召回打分 |
| `_test_named_sticker.py` | 表情包「点名挑图」触发判定与标签硬条件 |
| `_test_repeat.py` | 连续追问的去重/间隔逻辑 |
| `_test_meme_lines.py` | 梗图台词索引回归 |
| `_test_catchup.py` | **开机补消息** 34 项：收集规则、一句化、三层去重、兜底话术、冒泡阈值、启动间隔闸门、在线已收过滤 |
| `_test_catchup_wiring.py` | **开机补消息接线** 12 项：走 `main.py` 真实 `_on_message` / `_schedule_catchup`，确认钩子接对且新消息不被吃掉 |
| `_sim_input.py` | **零输出的全链路模拟**：真实 `ChatBot.initialize()` + 真实服务，但把 `handler.wechat` 换成只记录不发送的 `RecordingBridge`，直接喂消息给 `_handle_message`，能看出命中了哪条快路径 |
| `_build_tarot_data.py` | 从 npm `@cometpisces/tarot-kit` 的原始 JSON 重建 `src/divination/tarot_data.json`（繁转简、译名对齐） |
| `_build_tarot_images.py` | 从 npm `tarot-kit-images` 的 `images` 目录重建 `塔罗牌/images/` 与 `tags.json` |

## 需要微信（真机）

| 脚本 | 用途 | 风险 |
|---|---|---|
| `_probe_uia.py` | **热激活微信 UIA**（`gate RVA ... : 0 -> 1`）。微信每次重启后要先跑它，再让 bot 连微信 | 只读 |
| `_check_group.py` | 用 `WeChatDB` 回读会话最近几条消息，确认发送真的落地 | 只读，需改 `WXID` |
| `_test_text_send.py` | 走 `WeChatBridge` 发一条文本并回读 | 会真发 |
| `_test_voice_send.py` | 直测 `VoiceSender` 发语音条 | 会真发、会录音 |
| `_test_switch_stable.py` | 双向切会话发送 + 数据库校验 | 会切你的窗口 |
| `_test_group_e2e.py` | 群里文本 + 语音端到端 | **别在活跃群用** |
| `_test_filehelper_flow.py` | 用真实桥把会话设为「文件传输助手」跑完整 handler 链路 | 只在文件助手 |
| `_test_filehelper_tarot.py` | 塔罗「文字先到、图后到」的真机验证（轮询数据库最多 150 秒） | 只在文件助手 |
| `_test_joke_live.py` | 笑话 + 配图真机验证 | 只在文件助手 |
| `_probe_send.py` | 不需要微信，直接喂消息给 handler，输出写 `_probe_send_out.txt` | 只读 |
| `_test_cover_handler.py` | 点歌真唱的烟测（不起微信/GUI） | 会跑 GPU |
| `_test_token_push.py` | 连 WebSocket 触发一次 AI 调用，校验 `token_usage` 事件与 `data/ai_usage.json` 落盘 | 耗 1 次 token |
| `_test_divination_e2e.py` | 三种术数各跑一次真实解读（打印全文） | 耗 token |

## 注意

- `_test_filehelper_flow.py` 这类脚本里**不能用 `time.sleep()`**：会阻塞事件循环，
  `asyncio.create_task` 起的发图任务根本没机会跑，看起来像「图没发出去」。
  真机验证要用 `await asyncio.sleep()` + 轮询数据库的版本。
- 脚本里的群名/会话名/`WXID` 都改成了占位值（`示例群` / `12345678@chatroom`），
  **跑之前换成你自己的**。
- 一次只跑一个真机脚本：微信只有一个前台窗口，两个脚本抢输入区会互相打断。
