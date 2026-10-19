"""紫微斗数排盘：iztro-py（SylarLong/iztro 的 Python 移植，MIT）。

和八字一样，这里只出**事实**：命宫/身宫、五行局、十二宫的主星辅星与庙旺、
生年四化、各宫大限。吉凶判断交给 reader 的提示词，不许模型自己编星曜。

iztro 的星名/宫名默认返回 i18n key（tianjiMaj、soulPalace），
这里用包内自带的 zh_CN 词表翻回中文。
"""

from __future__ import annotations

from datetime import datetime

from iztro_py import astro
from iztro_py.i18n.locales import zh_CN

from .bazi import Birth

_TR = zh_CN.translations


def _nm(key: str) -> str:
    """把 iztro 的 i18n key 翻成中文。"""
    if not key:
        return ""
    for group in ("major", "minor"):
        table = _TR["stars"][group]
        if key in table:
            return table[key]
    for section in ("palaces", "heavenlyStem", "earthlyBranch", "fiveElementsClass",
                    "brightness", "gender", "zodiac", "sign"):
        table = _TR.get(section) or {}
        if isinstance(table, dict) and key in table:
            return table[key]
    if key in _TR and isinstance(_TR[key], str):
        return _TR[key]
    return key


def _time_index(hour: int) -> int:
    """小时 → iztro 的时辰序号。0=早子，1..11=丑..亥，12=晚子。"""
    if hour == 23:
        return 12
    return (hour + 1) // 2


def build_chart(birth: Birth, now: datetime | None = None) -> dict:
    """排紫微盘。"""
    date = f"{birth.year}-{birth.month}-{birth.day}"
    gender = "男" if birth.gender == "男" else "女"
    chart = astro.by_solar(date, _time_index(birth.hour), gender, True, "zh-CN")

    palaces = []
    for p in chart.palaces:
        major = []
        for s in p.major_stars:
            item = {"name": _nm(s.name), "brightness": _nm(getattr(s, "brightness", ""))}
            mut = getattr(s, "mutagen", None)
            if mut:
                item["mutagen"] = _nm(mut)
            major.append(item)
        minor = []
        for s in p.minor_stars:
            item = {"name": _nm(s.name)}
            mut = getattr(s, "mutagen", None)
            if mut:
                item["mutagen"] = _nm(mut)
            minor.append(item)
        decadal = getattr(p.decadal, "range", None) or ()
        palaces.append({
            "name": _nm(p.name),
            "ganzhi": _nm(p.heavenly_stem) + _nm(p.earthly_branch),
            "major": major,
            "minor": minor,
            "is_body": bool(p.is_body_palace),
            "is_empty": bool(p.is_empty),
            "decadal": list(decadal),
            "changsheng": _nm(getattr(p, "changsheng12", "")),
        })

    soul_branch = _nm(chart.earthly_branch_of_soul_palace)
    body_branch = _nm(chart.earthly_branch_of_body_palace)
    soul_palace = next((p for p in palaces if p["ganzhi"][1:] == soul_branch), None)
    body_palace = next((p for p in palaces if p["is_body"]), None)

    # 三方四正：命、财、官、迁
    want = {"命宫", "财帛宫", "官禄宫", "迁移宫"}
    sanfang = [
        {"name": p["name"], "major": [m["name"] for m in p["major"]]}
        for p in palaces if p["name"] in want
    ]

    # 当前大限：按虚岁算，落在哪个宫的年龄段
    now = now or datetime.now()
    age = now.year - birth.year + 1
    current_decadal = next(
        (p for p in palaces if p["decadal"] and p["decadal"][0] <= age <= p["decadal"][1]),
        None,
    )

    return {
        "birth": {
            "solar": f"{birth.year:04d}-{birth.month:02d}-{birth.day:02d} "
                     f"{birth.hour:02d}:{birth.minute:02d}",
            "lunar": chart.lunar_date,
            "gender": gender,
            "zodiac": _nm(chart.zodiac),
            "sign": _nm(chart.sign),
            "time": _nm(chart.time),
        },
        "age": age,
        "current_decadal": current_decadal,
        "soul": _nm(chart.soul),
        "body": _nm(chart.body),
        "five_elements_class": _nm(chart.five_elements_class),
        "soul_palace": soul_palace,
        "body_palace": body_palace,
        "palaces": palaces,
        "sanfang": sanfang,
        "zhongzhou": [f"{p['name']}：{'、'.join(m['name'] for m in p['major']) or '空宫'}"
                      for p in palaces],
    }


def chart_brief(chart: dict) -> str:
    b = chart["birth"]
    soul = chart["soul_palace"] or {}
    majors = "、".join(m["name"] for m in soul.get("major", [])) or "空宫"
    body = chart["body_palace"] or {}
    return (
        f"生日：{b['solar']}（{b['lunar']}·{b['gender']}）　"
        f"命宫：{soul.get('ganzhi', '')}（{majors}）\n"
        f"身宫：{body.get('name', '')}　五行局：{chart['five_elements_class']}　"
        f"命主：{chart['soul']}　身主：{chart['body']}"
    )


def chart_text(chart: dict) -> str:
    """给模型的盘面事实（纯文本）。"""
    lines = [chart_brief(chart), "十二宫："]
    for p in chart["palaces"]:
        majors = "、".join(
            f"{m['name']}{('(' + m['brightness'] + ')') if m.get('brightness') else ''}"
            + (f"[化{m['mutagen']}]" if m.get("mutagen") else "")
            for m in p["major"]
        ) or "空宫"
        minors = "、".join(m["name"] for m in p["minor"])
        span = f"{p['decadal'][0]}-{p['decadal'][1]}岁" if p["decadal"] else ""
        tags = "身宫" if p["is_body"] else ""
        lines.append(
            f"- {p['name']}（{p['ganzhi']}）：{majors}"
            + (f"；辅：{minors}" if minors else "")
            + (f"；大限{span}" if span else "")
            + (f"；{tags}" if tags else "")
        )
    return "\n".join(lines)
