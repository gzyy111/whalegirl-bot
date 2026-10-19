# -*- coding: utf-8 -*-
"""输出侧护栏：在消息发进群之前做最后一道机械检查。

背景（2026-10-16 改造）
----------------------
2026-10-15 的实测发现，群里最容易"露馅"的几类输出**不是模型能力问题，而是结构性漏出**：

* 「这话你都说第三遍了，是词库卡住了还是真拿不出别的？」
  —— 提示词里"严禁复读"这条**规则被模型复述了出来**；
* 助手腔收尾（"还有什么可以帮你的"）；
* 自称模型 / 声称是程序；
* 少量越界的人身攻击。

这些东西**靠 prompt 自律是堵不住的**（SillyTavern 官方文档也指出：模型更容易遵守
"该做什么"而不是"不该做什么"）。参考 Pallas-Bot 的做法，这里加一层
**不依赖大模型的正则/词表护栏**，并采用它的四态收敛：

    allow     全部通过 → 正常发送
    retry     命中"可重生成"类规则 → 调用方可以重生成一次
    fallback  命中"不可发"类规则 → 换成一句中性的安全回复
    silent    没有可用输出 → 直接不发（**不回落成一句废话**）

设计原则（照抄 Pallas-Bot 的表述）：
    「宁可少说、不说，也不把崩坏输出发进群。」

依赖：仅标准库 re / dataclasses，可离线单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 规则表
# ---------------------------------------------------------------------------

# 规则外泄 / meta 吐槽：机器人在"评论对方说了几遍、有没有新意"。
# 这是人机感最强的信号——真人在群里不会统计别人说过几次。
META_TALK_PATTERNS = (
    r"第\s*[一二三四五六七八九十两\d]+\s*(遍|次)",
    r"说(过|了|完)?\s*(第)?[一二三四五六七八九十两\d]+\s*(遍|次)",
    r"问(过|了)?\s*(第)?[一二三四五六七八九十两\d]+\s*(遍|次)",
    r"(你|你这|您这)(句|话)?[^\n]{0,8}(说|问)(过|了)[^\n]{0,6}(好几|几|\d|多)",
    r"没(点|啥|什么|什么好)\s*新意",
    r"(是|是不是)(词库|语料|复读机)[^\n]{0,4}(卡|没词)",
    r"是不是没词了",
    r"换(点|个|句)新(词|的|话)",
    r"(你|您)(是|是不是)(没|想不出)(话说|词)",
    r"复读(机|了|吧)",
)

# 助手腔 / 模板腔收尾：不是"群友"会说的话。
ASSISTANT_TONE_PATTERNS = (
    r"有(什么|啥)(可以|能)帮(你|您|到)",
    r"还有(什么|啥)(需要|可以|能)帮",
    r"随时(告诉我|找我|联系我)",
    r"希望(能)?对(你|您|你有|您有)(有所)?帮助",
    r"作为(一个|一名)?\s*(AI|人工智能|语言模型|助手|智能助手)",
    r"我(是|只是)(一个|一名)?\s*(AI|人工智能|语言模型|助手|智能助手|程序|机器人)",
    r"我(无法|不能|没办法)(帮|为)(你|您)",
    r"很抱歉[，,]?\s*我",
    r"感谢(你|您)的(理解|支持|反馈)",
)

# 舞台指示：整条只是 (动作) 或 *动作*，没有正文。
STAGE_DIRECTION_PATTERNS = (
    r"^\s*[（(【\[][^)）】\]]{0,24}[)）】\]]\s*$",
    r"^\s*\*[^*\n]{2,24}\*\s*$",
)

# 越界内容：真实人身攻击（家人 / 脏字 / 地域 / 性别 / 职业羞辱）。
# 注意：**故意不拦「胖 / 圆 / 肥」**——对鲸鱼娘来说这是人设的一部分（抗压脂肪层），
# 属于正常玩梗。这里只拦真正越界的那一类。
HARD_RISK_PATTERNS = (
    r"你(他)?妈",
    r"妈(的|逼|了个)",
    r"尼玛|泥马",
    r"傻[逼bB8]|煞笔|沙比|傻叉",
    r"滚(出去|远点|你)",
    r"去死|死(全家|妈|爹)",
    r"(妓女|婊子|贱人|骚货|荡妇)",
    r"(河南|东北|上海|北京|广东|新疆)人(都|就是|没一个)",
    r"(女人|男人)(都|就是)(贱|蠢|没一个)",
    r"你(这个|个)?(死|臭)?(废物|垃圾|蠢货|弱智|智障)(东西|玩意)?$",
)

_COMPILED: dict[str, tuple[re.Pattern, ...]] = {
    "meta_talk": tuple(re.compile(p) for p in META_TALK_PATTERNS),
    "assistant_tone": tuple(re.compile(p) for p in ASSISTANT_TONE_PATTERNS),
    "stage_direction": tuple(re.compile(p) for p in STAGE_DIRECTION_PATTERNS),
    "hard_risk": tuple(re.compile(p) for p in HARD_RISK_PATTERNS),
}

# 兜底文案：命中时用它替换（也用于"可重生成"失败后的降级）。
# 刻意写得短、平、像群里随口一句，不带任何助手腔和道歉腔。
DEFAULT_FALLBACKS = (
    "刚那句不算，重来。",
    "这条我接不住，跳过。",
    "算了，不说了。",
)

# 表现护栏：单条过长就在干净断点上收住。
MAX_REPLY_CHARS = 90
_SENTENCE_END = re.compile(r"[。！？!?~～…\n]")


@dataclass
class Verdict:
    """护栏判定结果。"""

    action: str  # allow / retry / fallback / silent
    reason: str = ""
    matched: str = ""
    text: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.action == "allow"

    @property
    def sends(self) -> bool:
        """是否会真的发出去。"""
        return self.action in {"allow", "fallback"}


def _first_match(category: str, text: str) -> str:
    for pattern in _COMPILED.get(category, ()):
        m = pattern.search(text)
        if m:
            return m.group(0)
    return ""


def trim_long(text: str, limit: int = MAX_REPLY_CHARS) -> tuple[str, bool]:
    """表现护栏：把过长的回复在最近的句末标点处收住。

    返回 (处理后的文本, 是否被裁剪)。
    """
    content = (text or "").strip()
    if len(content) <= limit:
        return content, False
    window = content[: limit + 1]
    cut = -1
    for m in _SENTENCE_END.finditer(window):
        cut = m.start()
    if cut >= max(6, limit // 4):
        trimmed = window[:cut].strip()
        if trimmed:
            return trimmed, True
    return content[:limit].strip(), True


class OutputGuard:
    """输出侧护栏。四态收敛：allow / retry / fallback / silent。"""

    def __init__(
        self,
        enabled: bool = True,
        fallbacks: tuple[str, ...] = DEFAULT_FALLBACKS,
        max_chars: int = MAX_REPLY_CHARS,
        hard_risk_action: str = "fallback",
    ) -> None:
        self.enabled = bool(enabled)
        self.fallbacks = tuple(fallbacks) or DEFAULT_FALLBACKS
        self.max_chars = max(10, int(max_chars))
        self.hard_risk_action = (
            hard_risk_action if hard_risk_action in {"retry", "fallback", "silent"}
            else "fallback"
        )
        self._fallback_index = 0

    # -- 判定 ---------------------------------------------------------------

    def check(self, text: str) -> Verdict:
        """检查一条待发送的输出。"""
        content = (text or "").strip()
        if not content:
            return Verdict(action="silent", reason="empty", text="")

        if not self.enabled:
            return Verdict(action="allow", text=content)

        # 1) 越界内容：最严重，优先判定。
        hit = _first_match("hard_risk", content)
        if hit:
            return Verdict(
                action=self.hard_risk_action,
                reason="hard_risk",
                matched=hit,
                text=content,
            )

        # 2) 舞台指示（整条没有正文）。
        if _first_match("stage_direction", content):
            return Verdict(
                action="silent",
                reason="stage_direction",
                text=content,
            )

        # 3) 规则外泄 / meta 吐槽：可直接降级成一句平话。
        hit = _first_match("meta_talk", content)
        if hit:
            return Verdict(
                action="fallback",
                reason="meta_talk",
                matched=hit,
                text=content,
            )

        # 4) 助手腔 / 模板腔收尾。
        hit = _first_match("assistant_tone", content)
        if hit:
            return Verdict(
                action="fallback",
                reason="assistant_tone",
                matched=hit,
                text=content,
            )

        # 5) 表现护栏：太长就收住，仍算通过。
        trimmed, was_trimmed = trim_long(content, self.max_chars)
        if was_trimmed:
            return Verdict(
                action="allow",
                reason="trimmed",
                text=trimmed,
                notes=["trimmed"],
            )

        return Verdict(action="allow", text=content)

    # -- 收敛 ---------------------------------------------------------------

    def next_fallback(self) -> str:
        """取一句兜底文案（轮换，避免每次都一样）。"""
        line = self.fallbacks[self._fallback_index % len(self.fallbacks)]
        self._fallback_index += 1
        return line

    def resolve(self, text: str, allow_retry: bool = False) -> tuple[str, Verdict]:
        """把 check 的结果收敛成"最终要发的内容"。

        返回 (要发送的文本, 判定)。文本为空字符串表示**不发**。
        """
        verdict = self.check(text)
        if verdict.action == "allow":
            return verdict.text, verdict
        if verdict.action == "retry" and allow_retry:
            # 交给调用方重生成；这里先按"不发"处理，由调用方决定后续。
            return "", verdict
        if verdict.action == "fallback":
            return self.next_fallback(), verdict
        return "", verdict

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "max_chars": self.max_chars,
            "rules": {k: len(v) for k, v in _COMPILED.items()},
        }
