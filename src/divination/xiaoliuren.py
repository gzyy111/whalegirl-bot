"""小六壬：不用生辰，掐指一算的那种。

起课：从「大安」起正月，顺数到生月；再从该宫起初一，顺数到生日；
再从该宫起子时，顺数到生时。落到哪一宫，就看哪一宫。

也可以报三个数字起课（问事时随手想三个数），同一套取模。
"""

from __future__ import annotations

from datetime import datetime

from lunar_python import Solar

#: 六神顺序（掌诀位置固定，不能改）
PALACES = ("大安", "留连", "速喜", "赤口", "小吉", "空亡")

#: 每宫的属性与白话断语
INFO = {
    "大安": {
        "wuxing": "木", "shen": "青龙", "dir": "东方",
        "luck": "吉", "tone": "稳",
        "good": "事情平稳，按原计划走就行，慢一点反而稳",
        "bad": "容易拖成温水，太安逸会错过时机",
        "ask": "问事多成，但来得慢；问人，人在原处没动",
    },
    "留连": {
        "wuxing": "土", "shen": "玄武", "dir": "北方",
        "luck": "偏凶", "tone": "缠",
        "good": "拖得住，适合守成、等消息",
        "bad": "纠缠不清、来回反复，越急越乱",
        "ask": "事情卡在中间，短时间难有结果；问人，人多半在犹豫",
    },
    "速喜": {
        "wuxing": "火", "shen": "朱雀", "dir": "南方",
        "luck": "吉", "tone": "快",
        "good": "来得快，消息、回音、机会都在路上",
        "bad": "快而不稳，容易乐过头",
        "ask": "问事有信，三天内多半有回音；问人，人正在行动",
    },
    "赤口": {
        "wuxing": "金", "shen": "白虎", "dir": "西方",
        "luck": "凶", "tone": "争",
        "good": "该说清楚的时候，正好把话摊开",
        "bad": "口舌、争执、被人挑刺，签字画押要留神",
        "ask": "问事多阻，容易起争执；问人，人心里有气",
    },
    "小吉": {
        "wuxing": "水", "shen": "六合", "dir": "东方",
        "luck": "吉", "tone": "和",
        "good": "有人帮、有转机，合作、求人都顺",
        "bad": "吉而不大，别指望一步到位",
        "ask": "问事有贵人，走人情比走流程快；问人，人愿意帮",
    },
    "空亡": {
        "wuxing": "土", "shen": "勾陈", "dir": "中央",
        "luck": "凶", "tone": "空",
        "good": "适合放下、清空、及时止损",
        "bad": "谋事落空、消息不实，别把话说满",
        "ask": "问事多半没着落，或者结果和想的不一样；问人，人可能已经不在局里",
    },
}


def _shichen_index(hour: int) -> int:
    """小时 → 时辰序号（子=1 … 亥=12）。23 点算子时。"""
    return ((hour + 1) // 2) % 12 + 1


def cast(
    month: int,
    day: int,
    hour: int,
    numbers: tuple[int, int, int] | None = None,
) -> dict:
    """起课。numbers 给了就用报数法，否则用农历月日时。"""
    if numbers:
        a, b, c = numbers
        m_idx = (a - 1) % 6
        d_idx = (m_idx + b - 1) % 6
        h_idx = (d_idx + c - 1) % 6
        source = f"报数 {a}、{b}、{c}"
    else:
        m_idx = (month - 1) % 6
        d_idx = (m_idx + day - 1) % 6
        h_idx = (d_idx + _shichen_index(hour) - 1) % 6
        source = f"农历{month}月{day}日 {_shichen_list()[_shichen_index(hour) - 1]}时"

    place = PALACES[h_idx]
    info = INFO[place]
    return {
        "palace": place,
        "index": h_idx,
        "source": source,
        "month_index": m_idx,
        "day_index": d_idx,
        **info,
    }


def _shichen_list() -> list[str]:
    return ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def cast_now(dt: datetime | None = None) -> dict:
    """按当前农历月日时起课。"""
    dt = dt or datetime.now()
    lunar = Solar.fromYmdHms(dt.year, dt.month, dt.day, dt.hour, 0, 0).getLunar()
    return cast(abs(lunar.getMonth()), lunar.getDay(), dt.hour)


def cast_text(result: dict) -> str:
    return (
        f"起课：{result['source']}　落宫：{result['palace']}（{result['luck']}）\n"
        f"五行属{result['wuxing']}，{result['shen']}，{result['dir']}\n"
        f"利：{result['good']}\n"
        f"忌：{result['bad']}\n"
        f"断：{result['ask']}"
    )
