"""梅花易数：以时间或报数起卦，看体用生克。

规则（先天卦数，邵雍一脉的通行起法）：
    上卦 = (年支数 + 月 + 日) % 8       0 作 8
    下卦 = (年支数 + 月 + 日 + 时支数) % 8
    动爻 = (年支数 + 月 + 日 + 时支数) % 6   0 作 6
    体用：动爻所在之卦为「用」，另一卦为「体」；断吉凶看体用五行生克。

报数起卦同理：两个数分别取上下卦，两数之和取动爻。
"""

from __future__ import annotations

from datetime import datetime

from lunar_python import Solar

from .gua import TRIGRAMS, hexagram_of

#: 先天八卦数
XIANTIAN = {1: "乾", 2: "兑", 3: "离", 4: "震", 5: "巽", 6: "坎", 7: "艮", 8: "坤"}

#: 八卦五行
TRIGRAM_WUXING = {"乾": "金", "兑": "金", "离": "火", "震": "木",
                  "巽": "木", "坎": "水", "艮": "土", "坤": "土"}
SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

ZHI_NUM = {z: i + 1 for i, z in enumerate("子丑寅卯辰巳午未申酉戌亥")}


def _relation(ti_wx: str, yong_wx: str) -> tuple[str, str, str]:
    """返回 (关系, 吉凶, 白话)。"""
    if ti_wx == yong_wx:
        return "体用比和", "吉", "同气相求，事情顺，不用太费劲"
    if SHENG[yong_wx] == ti_wx:
        return "用生体", "大吉", "外面有助力，事情自己往你这边走"
    if SHENG[ti_wx] == yong_wx:
        return "体生用", "偏耗", "你在往外掏，成是能成，但要花力气、花人情"
    if KE[ti_wx] == yong_wx:
        return "体克用", "小吉", "压得住，但要你主动去推，坐等不会来"
    return "用克体", "凶", "被对方压着，硬推容易吃亏，宜缓不宜急"


def cast_time(dt: datetime | None = None, question: str = "") -> dict:
    dt = dt or datetime.now()
    lunar = Solar.fromYmdHms(dt.year, dt.month, dt.day, dt.hour, 0, 0).getLunar()
    year_zhi = lunar.getYearInGanZhiByLiChun()[1]
    hour_zhi = lunar.getTimeZhi()
    y = ZHI_NUM[year_zhi]
    m = abs(lunar.getMonth())
    d = lunar.getDay()
    h = ZHI_NUM[hour_zhi]

    up = (y + m + d) % 8 or 8
    down = (y + m + d + h) % 8 or 8
    move = (y + m + d + h) % 6 or 6
    source = f"{dt:%Y-%m-%d %H:%M}（{lunar.getMonthInChinese()}月{lunar.getDayInChinese()} {hour_zhi}时）"
    return _assemble(source, up, down, move, question)


def cast_numbers(a: int, b: int, question: str = "") -> dict:
    up = a % 8 or 8
    down = b % 8 or 8
    move = (a + b) % 6 or 6
    return _assemble(f"报数 {a}、{b}", up, down, move, question)


def _assemble(source: str, up: int, down: int, move: int, question: str) -> dict:
    upper = XIANTIAN[up]
    lower = XIANTIAN[down]
    binary = TRIGRAMS[lower] + TRIGRAMS[upper]     # 自下而上

    changed_bits = list(binary)
    changed_bits[move - 1] = "1" if changed_bits[move - 1] == "0" else "0"
    changed = "".join(changed_bits)

    # 互卦：下互取 2-4 爻，上互取 3-5 爻
    hu_lower = binary[1:4]
    hu_upper = binary[2:5]
    hu = hu_lower + hu_upper

    # 体用：动爻在下卦则下为用，反之上为用
    yong_is_lower = move <= 3
    ti_name = upper if yong_is_lower else lower
    yong_name = lower if yong_is_lower else upper
    ti_wx, yong_wx = TRIGRAM_WUXING[ti_name], TRIGRAM_WUXING[yong_name]
    rel, luck, rel_text = _relation(ti_wx, yong_wx)

    return {
        "source": source,
        "question": question,
        "upper": upper,
        "lower": lower,
        "move": move,
        "original": hexagram_of(binary),
        "changed": hexagram_of(changed),
        "hu": hexagram_of(hu),
        "ti": ti_name,
        "yong": yong_name,
        "ti_wuxing": ti_wx,
        "yong_wuxing": yong_wx,
        "relation": rel,
        "luck": luck,
        "relation_text": rel_text,
    }


def cast_text(r: dict) -> str:
    bits = ["初", "二", "三", "四", "五", "上"]
    return (
        f"{r['source']}起卦\n"
        f"本卦：{r['original']['name']}（上{r['upper']}下{r['lower']}，"
        f"{r['original']['keyword']}）\n"
        f"互卦：{r['hu']['name']}　变卦：{r['changed']['name']}（第{r['move']}爻动）\n"
        f"体卦：{r['ti']}（{r['ti_wuxing']}）　用卦：{r['yong']}（{r['yong_wuxing']}）\n"
        f"体用关系：{r['relation']}·{r['luck']}——{r['relation_text']}"
    )
