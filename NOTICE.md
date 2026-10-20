# 授权状况、第三方组件与隐私说明（NOTICE）

## 1. 上游授权状况（请先读这一节）

本仓库是 **[linxisama/deepseekgirl](https://github.com/linxisama/deepseekgirl)**（B 站「西莲寺紫不是败犬」）
的二次开发版本。截至 2026-10-19 核查：

- 上游 GitHub 仓库里 **没有 `LICENSE` 文件**，也没有在 README 里声明许可证；
- 按伯尔尼公约默认规则，**未声明许可证 = 保留所有权利**，不等于可以自由再分发。

因此：

1. **本仓库不声明任何开源许可证**（不放 LICENSE 文件），不冒称 MIT / Apache / GPL；
2. 本仓库的定位是**个人学习与存档**，其中包含对本机环境适配、自写模块与实测文档；
3. **如果上游作者对公开这份二次开发存档有异议，请联系我，我会立即转为私有或删除。**
   在此提前致歉——这份存档的本意是记录踩过的坑、方便后来者，不是为了冒充原创。

如果你要基于本仓库做产品化分发，**请先取得上游作者的授权**，并逐项核对
[CREDITS.md](./CREDITS.md) 里各第三方组件的许可证。

## 2. 第三方组件与服务

| 类型 | 组件 | 许可证/条款 | 备注 |
|---|---|---|---|
| 微信自动化 | wxauto、wechatauto-replica、wxauto4、uiautomation | Apache-2.0 / Apache-2.0 / 未声明 / Apache-2.0 | 非官方接口 |
| 语音识别 | faster-whisper | MIT | 模型随 HF 分发 |
| 语音合成 | edge-tts | LGPL-3.0（`srt_composer.py` MIT） | 调用微软在线服务 |
| 参与判定 | jevos（feder-cr/jev） | MIT | 含 OpenVINO / llama.cpp / cpp-httplib / nlohmann-json |
| 唱歌 | ACE-Step、SoulX-Singer | Apache-2.0 | **模型权重另有许可条款，商用前务必核对** |
| 分轨/音频 | demucs（MIT）、librosa（ISC）、ffmpeg（LGPL/GPL，视构建） | — | ffmpeg 若是 GPL 构建，随附二进制要注意 |
| 排盘 | lunar-python、iztro / iztro-py | MIT | — |
| 塔罗 | @cometpisces/tarot-kit | MIT | 牌面 RWS 1909 公有领域 |
| 向量召回 | BAAI/bge-small-zh-v1.5 | MIT（模型卡） | — |
| 表情包 | lmy414/ai-girl-stickers（MIT）、梗鲸 aigengtu.com（未声明） | — | **本仓库不分发图片**，只留标签与代码 |
| 点歌 | 洛雪音乐 LX Music | Apache-2.0 | 音乐版权归各权利人，自行负责 |

> 上述许可证是 2026-10-19 用 GitHub API / PyPI / npm 元数据核实的。**以各项目仓库内的
> LICENSE 文件为最终依据**；若上游变更，以变更为准。

## 3. 数据与素材

- **没有进仓库**：群聊记录、`data/` 下的会话上下文、`logs/`、微信 ID、群名、API Key、
  `config.yaml`（含 Fernet 加密后的 Key）、表情包图片（5.4 GB）、曲库原曲（版权音频）、
  语料数据集、诊断脚本产出。
- **进了仓库的素材**（均可再生或公有领域）：
  - `塔罗牌/images/`：Rider-Waite-Smith 1909 牌面，**版权已过期**；取自 npm `tarot-kit-images`；
  - `塔罗牌/tags.json`、`src/divination/tarot_data.json`：由 MIT 许可的 `tarot-kit` 数据整理而来；
  - `styles/`：人设与风格文本，随上游项目一起提供；
  - `data/jokes.json`、`风格语料/`：自写内容。

## 4. 隐私与脱敏

上传前做了以下脱敏（不是「本来就没有」）：

| 项目 | 处理 |
|---|---|
| 群名 | 真实群名 → 「示例群」 |
| 群的 chatroom id | 真实 id → `12345678@chatroom` |
| 本机绝对路径 | 真实安装路径 → `D:\鲸鱼娘`；用户名 → `本人` |
| 运行期数据 | `data/*`、`logs/*` 全部 gitignore |
| API Key / 密钥 | `config.yaml` 不收；模板里只有占位符 `sk-your-api-key-here` |
| 聊天截图与 OCR 文本 | `_chat_screenshots_ocr.txt`、`_scan_screenshot_out.txt`、`_test_*_out.txt` 等全部不收 |

## 5. 免责

- 微信端依赖 **UI 自动化**，属于非官方接口，**存在账号风险**；请使用测试号、控制频率、自负后果。
- 算命 / 塔罗 / 黄历 / 八字输出**仅供娱乐**，不构成医疗、投资、法律或任何专业建议。
- 语音与歌唱功能涉及第三方模型与音色，**请勿用于伪造他人声音**或商业发行。
- 本项目按「现状」提供，不附带任何明示或暗示担保。
