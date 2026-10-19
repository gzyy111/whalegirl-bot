# -*- coding: utf-8 -*-
"""讲笑话子系统（2026-10-19 新增）。

为什么单独做一个服务
--------------------
群友在群里说「来点大肥鱼笑话」时，原来的链路会把这句话丢给大模型自由发挥：
要么答一句「我不太会讲笑话」，要么现编一个又长又不好笑的，还白花一轮 token。
这里改成 **本地笑话库 + 本地意图解析**：命中就秒回、不花钱、不会幻觉，
并且按房间记录「最近讲过哪几条」，避免连续两次同一个包袱。

**大肥鱼笑话是什么**：「大肥鱼 / 蓝色大肥鱼」是中文社区给 DeepSeek 蓝鲸 logo
起的爱称，也指它的拟人形象 DeepSeek娘（鲸鱼娘）。社区二创里最有名的梗包括
「真当我是便宜货啊」「我这只吃白饭的蓝色大肥鱼」「用户别走，我上错模型了」
「已思考用时 3 秒」「V4.1 还早啊」。笑话库就是围绕这些梗写的，另外补了
鲸鱼/深海类冷梗，正好对上她的人设。

和表情包的配合
--------------
每条笑话自带一个 `sticker` 查询词（例如「服务器繁忙 排队 挤不进去」），
讲完之后由 `sticker_recall`（bge 向量召回，索引就是 2121 张梗图的台词与梗说明）
挑一张最贴题的表情包补发——这就是「讲完笑话接一个表情包」的实现方式。
查不到就退回随机挑图，不会因为配图失败影响笑话本身。
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


# ---------------------------------------------------------------- 数据结构
@dataclass
class Joke:
    id: str
    kind: str
    setup: str
    punch: str
    sticker: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return f"{self.setup}\n{self.punch}" if self.setup else self.punch


@dataclass
class JokeIntent:
    """一条「讲笑话」请求。kind=None 表示没点名类别，随机讲。"""

    kind: str | None = None
    again: bool = False
    count: int = 1


class JokeService:
    """本地笑话库：解析意图、抽一条不重复的、拼成鲸鱼娘口吻。"""

    # 类别关键词：命中就限定讲哪一类
    KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("whale", ("大肥鱼", "肥鱼", "鲸鱼", "鲸", "蓝鲸", "深海")),
        ("deepseek", ("deepseek", "deep seek", "深度求索", "小深", "ds",
                      "模型", "token", "服务器", "算力", "显卡")),
        ("cold", ("冷笑话", "谐音", "冷梗", "冷的", "好冷")),
    )

    # 明确的「再要一个」：包括抱怨不好笑——那也是要换一个。
    # 刻意不放「继续 / 别的 / 其他的」：这三样在群聊里太泛，
    # 「继续加班」「别的都行」会被误判成点笑话。
    RE_AGAIN = re.compile(
        r"(再来|再讲|再整|换个|换一条|换一个|还有吗|还有没|不好笑|不搞笑|"
        r"没意思|太冷|换点|别的笑话|其他笑话)"
    )
    # 「笑」系词：判断这是不是在要笑话
    RE_JOKE_WORD = re.compile(r"(笑话|段子|冷笑话|谐音梗|讲个笑|来点笑|笑一个|乐子)")
    # 请求动作词
    RE_ASK_VERB = re.compile(
        r"(讲|说|来|整|上|搞|念|听|给|换|续|安排|放|甩|扔)"
    )
    # 明确是在评价/转述，不是在点单（这些句子即使含「笑话」也不接管）
    RE_EVALUATING = re.compile(
        r"(笑死|笑话我|别笑|你笑|在笑|笑出声|好笑的|很搞笑|这个笑话|那个笑话|"
        r"我说的笑话|你说的笑话)"
    )
    # 用户在讲给我听，不是点单（「我讲个笑话给你听」不能被抢过来讲）。
    # 注意要排除「给我讲」——那是让我讲，不是他要讲。
    RE_SELF_TELL = re.compile(
        r"((?<![给帮替])我讲|我说个|我来讲|我给您?讲|我给你讲|我跟你说个|听我讲)"
    )
    # 「来点 / 几个 / 多点」→ 一次给两个
    RE_MULTI = re.compile(r"(来点|来几个|几个|多点|一堆|刷屏|连讲|来一串)")
    RE_COUNT_CN = {"两": 2, "二": 2, "三": 3, "2": 2, "3": 3}

    def __init__(
        self,
        file: str = "./data/jokes.json",
        history_file: str = "./data/joke_history.json",
        enabled: bool = True,
        recent_avoid: int = 8,
        global_avoid: int = 14,
        max_count: int = 2,
    ):
        self.enabled = bool(enabled)
        self._file = Path(file)
        self._history_file = Path(history_file)
        self._recent_avoid = max(0, int(recent_avoid))
        self._global_avoid = max(0, int(global_avoid))
        self._max_count = max(1, min(3, int(max_count)))
        self._jokes: list[Joke] = []
        self._opens: list[str] = []
        self._tails: dict[str, list[str]] = {}
        self._history: dict = {"rooms": {}, "global": []}
        self._load()
        self._load_history()

    # ------------------------------------------------------------------ 装载
    def _load(self) -> None:
        if not self.enabled:
            logger.info("[笑话] 已关闭（joke.enabled=false）")
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[笑话] 笑话库读取失败，讲笑话能力关闭: {e}")
            self._jokes = []
            return

        jokes: list[Joke] = []
        for item in raw.get("jokes") or []:
            if not isinstance(item, dict):
                continue
            setup = str(item.get("setup") or "").strip()
            punch = str(item.get("punch") or "").strip()
            if not punch:
                continue
            jokes.append(
                Joke(
                    id=str(item.get("id") or f"anon-{len(jokes)}"),
                    kind=str(item.get("kind") or "general"),
                    setup=setup,
                    punch=punch,
                    sticker=str(item.get("sticker") or "").strip(),
                    tags=[str(t) for t in (item.get("tags") or [])],
                )
            )
        self._jokes = jokes
        self._opens = [str(x) for x in (raw.get("opens") or []) if str(x).strip()]
        tails = raw.get("tails") or {}
        self._tails = {
            str(k): [str(x) for x in (v or []) if str(x).strip()]
            for k, v in tails.items()
        }
        kinds: dict[str, int] = {}
        for j in jokes:
            kinds[j.kind] = kinds.get(j.kind, 0) + 1
        logger.info(
            "[笑话] 已加载 %d 条：%s；配图查询词 %d 条"
            % (
                len(jokes),
                "、".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "无",
                sum(1 for j in jokes if j.sticker),
            )
        )

    # ------------------------------------------------------------------ 历史
    def _load_history(self) -> None:
        try:
            if self._history_file.is_file():
                data = json.loads(self._history_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._history = {
                        "rooms": data.get("rooms") or {},
                        "global": data.get("global") or [],
                    }
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[笑话] 历史读取失败（当空处理）: {e}")

    def _save_history(self) -> None:
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(
                json.dumps(self._history, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[笑话] 历史保存失败: {e}")

    def _recent_ids(self, room_id: str) -> list[str]:
        rooms = self._history.get("rooms") or {}
        items = rooms.get(room_id) or []
        ids = [str(x.get("id")) for x in items if isinstance(x, dict)]
        return ids[-self._recent_avoid:] if self._recent_avoid else []

    def _global_ids(self) -> list[str]:
        items = self._history.get("global") or []
        ids = [str(x.get("id")) for x in items if isinstance(x, dict)]
        return ids[-self._global_avoid:] if self._global_avoid else []

    def last_kind(self, room_id: str) -> str | None:
        """该房间上一次讲的是哪一类（「再来一个」时沿用）。"""
        items = (self._history.get("rooms") or {}).get(room_id) or []
        for entry in reversed(items):
            if isinstance(entry, dict) and entry.get("kind"):
                return str(entry["kind"])
        return None

    def record(self, joke: Joke, room_id: str) -> None:
        """记下这次讲了什么，供下次避重。"""
        now = time.time()
        entry = {"id": joke.id, "kind": joke.kind, "ts": now}
        rooms = self._history.setdefault("rooms", {})
        items = rooms.setdefault(room_id, [])
        items.append(entry)
        if len(items) > 20:
            rooms[room_id] = items[-20:]
        glob = self._history.setdefault("global", [])
        glob.append(entry)
        if len(glob) > 60:
            self._history["global"] = glob[-60:]
        self._save_history()

    # ------------------------------------------------------------------ 意图
    @property
    def available(self) -> bool:
        return bool(self.enabled and self._jokes)

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for j in self._jokes:
            kinds[j.kind] = kinds.get(j.kind, 0) + 1
        return {
            "enabled": self.enabled,
            "total": len(self._jokes),
            "kinds": kinds,
            "with_sticker": sum(1 for j in self._jokes if j.sticker),
        }

    def _guess_kind(self, text: str) -> str | None:
        content = str(text or "").casefold()
        for kind, words in self.KIND_HINTS:
            if any(w.casefold() in content for w in words):
                return kind
        return None

    def resolve(self, text: str) -> JokeIntent | None:
        """判断这句话是不是在要笑话；是就给出类别与条数。

        返回 None = 不是讲笑话的请求，交给后续链路（AI 主流程）。
        """
        if not self.available:
            return None
        content = str(text or "").casefold().strip()
        if not content or len(content) > 40:
            return None

        has_word = bool(self.RE_JOKE_WORD.search(content))
        again = bool(self.RE_AGAIN.search(content))
        if not has_word:
            # 没有说话「笑话/段子」时，只认「再来一个」「不好笑」这类纯追问。
            # 判据：把「再来/换个/不好笑」这些词全剥掉以后，几乎什么都不剩。
            # 这样「继续加班」「今天太冷了」不会被误判成点笑话。
            if not again:
                return None
            rest = re.sub(
                r"[\s,，。.!！?？~～、…]+", "", self.RE_AGAIN.sub("", content)
            )
            if len(rest) > 2:
                return None
            return JokeIntent(kind=None, again=True, count=1)

        # 含笑话词：先排掉「笑死我了」「笑话我一下」这类纯评价/攻击
        if not again and self.RE_EVALUATING.search(content):
            return None
        # 「我讲个笑话给你听」是用户要讲，不是点我要讲
        if self.RE_SELF_TELL.search(content):
            return None

        asked = bool(self.RE_ASK_VERB.search(content))
        # 去掉「笑话」这类词之后几乎没剩内容（「笑话」「冷笑话」）也算请求
        stripped = self.RE_JOKE_WORD.sub("", content)
        stripped = re.sub(r"[\s,，。.!！?？~～、…]+", "", stripped)
        bare = len(stripped) <= 6
        kind = self._guess_kind(content)
        if not (asked or bare or kind):
            return None

        count = 1
        if self.RE_MULTI.search(content) or "再" in content:
            count = 2
        m = re.search(r"([两二三四2-4])\s*(?:个|条|段)", content)
        if m:
            count = self.RE_COUNT_CN.get(m.group(1), 2)
        count = max(1, min(self._max_count, count))

        return JokeIntent(
            kind=kind,
            again=again,
            count=count,
        )

    def pick(self, intent: JokeIntent, room_id: str) -> list[Joke]:
        """按意图抽 count 条，尽量避开本房间与全局最近讲过的。"""
        if not self._jokes:
            return []
        kind = intent.kind
        if kind is None and intent.again:
            kind = self.last_kind(room_id)
        pool = [j for j in self._jokes if kind is None or j.kind == kind]
        if not pool:
            pool = list(self._jokes)

        recent_list = self._recent_ids(room_id)
        recent = set(recent_list)
        general = set(self._global_ids())
        # 三层兜底：都避开 → 只避开本房间 → 只避开最近三条 → 全池
        tiers = [
            [j for j in pool if j.id not in recent and j.id not in general],
            [j for j in pool if j.id not in recent],
            [j for j in pool if j.id not in set(recent_list[-3:])],
            list(pool),
        ]
        picked: list[Joke] = []
        for tier in tiers:
            if not tier:
                continue
            cand = [j for j in tier if j not in picked]
            random.shuffle(cand)
            while cand and len(picked) < intent.count:
                picked.append(cand.pop())
            if len(picked) >= intent.count:
                break
        return picked

    # ------------------------------------------------------------------ 组合
    def compose(self, jokes: list[Joke]) -> tuple[str, str]:
        """把笑话拼成一条可以直接发出去的文本，并给出配图查询词。

        返回 (文本, 配图查询词)。配图查询词为空表示不发图。
        """
        if not jokes:
            return "", ""
        blocks: list[str] = []
        if len(jokes) == 1 and self._opens and random.random() < 0.45:
            blocks.append(random.choice(self._opens))
        for i, joke in enumerate(jokes):
            if i:
                blocks.append("（再送一个）")
            blocks.append(joke.body)
        last = jokes[-1]
        tails = self._tails.get(last.kind) or []
        if len(jokes) == 1 and tails and random.random() < 0.45:
            blocks.append(random.choice(tails))
        # 配图查询词取最后一个笑话的，短语后面再叠一点画面词更容易召回
        query = last.sticker or f"{last.kind} 表情包"
        return "\n".join(b for b in blocks if b), query
