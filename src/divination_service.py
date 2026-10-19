"""算命服务：意图解析 → 排盘 → 断命 → 人设化解读。

群聊里的使用姿势（都在 @她 之后）：
    算命 1998年5月12日 下午2点半 女 看看事业
    农历1998年四月十七 未时 女
    起一卦 最近要不要换工作
    今日运势

没有出生信息时不会硬算，而是回一句「报上生辰」并把这次提问挂起 5 分钟，
下一条把生日补上就直接接着算。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from loguru import logger

from .divination import bazi, gua, reader, rules

# ---------------------------------------------------------------- 触发词
# 2026-10-19 改：关键词「包含」判定太窄——群里说「算个命」，"算命" 不是它的子串，
# 整条就掉进普通 AI，被回一句「本鲸鱼娘不是算命的」。改成正则，容忍「个/一/下」这类衬字。

RE_HELP = re.compile(r"(你会算命|怎么算命|算命功能|能算命吗|会看八字|怎么起卦|算命怎么用)")
RE_TODAY = re.compile(r"((今[日天])(的)?(运势|运气|黄历)|黄历|老黄历|运势)")
RE_ZIWEI = re.compile(r"(紫微斗数|紫微盘|紫微)")
RE_XLR = re.compile(r"(小六壬|掐指一算|掐指)")
RE_MEIHUA = re.compile(r"(梅花易数|梅花卦)")
RE_TAROT = re.compile(r"(塔罗|塔羅|tarot)", re.I)
RE_GUA = re.compile(
    r"(算|起|摇|占|卜|求|打)[个一]?下?卦|起卦|摇卦|问卦|"
    r"抽[个一]?签|求签|抽签|来[支一]?签")
RE_BAZI = re.compile(
    r"算算命|算[个一]?下?命|看[个一]?命|看.{0,3}命(?!令)|批[个一]?命|"
    r"排[个一]?盘|看[个一]?盘|命盘|八字|命理|本命")
#: 明确的否定/闲聊语境，别把吐槽当成求算
RE_NEG = re.compile(
    r"(算命.{0,8}(骗|假|迷信|都是|不靠谱)|不信算命|别算命|算命软件|算命节目|算命先生)")
#: 兜底：出现这些词但没命中严格句式时，至少问一句生辰，别再回「不是算命的」
RE_SOFT = re.compile(r"(算命|八字|命盘|起卦|抽签|黄历|命理)")

ASK_BIRTH = (
    "想给你看盘，得先知道生辰。\n"
    "格式：「算命 1998年5月12日 14:30 女」，农历就说「农历1998年四月十七 未时 女」。\n"
    "时辰越准越好，实在不记得就先报个大概，本鲸鱼娘按午时给你排。"
)

HELP_TEXT = (
    "本鲸鱼娘现在会这几样：\n"
    "一、八字排盘断命——报出生年月日时和性别，公历农历都行，先排四柱给你核对，"
    "再说性格、事业、财运、感情、这几年的大运。\n"
    "二、紫微斗数——同一份生辰，换成「紫微 1999年7月1日 17:00 男」，看命宫、三方四正、大限。\n"
    "三、小六壬——不用生辰，直接说「小六壬 问工作」，我掐指给你落一宫。\n"
    "四、梅花易数——「梅花易数 问考编」，按当下时辰起卦，看体用生克；报两个数也行。\n"
    "五、塔罗——「塔罗 问感情」抽三张（过去/现在/未来），「塔罗 一张」就抽单张，正逆位都看。\n"
    "六、起卦 / 抽签——「起一卦 要不要换工作」。\n"
    "七、黄历——「今日运势」直接看今天宜忌冲煞。\n"
    "先说好：盘是算出来的，断语只是参考，不当真也不吓人。"
)

#: 农历月名 → 月数
LUNAR_MONTHS = {
    "正": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "冬": 11, "十一": 11,
    "腊": 12, "十二": 12,
}
SHICHEN = {"子": 0, "丑": 2, "寅": 4, "卯": 6, "辰": 8, "巳": 10,
           "午": 12, "未": 14, "申": 16, "酉": 18, "戌": 20, "亥": 22}

DATE_RE = re.compile(
    r"(?P<y>19\d{2}|20\d{2}|\d{2})\s*[年\-/\.．]\s*(?P<m>\d{1,2})\s*[月\-/\.．]\s*(?P<d>\d{1,2})\s*[日号]?"
)
LUNAR_RE = re.compile(
    r"(?:农历|阴历|旧历|老历)\s*(?P<y>19\d{2}|20\d{2}|\d{2})\s*年\s*(?P<mn>正|冬|腊|十[一二]?|[一二三四五六七八九])\s*月\s*(?P<d>初?[一二三四五六七八九十]+)\s*[日号]?"
)
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass
class DivinationIntent:
    kind: str                       # bazi / bazi_ask / ziwei / xlr / meihua / gua / today / help
    birth: bazi.Birth | None = None
    question: str = ""
    raw: str = ""
    note: str = ""
    #: {"time": 是否报了时辰, "gender": 是否写了性别}
    flags: dict = field(default_factory=dict)
    #: 问盘类型（等生日补齐时用来记「他问的是紫微还是八字」）
    target: str = ""
    #: 报数起卦时的数字
    numbers: tuple = ()


@dataclass
class _Pending:
    question: str
    kind: str
    expire: float
    birth: bazi.Birth | None = None
    flags: dict = field(default_factory=dict)
    target: str = "bazi"


def _cn_day(text: str) -> int | None:
    """初一 / 十七 / 二十三 → 日"""
    t = text.strip().lstrip("初")
    if not t:
        return None
    if t == "十":
        return 10
    if t.startswith("十"):
        rest = t[1:]
        return 10 + CN_DIGITS.get(rest, 0) if rest else 10
    if "十" in t:
        a, b = t.split("十", 1)
        return CN_DIGITS.get(a, 0) * 10 + (CN_DIGITS.get(b, 0) if b else 0)
    return CN_DIGITS.get(t)


class DivinationService:
    """对外只暴露 resolve / answer / pending 三组方法。"""

    def __init__(
        self,
        ai=None,
        enabled: bool = True,
        persona: str = "",
        max_tokens: int = 700,
        temperature: float = 0.88,
        pending_ttl: float = 300.0,
        true_solar: bool = False,
        longitude: float | None = None,
        tarot_images: bool = True,
    ):
        self.ai = ai
        self.enabled = bool(enabled)
        self.persona = persona or ""
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.pending_ttl = float(pending_ttl)
        self.true_solar = bool(true_solar)
        self.longitude = longitude
        self.tarot_images = bool(tarot_images)
        self._pending: dict[str, _Pending] = {}
        #: 本轮要补发的图片（目前只有塔罗牌面），由 handler 取走
        self._media: list[str] = []

    def take_media(self) -> list[str]:
        media, self._media = self._media, []
        return media

    # ------------------------------------------------------------ 意图

    @staticmethod
    def _norm(text: str) -> str:
        return (text or "").replace("＠", "@").strip()

    def _detect_kind(self, text: str) -> str:
        if RE_HELP.search(text):
            return "help"
        if RE_NEG.search(text):
            return ""
        if RE_TODAY.search(text):
            return "today"
        if RE_ZIWEI.search(text):
            return "ziwei"
        if RE_XLR.search(text):
            return "xlr"
        if RE_MEIHUA.search(text):
            return "meihua"
        if RE_TAROT.search(text):
            return "tarot"
        if RE_GUA.search(text):
            return "gua"
        if RE_BAZI.search(text):
            return "bazi"
        if RE_SOFT.search(text):     # 兜底：宁可多问一句，也别装不会
            return "bazi"
        return ""

    def _numbers(self, text: str) -> tuple:
        """报数起卦：从话里抠 2—3 个数字。带日期的句子不算。"""
        if DATE_RE.search(text) or LUNAR_RE.search(text):
            return ()
        nums = [int(x) for x in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", text)]
        return tuple(nums[:3]) if len(nums) >= 2 else ()

    def _time_and_gender(self, text: str) -> tuple[int | None, int, str | None]:
        """从一句话里抽 (小时, 分钟, 性别)。抽不到就是 None。"""
        hour: int | None = None
        minute = 0
        m = re.search(r"(?<!\d)(\d{1,2})\s*[:：.．]\s*(\d{2})(?!\d)", text)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(\d{1,2})\s*[点時时]", text)
            if m:
                hour = int(m.group(1))
                if "半" in text[m.end():m.end() + 2]:
                    minute = 30
            else:
                m = re.search(r"([子丑寅卯辰巳午未申酉戌亥])\s*时", text)
                if m:
                    hour = SHICHEN[m.group(1)]
        if hour is not None:
            if any(k in text for k in ("下午", "傍晚", "晚上")) and hour < 12:
                hour += 12
            elif "中午" in text and hour < 11:
                hour += 12
            elif "凌晨" in text and hour == 12:
                hour = 0
            hour = max(0, min(23, hour))
            minute = minute if 0 <= minute < 60 else 0

        gender = None
        if re.search(r"(性别[:：]?\s*)?(男|男生|男的|男性)", text):
            gender = "男"
        elif re.search(r"(性别[:：]?\s*)?(女|女生|女的|女性)", text):
            gender = "女"
        return hour, minute, gender

    def _parse_birth(self, text: str) -> tuple[bazi.Birth | None, str, dict]:
        """解析出生信息。返回 (Birth, 提示, 是否报齐时辰/性别)。"""
        birth = None
        note = ""

        m = LUNAR_RE.search(text)
        if m:
            year = int(m.group("y"))
            if year < 100:
                year += 1900
            month = LUNAR_MONTHS.get(m.group("mn"), 0)
            day = _cn_day(m.group("d"))
            if month and day:
                birth = bazi.Birth(year=year, month=month, day=day,
                                   calendar="lunar")
                note = "按农历排的"
        if birth is None:
            m = DATE_RE.search(text)
            if m:
                year = int(m.group("y"))
                if year < 100:
                    year += 1900
                month, day = int(m.group("m")), int(m.group("d"))
                if 1 <= month <= 12 and 1 <= day <= 31:
                    birth = bazi.Birth(year=year, month=month, day=day)
                    if "农历" in text or "阴历" in text:
                        birth.calendar = "lunar"
                        note = "按农历排的"
        if birth is None:
            return None, "", {}

        hour, minute, gender = self._time_and_gender(text)
        flags = {"time": hour is not None, "gender": gender is not None}
        if hour is None:
            note = (note + "；" if note else "") + "没报时辰，先按午时（12:00）排"
            birth.hour = 12
        else:
            birth.hour, birth.minute = hour, minute
        if gender is None:
            note = (note + "；" if note else "") + "没写性别，按女命排（性别影响大运顺逆）"
        else:
            birth.gender = gender

        if self.longitude is not None:
            birth.longitude = self.longitude
        return birth, note, flags

    def _extract_question(self, text: str, kind: str) -> str:
        """把触发词和生辰信息剥掉，剩下的大概率是用户真正想问的那句。"""
        q = DATE_RE.sub(" ", text)
        q = LUNAR_RE.sub(" ", q)
        for pattern in (RE_HELP, RE_TODAY, RE_ZIWEI, RE_XLR, RE_MEIHUA,
                        RE_TAROT, RE_GUA, RE_BAZI):
            q = pattern.sub(" ", q)
        q = re.sub(r"(性别[:：]?\s*)?[男女](生|的|性)?", " ", q)
        q = re.sub(r"(上午|下午|中午|凌晨|早上|晚上|傍晚|半夜)", " ", q)
        q = re.sub(r"\d{1,2}\s*[:：]?\s*\d{0,2}\s*[点時时分半]?", " ", q)
        q = re.sub(r"[子丑寅卯辰巳午未申酉戌亥]\s*时", " ", q)
        if kind in ("xlr", "meihua"):
            q = re.sub(r"(?<!\d)\d{1,3}(?!\d)", " ", q)
        if kind == "tarot":
            q = re.sub(r"(单张|一张|抽一张|抽一支|三张|抽三张)", " ", q)
        q = re.sub(r"@\S+\s*", " ", q)
        q = re.sub(r"(帮我|帮忙|给我|替我|麻烦|请|你|帮我算算|算算|看看|瞧瞧|瞅瞅)", " ", q)
        q = re.sub(r"[，,。.、!！?？~～…]+", " ", q)
        q = re.sub(r"\s+", " ", q).strip()
        if len(q) <= 1:
            return ""
        return q[:60]

    def _looks_like_birth_only(self, text: str) -> bool:
        """用户直接甩一个完整生辰（没有「算命」二字）时也认。

        门槛放得比较高：必须同时有「具体时辰」和「性别」，而且整句很短——
        群里随口说「我生日5月12日」不会被误当成求算。
        """
        if len(text) > 40:
            return False
        birth, _, _ = self._parse_birth(text)
        if birth is None:
            return False
        hour, _minute, gender = self._time_and_gender(text)
        return hour is not None and gender is not None

    def resolve(self, text: str) -> DivinationIntent | None:
        if not self.enabled:
            return None
        text = self._norm(text)
        if len(text) > 200:
            return None
        kind = self._detect_kind(text)
        if not kind:
            if self._looks_like_birth_only(text):
                birth, note, flags = self._parse_birth(text)
                return DivinationIntent(kind="bazi", birth=birth, raw=text,
                                        note=note, flags=flags, question="")
            return None
        if kind == "help":
            return DivinationIntent(kind="help", raw=text)
        if kind == "today":
            return DivinationIntent(kind="today", raw=text,
                                    question=self._extract_question(text, kind))
        if kind == "gua":
            return DivinationIntent(kind="gua", raw=text,
                                    question=self._extract_question(text, kind))
        if kind == "xlr":
            return DivinationIntent(kind="xlr", raw=text,
                                    numbers=self._numbers(text),
                                    question=self._extract_question(text, kind))
        if kind == "meihua":
            return DivinationIntent(kind="meihua", raw=text,
                                    numbers=self._numbers(text),
                                    question=self._extract_question(text, kind))
        if kind == "tarot":
            n = 1 if re.search(r"(单张|一张|抽一张|抽一支)", text) else 3
            return DivinationIntent(kind="tarot", raw=text, numbers=(n,),
                                    question=self._extract_question(text, kind))
        if kind == "ziwei":
            birth, note, flags = self._parse_birth(text)
            question = self._extract_question(text, kind)
            if birth is None:
                return DivinationIntent(kind="bazi_ask", raw=text, note=note,
                                        question=question, target="ziwei")
            if not flags.get("time") and not flags.get("gender"):
                return DivinationIntent(kind="bazi_ask", birth=birth, flags=flags,
                                        raw=text, note="gap", question=question,
                                        target="ziwei")
            return DivinationIntent(kind="ziwei", birth=birth, raw=text, note=note,
                                    flags=flags, question=question, target="ziwei")
        birth, note, flags = self._parse_birth(text)
        question = self._extract_question(text, kind)
        if birth is None:
            return DivinationIntent(kind="bazi_ask", raw=text, note=note,
                                    question=question, target="bazi")
        # 时辰和性别都没报：先问一句，别拿默认值硬排
        if not flags.get("time") and not flags.get("gender"):
            return DivinationIntent(kind="bazi_ask", birth=birth, flags=flags,
                                    raw=text, note="gap", question=question,
                                    target="bazi")
        return DivinationIntent(kind="bazi", birth=birth, raw=text, note=note,
                                flags=flags, question=question, target="bazi")

    # ------------------------------------------------------------ 挂起

    def set_pending(self, room_id: str, question: str, kind: str = "bazi",
                    birth: bazi.Birth | None = None,
                    flags: dict | None = None,
                    target: str = "bazi") -> None:
        self._pending[room_id] = _Pending(
            question, kind, time.time() + self.pending_ttl,
            birth=birth, flags=dict(flags or {}), target=target or "bazi",
        )

    def get_pending(self, room_id: str) -> dict | None:
        p = self._pending.get(room_id)
        if not p:
            return None
        if time.time() > p.expire:
            self._pending.pop(room_id, None)
            return None
        return {"question": p.question, "kind": p.kind,
                "birth": p.birth, "flags": dict(p.flags), "target": p.target}

    def clear_pending(self, room_id: str) -> None:
        self._pending.pop(room_id, None)

    def pending_intent(self, text: str, pending: dict) -> DivinationIntent | None:
        """下一条消息里补的生日（或补的时辰/性别）。补上了就接回上一轮的提问。"""
        if not pending or pending.get("kind") != "bazi":
            return None
        question = pending.get("question", "")
        base = pending.get("birth")
        target = pending.get("target") or "bazi"

        # 情况一：上一轮连生日都没有，这条要能解析出日期
        if base is None:
            birth, note, flags = self._parse_birth(text)
            if birth is None:
                return None
            if not flags.get("time") and not flags.get("gender"):
                return DivinationIntent(kind="bazi_ask", birth=birth,
                                        flags=flags, raw=text, note="gap",
                                        question=question, target=target)
            return DivinationIntent(kind=target, birth=birth, flags=flags,
                                    note=note, raw=text, question=question,
                                    target=target)

        # 情况二：生日已经有了，这条只是补时辰/性别
        hour, minute, gender = self._time_and_gender(text)
        flags = dict(pending.get("flags") or {})
        birth = bazi.Birth(**vars(base))
        if hour is not None:
            birth.hour, birth.minute = hour, minute
            flags["time"] = True
        if gender:
            birth.gender = gender
            flags["gender"] = True
        if not flags.get("time") and not flags.get("gender"):
            return DivinationIntent(kind="bazi_ask", birth=birth, flags=flags,
                                    raw=text, note="gap", question=question,
                                    target=target)
        note_parts = []
        if not flags.get("time"):
            note_parts.append("没报时辰，先按午时排")
        if not flags.get("gender"):
            note_parts.append("没写性别，按女命排")
        return DivinationIntent(kind=target, birth=birth, flags=flags,
                                note="；".join(note_parts), raw=text,
                                question=question, target=target)

    # ------------------------------------------------------------ 出结果

    async def answer(self, intent: DivinationIntent) -> str:
        self._media = []
        if intent.kind == "help":
            return HELP_TEXT
        if intent.kind == "bazi_ask":
            if intent.birth is not None:
                return self._gap_text(intent.birth)
            return ASK_BIRTH

        if intent.kind == "bazi" and intent.birth is not None:
            return await self._bazi(intent)
        if intent.kind == "ziwei" and intent.birth is not None:
            return await self._ziwei(intent)
        if intent.kind == "xlr":
            return await self._xlr(intent)
        if intent.kind == "meihua":
            return await self._meihua(intent)
        if intent.kind == "tarot":
            return await self._tarot(intent)
        if intent.kind == "gua":
            return await self._gua(intent)
        if intent.kind == "today":
            return await self._today(intent)
        return ""

    async def _bazi(self, intent: DivinationIntent) -> str:
        chart = bazi.build_chart(intent.birth)
        analysis = rules.analyze(chart)
        header = "【盘面】" + bazi.chart_brief(chart)
        if intent.note:
            header += f"（{intent.note}）"
        system, user = reader.build_bazi_reading(
            chart, analysis, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                self.max_tokens, self.temperature)
        if not text:
            text = self._fallback_from_facts(analysis)
        logger.info(
            f"[算命] 八字 {chart['birth']['solar']} {chart['bazi']} "
            f"格局={chart['pattern']} 要点={len(analysis['facts'])} 条"
        )
        return header + "\n\n" + text

    async def _gua(self, intent: DivinationIntent) -> str:
        result = gua.cast()
        header = (
            f"【卦】第{result['original']['number']}卦 "
            f"{result['original']['name']}（{result['original']['keyword']}）"
        )
        if result["changed"]:
            header += f" → {result['changed']['name']}"
        system, user = reader.build_gua_reading(
            result, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                min(self.max_tokens, 600), self.temperature)
        if not text:
            text = gua.cast_text(result)
        logger.info(f"[算命] 起卦 {result['original']['name']} 动爻={result['moving']}")
        return header + "\n\n" + text

    async def _ziwei(self, intent: DivinationIntent) -> str:
        try:
            from .divination import ziwei
        except Exception as exc:  # iztro-py 没装也不能拖垮整条路径
            logger.error(f"[算命] 紫微依赖不可用: {exc}")
            return "紫微盘要用 iztro 排，本鲸鱼娘这边还没装好，先看八字行不行？"
        chart = ziwei.build_chart(intent.birth)
        header = "【紫微】" + ziwei.chart_brief(chart)
        if intent.note:
            header += f"（{intent.note}）"
        system, user = reader.build_ziwei_reading(
            chart, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                self.max_tokens, self.temperature)
        if not text:
            text = "紫微盘排出来了，但本鲸鱼娘这会儿说不出话，先看盘面吧。"
        logger.info(
            f"[算命] 紫微 {chart['birth']['solar']} 命宫="
            f"{(chart['soul_palace'] or {}).get('name', '')} "
            f"主星={'、'.join(m['name'] for m in (chart['soul_palace'] or {}).get('major', []))}"
        )
        return header + "\n\n" + text

    async def _xlr(self, intent: DivinationIntent) -> str:
        from .divination import xiaoliuren as xlr

        nums = intent.numbers if len(intent.numbers) >= 3 else None
        result = xlr.cast(1, 1, 0, numbers=nums) if nums else xlr.cast_now()
        header = f"【小六壬】{result['source']}　落宫：{result['palace']}（{result['luck']}）"
        system, user = reader.build_xlr_reading(
            result, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                min(self.max_tokens, 520), self.temperature)
        if not text:
            text = xlr.cast_text(result)
        logger.info(f"[算命] 小六壬 落宫={result['palace']} 来源={result['source']}")
        return header + "\n\n" + text

    async def _meihua(self, intent: DivinationIntent) -> str:
        from .divination import meihua

        if len(intent.numbers) >= 2:
            result = meihua.cast_numbers(intent.numbers[0], intent.numbers[1],
                                         intent.question)
        else:
            result = meihua.cast_time(question=intent.question)
        header = (
            f"【梅花】{result['original']['name']}（{result['original']['keyword']}）"
            f" → {result['changed']['name']}　体{result['ti']}·用{result['yong']}"
            f"·{result['relation']}（{result['luck']}）"
        )
        system, user = reader.build_meihua_reading(
            result, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                min(self.max_tokens, 560), self.temperature)
        if not text:
            text = meihua.cast_text(result)
        logger.info(
            f"[算命] 梅花 {result['original']['name']}→{result['changed']['name']} "
            f"体用={result['relation']}"
        )
        return header + "\n\n" + text

    async def _tarot(self, intent: DivinationIntent) -> str:
        from .divination import tarot

        n = intent.numbers[0] if intent.numbers else 3
        result = tarot.cast(n)
        names = "｜".join(
            f"{d['position']}·{d['card']['name']}{'（逆）' if d['reversed'] else ''}"
            for d in result["drawn"]
        )
        header = f"【塔罗】{result['spread']}：{names}"
        system, user = reader.build_tarot_reading(
            result, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                min(self.max_tokens, 620), self.temperature)
        if not text:
            text = tarot.cast_text(result)
        if self.tarot_images:
            try:
                self._media = [str(p) for p in tarot.images_for(result)]
            except Exception as exc:  # 出图失败不影响解读
                logger.warning(f"[算命] 塔罗出图失败: {exc}")
                self._media = []
        logger.info(f"[算命] 塔罗 {result['spread']} {names}")
        return header + "\n\n" + text

    @staticmethod
    def _gap_text(birth: bazi.Birth) -> str:
        got = f"{birth.year}年{birth.month}月{birth.day}日"
        if birth.calendar == "lunar":
            got += "（农历）"
        return (
            f"生日记下了：{got}。\n"
            "还差两样：① 出生的时辰（几点，不记得就说不记得）② 性别是男是女。\n"
            "回一句「女 晚上8点」这样就行，本鲸鱼娘接着给你排。"
        )

    #: 黄历宜项里的丧葬类词，群聊里念出来太阴间，展示时略去
    DARK_YI = ("入殓", "破土", "启钻", "安葬", "除服", "成服", "移柩",
               "修坟", "立碑", "谢土", "开生坟", "合寿木", "行丧")

    @classmethod
    def _friendly_yi(cls, items: list[str]) -> list[str]:
        out = []
        for item in items:
            if item in cls.DARK_YI or "殓" in item or "葬" in item:
                continue
            out.append(item)
            if len(out) >= 5:
                break
        return out

    async def _today(self, intent: DivinationIntent) -> str:
        huangli = bazi.today_huangli()
        yi = self._friendly_yi(huangli["yi"])
        header = (
            f"【今日】{huangli['solar']} {huangli['ganzhi']}\n"
            f"宜：{'、'.join(yi) if yi else '今天日子偏静，宜守不宜动'}\n"
            f"忌：{'、'.join(huangli['ji'][:6]) or '无'}"
        )
        for_prompt = dict(huangli)
        for_prompt["yi"] = yi
        system, user = reader.build_today_reading(
            for_prompt, intent.question, self.persona)
        text = await reader.ask(self.ai, system, user,
                                min(self.max_tokens, 500), self.temperature)
        if not text:
            text = "今天按老黄历的条目走就行，别硬碰冲煞的方向。"
        logger.info(f"[算命] 今日黄历 {huangli['solar']}")
        return header + "\n\n" + text

    @staticmethod
    def _fallback_from_facts(analysis: dict) -> str:
        facts = analysis["facts"][:4]
        lines = [analysis["summary"] + "。"]
        for f in facts:
            lines.append(f"· {f['area']}：{f['conclusion']}")
        lines.append("（本鲸鱼娘今天脑子有点糊，先把这个盘给你，细节改天再补。）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 状态

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "pending": len(self._pending),
            "has_ai": self.ai is not None,
            "true_solar": self.true_solar,
        }
