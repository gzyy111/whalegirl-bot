# -*- coding: utf-8 -*-
"""本地 jevos 参与判定客户端（2026-10-16 新增）。

背景与实测依据见 `安装记录-本机.md` 五点九节：

  * 官方 TypeSafe Jev API 在本机被地域封锁（HTTP 451），改用同名开源实现
    jevos（feder-cr/jev，MIT），以 `jev.exe serve` 跑在 127.0.0.1:8017，
    线格式与官方一致：POST /v1/systemone。
  * 实测 139 条真实群消息样本，延迟中位 185ms。
  * **`addressed`（是不是在跟我说话）是唯一可靠维度**：
      点名消息 0.59~0.90，@别人 0.28~0.49，阈值 0.55 能干净分开。
  * **`should_reply`（该不该回）在中文上不可靠**：它把「@Peanut 美女多吗」
    判成 0.68~0.74，比认真求助的 0.46~0.60 还高，方向是反的。
    因此本模块只用它做**次要门控**，绝不让它单独决定回不回。
  * 英文指令下模型几乎失效（分离度 0.41→0.12），所以问题一律用中文问。

调用方拿到的是一次判定结果，**是否真的回复由调用方决定**；判定失败时
`ok=False`，调用方应回退到原有规则/概率，绝不因为判定服务挂了就不回话。
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from loguru import logger

DEFAULT_ENDPOINT = "http://127.0.0.1:8017/v1/systemone"
HEALTH_PATH = "/health"

BOT_ALIASES = "大肥鱼（也叫鲸鱼娘、小深、蓝鲸酱、大肥鲸）"

# 三个问题一律用中文问（实测英文提问会失效）
QUESTIONS = {
    "addressed": {
        "type": "noul",
        "instructions": (
            "这条消息是在对机器人「%s」说话吗？"
            "包括直接 @ 她、喊她名字或昵称、向她提问、接她上一句话、拍一拍她。"
            "如果是在 @ 或回应别的群友、或者只是群友之间互相闲聊没带上她，"
            "则不是在对她说话。" % BOT_ALIASES
        ),
    },
    # 2026-10-16 记录两次失败的尝试（别重蹈）：
    # 目标是让模型分清「在对她说」和「在背后议论她」。试了两条路，都不行——
    #   1) 把 addressed 的措辞收严（显式写明"议论她不算"）：
    #      addressed 整体被压到 0.16~0.45，连「@大肥鱼 出来干活！」都只剩 0.45，
    #      区分度直接消失，漏回严重。
    #   2) 另加一个 choice 问题，把 to_bot / about_bot 摆成互斥选项：
    #      模型把 47/48 条全判成 to_bot，等于没判。
    # 结论：目前只能用这个宽松版的 addressed + 阈值。它对「群友在讨论机器人」
    # 这类消息仍会误判（真实回放里「需要判定一下这句话是和大肥鱼说的还是我们
    # 自己在聊天」addr=0.73 会插嘴），该缺陷已如实记录，解法留待后续
    # （可能需要用本群语料微调，或换更大的判别模型）。
    "should_reply": {
        "type": "noul",
        "instructions": (
            "机器人「%s」应该回复这条消息吗？"
            "被点名、被提问、被搭话、被挑衅，应该回；"
            "群友之间各说各的、纯表情或系统提示，不应该回。"
            "注意：别人聊得正热闹且没带上她时，插进去很讨人厌。" % BOT_ALIASES
        ),
    },
    "reply_mode": {
        "type": "choice",
        "instructions": "如果机器人要回复，用下面哪种方式最合适？",
        "criteria": {
            "silent": "不要回复这条",
            "short": "极简，一句话带过，比如吐槽、点头、卖萌",
            "normal": "正常接话，聊一到两句",
            "serious": "认真解答，对方在求助、问知识或问电脑问题",
            # 2026-10-16：原来只写「用语音、唱歌或发表情包来回应」，
            # 139 条实测里 media 只被选中 2 次（1.4%），等于没触发。
            # 把适用场景写宽，让斗图 / 被怼 / 被夸 / 无语这类场合也能落到 media。
            "media": (
                "用表情包回应（她有个表情包库）。适合：被调侃、被怼、被夸、"
                "斗图、起哄、对方发的是表情或图片、情绪很浓但没什么可说的、"
                "或者干脆懒得打字"
            ),
        },
    },
}


@dataclass
class Judgement:
    """一次判定的结果。"""

    addressed: float = 0.0
    should_reply: float = 0.0
    reply_mode: str = ""
    # to_bot / about_bot / member_chat / system，见 QUESTIONS["intent"]
    intent: str = ""
    ok: bool = False                 # 判定是否成功；False 时调用方必须走降级
    skipped: str = ""                # 未真正调用的原因（熔断/关闭等）
    latency_ms: float = 0.0
    probabilities: dict = field(default_factory=dict)

    def summary(self) -> str:
        if not self.ok:
            return "判定未生效(%s)" % (self.skipped or "失败")
        return "intent=%s addr=%.2f rep=%.2f mode=%s (%.0fms)" % (
            self.intent or "?", self.addressed, self.should_reply,
            self.reply_mode or "?", self.latency_ms,
        )


class JevJudge:
    """本地 jevos 的判定封装（同步 HTTP，内部丢线程池跑）。"""

    # 连续失败这么多次就熔断，避免每条消息都白等一个超时
    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 60.0

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        enabled: bool = True,
        addressed_threshold: float = 0.55,
        should_reply_threshold: float = 0.35,
        fallback_participation: float = 0.15,
        timeout: float = 3.0,
        model: str = "jev-latest",
    ):
        self.endpoint = str(endpoint or DEFAULT_ENDPOINT)
        self.enabled = bool(enabled)
        try:
            self.addressed_threshold = float(addressed_threshold)
        except (TypeError, ValueError):
            self.addressed_threshold = 0.55
        try:
            self.should_reply_threshold = float(should_reply_threshold)
        except (TypeError, ValueError):
            self.should_reply_threshold = 0.35
        try:
            self.fallback_participation = min(
                max(float(fallback_participation), 0.0), 1.0
            )
        except (TypeError, ValueError):
            self.fallback_participation = 0.15
        try:
            self.timeout = max(0.5, float(timeout))
        except (TypeError, ValueError):
            self.timeout = 3.0
        self.model = model or "jev-latest"

        self._failures = 0
        self._disabled_until = 0.0
        self._calls = 0
        self._hits = 0

    # ------------------------------------------------------------------ 状态
    @property
    def available(self) -> bool:
        return self.enabled and time.time() >= self._disabled_until

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "calls": self._calls,
            "hits": self._hits,
            "failures": self._failures,
            "addressed_threshold": self.addressed_threshold,
            "should_reply_threshold": self.should_reply_threshold,
            "fallback_participation": self.fallback_participation,
        }

    # ------------------------------------------------------------------ 调用
    def _post(self, payload: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    async def judge(
        self,
        message: str,
        speaker: str = "群友",
        recent_context: str = "",
        room: str = "群聊",
    ) -> Judgement:
        """问一次三个问题。失败返回 ok=False，调用方自行降级。"""
        if not self.enabled:
            return Judgement(ok=False, skipped="未启用")
        if not self.available:
            return Judgement(ok=False, skipped="熔断中")
        text = (message or "").strip()
        if not text:
            return Judgement(ok=False, skipped="空消息")

        state = {
            "scene": "微信群聊",
            "room": room,
            "bot_nickname": "大肥鱼",
            "speaker": speaker or "群友",
        }
        if recent_context:
            state["recent_context"] = recent_context
        state["message"] = text

        payload = {"model": self.model, "state": state, "questions": QUESTIONS}
        self._calls += 1
        t0 = time.time()
        try:
            loop = asyncio.get_event_loop()
            out = await loop.run_in_executor(None, self._post, payload)
        except Exception as e:  # noqa: BLE001
            self._failures += 1
            if self._failures >= self.FAILURE_THRESHOLD:
                self._disabled_until = time.time() + self.COOLDOWN_SECONDS
                logger.warning(
                    "jevos 判定连续失败 %d 次，熔断 %.0f 秒（期间按原概率参与）: %s"
                    % (self._failures, self.COOLDOWN_SECONDS, e)
                )
            else:
                logger.warning("jevos 判定失败: %s" % e)
            return Judgement(ok=False, skipped="调用失败", latency_ms=(time.time() - t0) * 1000)

        self._failures = 0
        answers = (out or {}).get("answers") or {}
        addr = _noul(answers, "addressed")
        rep = _noul(answers, "should_reply")
        mode_a = answers.get("reply_mode") or {}
        mode = str(mode_a.get("choice") or "")
        intent_a = answers.get("intent") or {}
        intent = str(intent_a.get("choice") or "")
        return Judgement(
            addressed=addr,
            should_reply=rep,
            reply_mode=mode,
            intent=intent,
            ok=True,
            latency_ms=(time.time() - t0) * 1000,
            probabilities=mode_a.get("probabilities") or {},
        )

    # ------------------------------------------------------------------ 决策
    def decide(self, j: Judgement) -> bool:
        """把判定结果翻成「这一条要不要回」。

        规则：addressed 与 should_reply 都过线 -> 回；
              否则按 fallback_participation 兜底参与（保留一点活人感）。
        判定失败时不在这里做决定，由调用方走原有逻辑。
        """
        if not j.ok:
            return False
        # intent 目前恒为空（那个 choice 方案实测失效，见 QUESTIONS 顶部注释）。
        # 这两条分支留着，是给以后换到能用的意图分类时直接接上。
        if j.intent == "to_bot":
            self._hits += 1
            return True
        if j.intent == "about_bot":
            return False
        # 主判据：addressed 过线且 should_reply 过线 -> 直接参与；
        # 否则进兜底池，按 fallback_participation 的概率参与。
        if (
            j.addressed >= self.addressed_threshold
            and j.should_reply >= self.should_reply_threshold
        ):
            self._hits += 1
            return True
        return random.random() < self.fallback_participation


def _noul(answers: dict, key: str) -> float:
    item = answers.get(key) or {}
    try:
        return float(item.get("noul") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_state(message: str, speaker: str, recent_context: str = "",
                room: str = "群聊") -> dict:
    """单独暴露出来，方便自检脚本构造同样的输入。"""
    state = {
        "scene": "微信群聊",
        "room": room,
        "bot_nickname": "大肥鱼",
        "speaker": speaker or "群友",
    }
    if recent_context:
        state["recent_context"] = recent_context
    state["message"] = message or ""
    return state
