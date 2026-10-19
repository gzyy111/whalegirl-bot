"""八字排盘：把出生时间变成一张可断的盘面。

依赖 lunar-python（6tail 的开源历法库，PyPI: lunar-python，纯 Python、零重依赖）。
本模块只负责「算」：四柱、十神、藏干、纳音、地势、五行力量、神煞、大运流年、
刑冲合害、黄历。**不做任何吉凶判断**——判断在 rules.py，话术在 reader.py。

设计原则（2026-10-19）：
1. 一切盘面数据都能在对话里复述给用户核对，不写"我算出来"这种无法验证的结论；
2. 强弱/格局这类有流派的算法，统一标注「简化模型」，不冒充某一派精算；
3. 不用 LLM 生成盘面——盘面错一个字，后面全是编的。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from lunar_python import Lunar, Solar

# ---------------------------------------------------------------- 基础表

GAN = "甲乙丙丁戊己庚辛壬癸"
ZHI = "子丑寅卯辰巳午未申酉戌亥"

GAN_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}
GAN_YINYANG = {
    "甲": "阳", "乙": "阴", "丙": "阳", "丁": "阴", "戊": "阳",
    "己": "阴", "庚": "阳", "辛": "阴", "壬": "阳", "癸": "阴",
}
ZHI_WUXING = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
    "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水",
}
#: 地支藏干，顺序为「本气、中气、余气」
ZHI_HIDE_GAN = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "庚", "戊"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}
HIDE_WEIGHTS = (1.0, 0.5, 0.25)

SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

WUXING_ORDER = ("木", "火", "土", "金", "水")

CHONG = {
    "子": "午", "午": "子", "丑": "未", "未": "丑", "寅": "申", "申": "寅",
    "卯": "酉", "酉": "卯", "辰": "戌", "戌": "辰", "巳": "亥", "亥": "巳",
}
HE6 = {
    "子": "丑", "丑": "子", "寅": "亥", "亥": "寅", "卯": "戌", "戌": "卯",
    "辰": "酉", "酉": "辰", "巳": "申", "申": "巳", "午": "未", "未": "午",
}
HAI6 = {
    "子": "未", "未": "子", "丑": "午", "午": "丑", "寅": "巳", "巳": "寅",
    "卯": "辰", "辰": "卯", "申": "亥", "亥": "申", "酉": "戌", "戌": "酉",
}
XING_GROUPS = (
    ("寅", "巳", "申"),
    ("丑", "戌", "未"),
    ("子", "卯"),
)
SELF_XING = {"辰", "午", "酉", "亥"}
SANHE = (("申", "子", "辰"), ("亥", "卯", "未"), ("寅", "午", "戌"), ("巳", "酉", "丑"))

POS_NAMES = ("年柱", "月柱", "日柱", "时柱")


# ---------------------------------------------------------------- 十神


def shi_shen(day_gan: str, other_gan: str) -> str:
    """以日干为「我」，求另一个天干的十神。"""
    me = GAN_WUXING[day_gan]
    other = GAN_WUXING[other_gan]
    same_polarity = GAN_YINYANG[day_gan] == GAN_YINYANG[other_gan]
    if other == me:
        return "比肩" if same_polarity else "劫财"
    if SHENG[me] == other:
        return "食神" if same_polarity else "伤官"
    if KE[me] == other:
        return "偏财" if same_polarity else "正财"
    if KE[other] == me:
        return "七杀" if same_polarity else "正官"
    if SHENG[other] == me:
        return "偏印" if same_polarity else "正印"
    return ""


def shi_shen_group(name: str) -> str:
    """十神归到「比劫/食伤/财/官杀/印」五类。"""
    return {
        "比肩": "比劫", "劫财": "比劫",
        "食神": "食伤", "伤官": "食伤",
        "正财": "财", "偏财": "财",
        "正官": "官杀", "七杀": "官杀",
        "正印": "印", "偏印": "印",
    }.get(name, "")


# ---------------------------------------------------------------- 神煞表（以日干/年支/日支查）

TIANYI = {  # 天乙贵人
    "甲": "丑未", "戊": "丑未", "庚": "丑未",
    "乙": "子申", "己": "子申",
    "丙": "亥酉", "丁": "亥酉",
    "壬": "卯巳", "癸": "卯巳",
    "辛": "午寅",
}
WENCHANG = {"甲": "巳", "乙": "午", "丙": "申", "戊": "申", "丁": "酉",
            "己": "酉", "庚": "亥", "辛": "子", "壬": "寅", "癸": "卯"}
LUSHEN = {"甲": "寅", "乙": "卯", "丙": "巳", "戊": "巳", "丁": "午",
          "己": "午", "庚": "申", "辛": "酉", "壬": "亥", "癸": "子"}
YANGREN = {"甲": "卯", "乙": "寅", "丙": "午", "戊": "午", "丁": "巳",
           "己": "巳", "庚": "酉", "辛": "申", "壬": "子", "癸": "亥"}

#: 三合局 -> 桃花/驿马/华盖/将星/劫煞
TRIAD_STARS = {
    "寅午戌": {"桃花": "卯", "驿马": "申", "华盖": "戌", "将星": "午", "劫煞": "亥"},
    "申子辰": {"桃花": "酉", "驿马": "寅", "华盖": "辰", "将星": "子", "劫煞": "巳"},
    "巳酉丑": {"桃花": "午", "驿马": "亥", "华盖": "丑", "将星": "酉", "劫煞": "寅"},
    "亥卯未": {"桃花": "子", "驿马": "巳", "华盖": "未", "将星": "卯", "劫煞": "申"},
}

GUCHEN = {  # 年支三会 -> (孤辰, 寡宿)
    "亥子丑": ("寅", "戌"), "寅卯辰": ("巳", "丑"),
    "巳午未": ("申", "辰"), "申酉戌": ("亥", "未"),
}
HONGLUAN = {"子": "卯", "丑": "寅", "寅": "丑", "卯": "子", "辰": "亥", "巳": "戌",
            "午": "酉", "未": "申", "申": "未", "酉": "午", "戌": "巳", "亥": "辰"}


def _triad_key(zhi: str) -> str:
    for group in TRIAD_STARS:
        if zhi in group:
            return group
    return ""


def _shensha(day_gan: str, year_zhi: str, day_zhi: str,
             branches: list[str], xunkong: list[str]) -> list[dict]:
    """常见神煞。只取「落在四柱地支上」的那些，避免报一堆没落地的名字。"""
    found: list[dict] = []

    def add(name: str, target: str, note: str = "") -> None:
        for pos, zhi in zip(POS_NAMES, branches):
            if zhi == target:
                found.append({"name": name, "pos": pos, "zhi": zhi, "note": note})

    for zhi in TIANYI.get(day_gan, ""):
        add("天乙贵人", zhi, "贵人相助、遇难有人搭手")
    add("文昌", WENCHANG.get(day_gan, ""), "读书、考试、文书")
    add("禄神", LUSHEN.get(day_gan, ""), "衣食、俸禄")
    add("羊刃", YANGREN.get(day_gan, ""), "刚烈、冲劲、易过刚")

    for base, label in ((year_zhi, "年支"), (day_zhi, "日支")):
        stars = TRIAD_STARS.get(_triad_key(base), {})
        for name, zhi in stars.items():
            add(name, zhi, f"以{label}起")

    for group, (gu, gua) in GUCHEN.items():
        if year_zhi in group:
            add("孤辰", gu)
            add("寡宿", gua)

    add("红鸾", HONGLUAN.get(year_zhi, ""), "喜事、姻缘")
    hl = HONGLUAN.get(year_zhi, "")
    if hl:
        add("天喜", ZHI[(ZHI.index(hl) + 6) % 12], "喜事、添丁")

    for zhi in xunkong:
        add("空亡", zhi, "该柱力量打折、易落空")
    return found


# ---------------------------------------------------------------- 关系


def _relations(branches: list[str]) -> list[str]:
    """四柱地支之间的冲/合/害/刑/三合。"""
    out: list[str] = []
    for i in range(4):
        for j in range(i + 1, 4):
            a, b = branches[i], branches[j]
            if CHONG.get(a) == b:
                out.append(f"{POS_NAMES[i]}与{POS_NAMES[j]}相冲（{a}{b}）")
            if HE6.get(a) == b:
                out.append(f"{POS_NAMES[i]}与{POS_NAMES[j]}六合（{a}{b}）")
            if HAI6.get(a) == b:
                out.append(f"{POS_NAMES[i]}与{POS_NAMES[j]}相害（{a}{b}）")
            for group in XING_GROUPS:
                if a in group and b in group and a != b:
                    out.append(f"{POS_NAMES[i]}与{POS_NAMES[j]}相刑（{a}{b}）")
                    break
            if a == b and a in SELF_XING:
                out.append(f"{POS_NAMES[i]}与{POS_NAMES[j]}自刑（{a}{a}）")
    for group in SANHE:
        hit = [z for z in branches if z in group]
        if len(set(hit)) == 3:
            out.append("三合" + "".join(group) + "局")
    return out


# ---------------------------------------------------------------- 真太阳时


def true_solar_time(dt: datetime, longitude: float) -> datetime:
    """把北京时间换算成出生地真太阳时（经度差 + 均时差）。"""
    day_of_year = dt.timetuple().tm_yday
    b = 2 * math.pi * (day_of_year - 81) / 364.0
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    minutes = (longitude - 120.0) * 4.0 + eot
    return dt + timedelta(minutes=minutes)


# ---------------------------------------------------------------- 主入口


@dataclass
class Birth:
    year: int
    month: int
    day: int
    hour: int = 12
    minute: int = 0
    gender: str = "女"          # 女 / 男
    calendar: str = "solar"     # solar / lunar
    name: str = ""
    longitude: float | None = None   # 给了就换算真太阳时

    def to_datetime(self) -> datetime:
        return datetime(self.year, self.month, self.day, self.hour, self.minute)


def _wuxing_power(day_gan: str, bazi: list[str]) -> dict:
    """五行力量：天干各 1.0；地支按藏干 1 / 0.5 / 0.25，月支再 ×1.5。"""
    power = {w: 0.0 for w in WUXING_ORDER}
    for i, ganzhi in enumerate(bazi):
        gan, zhi = ganzhi[0], ganzhi[1]
        power[GAN_WUXING[gan]] += 1.0
        mult = 1.5 if i == 1 else 1.0
        for k, hide in enumerate(ZHI_HIDE_GAN[zhi]):
            weight = HIDE_WEIGHTS[k] if k < len(HIDE_WEIGHTS) else 0.1
            power[GAN_WUXING[hide]] += weight * mult
    total = sum(power.values()) or 1.0
    pct = {w: round(v / total * 100, 1) for w, v in power.items()}
    return {"raw": {w: round(v, 2) for w, v in power.items()}, "pct": pct}


def _strength(day_gan: str, bazi: list[str], power: dict, month_zhi: str) -> dict:
    """日主强弱（简化模型）：同党=比劫+印，异党=食伤+财+官杀，再看得令/得地。"""
    me = GAN_WUXING[day_gan]
    sheng_me = next(w for w, s in SHENG.items() if s == me)
    raw = power["raw"]
    same = raw[me] + raw[sheng_me]
    other = sum(v for w, v in raw.items() if w not in (me, sheng_me))
    score = same / (same + other or 1.0)

    month_wx = ZHI_WUXING[month_zhi]
    if month_wx == me or SHENG[month_wx] == me:
        score += 0.05
    else:
        score -= 0.05
    roots = sum(
        1 for z in bazi
        if GAN_WUXING[ZHI_HIDE_GAN[z[1]][0]] in (me, sheng_me)
    )
    if roots >= 2:
        score += 0.03
    elif roots == 0:
        score -= 0.05
    score = max(0.05, min(0.95, score))
    if score >= 0.60:
        label = "偏强"
    elif score <= 0.42:
        label = "偏弱"
    else:
        label = "中和"
    return {"score": round(score, 3), "label": label, "roots": roots}


def _pattern(day_gan: str, month_zhi: str) -> str:
    """月令本气取格（简化）。"""
    main_qi = ZHI_HIDE_GAN[month_zhi][0]
    name = shi_shen(day_gan, main_qi)
    if name in ("比肩",):
        return "建禄格"
    if name in ("劫财",):
        return "月刃格"
    return name + "格"


def liunian_year(now: datetime | None = None) -> int:
    """命理上的「今年」：**立春前仍算上一个干支年**。

    公历 1 月 1 日到立春（2 月 3—5 日）之间，八字用的还是上一年干支；
    直接拿 ``datetime.now().year`` 会把流年和大运都提前半个月切换。
    """
    now = now or datetime.now()
    lunar_now = Solar.fromYmdHms(
        now.year, now.month, now.day, 12, 0, 0).getLunar()
    gz_now = lunar_now.getYearInGanZhiByLiChun()
    if Solar.fromYmd(now.year, 7, 1).getLunar().getYearInGanZhiByLiChun() != gz_now:
        return now.year - 1
    return now.year


def build_chart(birth: Birth, now: datetime | None = None) -> dict:
    """排盘。返回可直接 JSON 化的 dict。

    ``now`` 只影响「当前大运/流年」的判定，默认取本机当前时间；
    测试时可传入指定日期（例如立春前后）来验证年份切换。
    """
    dt = birth.to_datetime()
    solar_note = ""
    if birth.longitude is not None:
        adjusted = true_solar_time(dt, birth.longitude)
        solar_note = (
            f"真太阳时 {adjusted:%H:%M}（经度 {birth.longitude:.1f}°，"
            f"北京时 {dt:%H:%M}）"
        )
        dt = adjusted

    if birth.calendar == "lunar":
        lunar = Lunar.fromYmdHms(
            birth.year, birth.month, birth.day, dt.hour, dt.minute, 0
        )
        solar = lunar.getSolar()
    else:
        solar = Solar.fromYmdHms(
            dt.year, dt.month, dt.day, dt.hour, dt.minute, 0
        )
        lunar = solar.getLunar()

    ec = lunar.getEightChar()
    bazi = [ec.getYear(), ec.getMonth(), ec.getDay(), ec.getTime()]
    branches = [b[1] for b in bazi]
    day_gan = bazi[2][0]

    hide_gan = [ec.getYearHideGan(), ec.getMonthHideGan(),
                ec.getDayHideGan(), ec.getTimeHideGan()]
    shishen_gan = [ec.getYearShiShenGan(), ec.getMonthShiShenGan(),
                   "日主", ec.getTimeShiShenGan()]
    shishen_zhi = [ec.getYearShiShenZhi(), ec.getMonthShiShenZhi(),
                   ec.getDayShiShenZhi(), ec.getTimeShiShenZhi()]

    power = _wuxing_power(day_gan, bazi)
    strength = _strength(day_gan, bazi, power, branches[1])

    # 十神统计（天干 + 地支本气），用来判断「缺什么」
    counts: dict[str, int] = {}
    for name in shishen_gan:
        if name and name != "日主":
            counts[shi_shen_group(name)] = counts.get(shi_shen_group(name), 0) + 1
    for names in shishen_zhi:
        if names:
            g = shi_shen_group(names[0])
            counts[g] = counts.get(g, 0) + 1

    xunkong = [ec.getDayXunKong(), ec.getTimeXunKong()]
    shensha = _shensha(day_gan, branches[0], branches[2], branches, xunkong)

    # 大运
    yun = ec.getYun(1 if birth.gender == "男" else 0)
    dayun = []
    for d in yun.getDaYun():
        if not d.getGanZhi():
            continue
        dayun.append({
            "start_year": d.getStartYear(),
            "start_age": d.getStartAge(),
            "ganzhi": d.getGanZhi(),
            "shishen": shi_shen(day_gan, d.getGanZhi()[0]),
            "xunkong": d.getXunKong(),
        })

    now_year = liunian_year(now)
    current_dayun = next(
        (d for d in dayun if d["start_year"] <= now_year < d["start_year"] + 10),
        dayun[-1] if dayun else None,
    )
    liunian = []
    for year in range(now_year, now_year + 4):
        gz = Solar.fromYmd(year, 7, 1).getLunar().getYearInGanZhiByLiChun()
        liunian.append({
            "year": year,
            "ganzhi": gz,
            "shishen": shi_shen(day_gan, gz[0]),
            "chong_day_zhi": CHONG.get(branches[2]) == gz[1],
            "he_day_zhi": HE6.get(branches[2]) == gz[1],
        })

    solar_text = (
        f"{solar.getYear():04d}-{solar.getMonth():02d}-{solar.getDay():02d} "
        f"{solar.getHour():02d}:{solar.getMinute():02d}"
    )

    chart = {
        "birth": {
            "solar": solar_text,
            "lunar": f"{lunar.getYearInChinese()}年{lunar.getMonthInChinese()}月"
                     f"{lunar.getDayInChinese()} {lunar.getTimeZhi()}时",
            "gender": birth.gender,
            "zodiac": lunar.getYearShengXiao(),
            "note": solar_note,
        },
        "bazi": bazi,
        "day_gan": day_gan,
        "day_master": f"{day_gan}{GAN_WUXING[day_gan]}",
        "hide_gan": hide_gan,
        "shishen_gan": shishen_gan,
        "shishen_zhi": shishen_zhi,
        "nayin": [ec.getYearNaYin(), ec.getMonthNaYin(),
                  ec.getDayNaYin(), ec.getTimeNaYin()],
        "dishi": [ec.getYearDiShi(), ec.getMonthDiShi(),
                  ec.getDayDiShi(), ec.getTimeDiShi()],
        "xunkong": xunkong,
        "taiyuan": ec.getTaiYuan(),
        "minggong": ec.getMingGong(),
        "shengong": ec.getShenGong(),
        "wuxing": power,
        "strength": strength,
        "pattern": _pattern(day_gan, branches[1]),
        "shishen_count": counts,
        "shensha": shensha,
        "relations": _relations(branches),
        "dayun": dayun,
        "current_dayun": current_dayun,
        "liunian": liunian,
        "jieqi": {
            "prev": lunar.getPrevJieQi().getName(),
            "next": lunar.getNextJieQi().getName(),
        },
        "huangli": {
            "yi": lunar.getDayYi()[:10],
            "ji": lunar.getDayJi()[:10],
            "chong": lunar.getDayChongDesc(),
            "sha": lunar.getDaySha(),
            "tianshen": lunar.getDayTianShen(),
            "tianshen_type": lunar.getDayTianShenType(),
            "xiu": f"{lunar.getXiu()}{lunar.getXiuLuck()}",
        },
    }
    return chart


def chart_brief(chart: dict) -> str:
    """群聊里先给用户看的两行盘面，方便他核对生日有没有报错。"""
    b = chart["birth"]
    bazi = " ".join(chart["bazi"])
    line1 = f"生日：{b['solar']}（{b['lunar']}·{b['gender']}）"
    line2 = f"四柱：{bazi}　日主：{chart['day_master']}（{chart['strength']['label']}）"
    return line1 + "\n" + line2


def today_huangli(dt: datetime | None = None) -> dict:
    """今日黄历（不需要生日，群里最好用的一条）。"""
    dt = dt or datetime.now()
    solar = Solar.fromYmdHms(dt.year, dt.month, dt.day, 12, 0, 0)
    lunar = solar.getLunar()
    return {
        "solar": f"{dt:%Y-%m-%d}",
        "lunar": f"{lunar.getYearInChinese()}年{lunar.getMonthInChinese()}月"
                 f"{lunar.getDayInChinese()}",
        "ganzhi": f"{lunar.getYearInGanZhi()}年 {lunar.getMonthInGanZhi()}月 "
                  f"{lunar.getDayInGanZhi()}日",
        "zodiac": lunar.getYearShengXiao(),
        "yi": lunar.getDayYi()[:8],
        "ji": lunar.getDayJi()[:8],
        "chong": lunar.getDayChongDesc(),
        "sha": lunar.getDaySha(),
        "tianshen": lunar.getDayTianShen(),
        "tianshen_type": lunar.getDayTianShenType(),
        "xiu": f"{lunar.getXiu()}{lunar.getXiuLuck()}",
        "jieqi": f"{lunar.getPrevJieQi().getName()}后"
                 f"{lunar.getNextJieQi().getName()}前",
    }
