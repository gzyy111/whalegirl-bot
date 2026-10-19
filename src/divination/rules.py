"""断命规则层：把盘面翻译成「可复述的结论 + 依据」。

这一层是「算命」真正的手艺所在——盘是算出来的，吉凶是断出来的。
做法刻意保持「规则可审计」：

* 每一条结论都带 ``basis``（依据），LLM 只能拿这些结论去组织语言，
  不允许自己发明新的断语；
* 所有有流派分歧的地方（强弱、格局、喜用）统一标注「简化模型」；
* 断语库只写传统子平的常见口径，不写恐吓性、决定论式的内容。

输出给 reader.py 的 facts 形如::

    {"area": "事业", "conclusion": "适合靠一门手艺吃饭…", "basis": "月令伤官+日主偏弱"}
"""

from __future__ import annotations

from .bazi import (
    GAN_WUXING, KE, SHENG, WUXING_ORDER, ZHI_WUXING, shi_shen_group,
)

# ---------------------------------------------------------------- 描述库

WUXING_TRAIT = {
    "木": "仁厚、爱生长、做事讲章法，喜欢把事情往前推",
    "火": "热情、要面子、反应快，情绪来得快去得也快",
    "土": "稳、能扛事、慢热，重承诺但也容易死心眼",
    "金": "干脆、有原则、边界感强，说话直",
    "水": "脑子转得快、适应力强，心思深、不轻易交底",
}

WUXING_BODY = {
    "木": "肝胆、筋骨、眼睛",
    "火": "心脏、小肠、睡眠与情绪",
    "土": "脾胃、消化、体重",
    "金": "肺、呼吸道、皮肤",
    "水": "肾、膀胱、腰与耳",
}

SHISHEN_TRAIT = {
    "比肩": "自立、有主见，朋友缘不差，但不太服人管",
    "劫财": "好胜、敢抢，行动力强，钱容易大进大出",
    "食神": "性情温和、懂享受，有口福也有才艺",
    "伤官": "聪明外露、创意多，最受不了被按着头做事",
    "正财": "务实、算得清账，喜欢稳定的进项",
    "偏财": "会来事、路子广，机会型收入比死工资多",
    "正官": "守规矩、有责任感，适合有明确层级的地方",
    "七杀": "有魄力、抗压强，但也常把自己逼得太紧",
    "正印": "好学、心软，习惯依靠长辈或体系",
    "偏印": "直觉准、爱钻冷门，容易想太多、有点孤",
}

SHISHEN_CAREER = {
    "比劫": "合伙、销售、自己单干都行，但账要算清",
    "食伤": "靠表达、手艺、内容、技术吃饭最顺",
    "财": "对钱敏感，适合经营、采购、金融、生意",
    "官杀": "在体制、大公司、有规则的地方更能发挥",
    "印": "适合研究、教育、文书、专业资格路线",
}

SPOUSE_TRAIT = {
    "比肩": "像朋友多过像恋人，处得来但容易互不相让",
    "劫财": "性子烈、有主见，感情里争强的多",
    "食神": "温和顾家、会做饭，日子过得细",
    "伤官": "有趣、有才华，但也需要自己的空间",
    "正财": "务实、会过日子，重实际",
    "偏财": "大方、社交多，异性缘不差",
    "正官": "正派、有担当，可能偏传统",
    "七杀": "强势、有魄力，吵起来也凶",
    "正印": "照顾型、心软，可能年龄或阅历比你长",
    "偏印": "内敛、想法多，需要慢慢焐热",
}


# ---------------------------------------------------------------- 喜用


def _favor(chart: dict) -> dict:
    me = GAN_WUXING[chart["day_gan"]]
    sheng_me = next(w for w, s in SHENG.items() if s == me)
    wo_sheng = SHENG[me]
    wo_ke = KE[me]
    ke_me = next(w for w, s in KE.items() if s == me)
    pct = chart["wuxing"]["pct"]
    label = chart["strength"]["label"]
    month_zhi = chart["bazi"][1][1]

    if label == "偏弱":
        favor = [sheng_me, me]
        avoid = sorted([wo_ke, ke_me, wo_sheng], key=lambda w: -pct[w])[:2]
        why = "日主偏弱，喜印比帮身，忌财官食伤过度耗泄"
    elif label == "偏强":
        favor = [wo_sheng, wo_ke, ke_me]
        favor.sort(key=lambda w: pct[w])
        avoid = [sheng_me, me]
        why = "日主偏强，喜食伤泄秀、财官制衡，忌再添印比"
    else:
        favor = []
        avoid = [max(pct, key=pct.get)]
        why = "日主中和，以调候与通关为主"

    if month_zhi in "亥子丑" and "火" not in favor:
        favor.insert(0, "火")
        why += "；冬月生，调候优先取火"
    elif month_zhi in "巳午未" and "水" not in favor:
        favor.insert(0, "水")
        why += "；夏月生，调候优先取水"
    favor = [w for w in favor if w not in avoid][:3]
    if not favor:
        favor = [min(pct, key=pct.get)]
    return {"favor": favor, "avoid": avoid, "why": why}


# ---------------------------------------------------------------- 主分析


def analyze(chart: dict) -> dict:
    bazi = chart["bazi"]
    me = GAN_WUXING[chart["day_gan"]]
    sheng_me = next(w for w, s in SHENG.items() if s == me)
    pct = chart["wuxing"]["pct"]
    label = chart["strength"]["label"]
    counts = chart["shishen_count"]
    favor = _favor(chart)
    facts: list[dict] = []

    def add(area: str, conclusion: str, basis: str) -> None:
        facts.append({"area": area, "conclusion": conclusion, "basis": basis})

    # ---- 性格：日主五行 + 强弱 + 主星
    add(
        "性格",
        f"底子是{WUXING_TRAIT[me]}；{label}的盘，"
        + ("遇事先自己扛，不太愿意麻烦别人" if label == "偏强"
           else "遇事会先看别人脸色，习惯借力而不是硬顶" if label == "偏弱"
           else "刚柔都有，看场合切换"),
        f"日主{chart['day_master']}·{label}",
    )
    top_groups = sorted(counts.items(), key=lambda kv: -kv[1])
    if top_groups:
        g, n = top_groups[0]
        add(
            "性格",
            f"盘里{g}最旺（{n}处），所以「{SHISHEN_CAREER[g]}」这条线在你身上特别明显",
            f"十神统计：{g}×{n}",
        )

    # ---- 缺项
    missing = [g for g in ("比劫", "食伤", "财", "官杀", "印") if counts.get(g, 0) == 0]
    for g in missing:
        text = {
            "比劫": "兄弟朋友这块帮衬少，很多事得自己扛",
            "食伤": "不太爱表达，心里有货但懒得说给人听",
            "财": "对钱没那么执着，理财这块容易交给别人",
            "官杀": "不喜欢被管，规矩多的环境待不久",
            "印": "靠山和学历这条路不算顺，更吃实战经验",
        }[g]
        add("性格", text, f"十神缺{g}")

    # ---- 五行偏枯
    for w in WUXING_ORDER:
        if pct[w] >= 35:
            add("健康", f"{w}太旺（{pct[w]}%），{WUXING_BODY[w]}容易先出信号，别硬扛",
                f"五行{w}={pct[w]}%")
        elif pct[w] < 5:
            add("健康", f"{w}偏弱（{pct[w]}%），{WUXING_BODY[w]}是需要主动养的部分",
                f"五行{w}={pct[w]}%")

    # ---- 事业
    career_groups = [g for g in ("官杀", "食伤", "财", "印") if counts.get(g, 0) >= 2]
    if career_groups:
        add(
            "事业",
            "；".join(SHISHEN_CAREER[g] for g in career_groups[:2]),
            "十神≥2：" + "、".join(f"{g}×{counts[g]}" for g in career_groups[:2]),
        )
    if counts.get("官杀", 0) == 0:
        add("事业", "体制内熬资历不是你的路，自由度越高越出活", "官杀缺位")
    if counts.get("食伤", 0) >= 2 and counts.get("印", 0) == 0:
        add("事业", "适合靠作品和手艺说话，别指望证书给你兜底", "食伤旺而印缺")

    # ---- 财运
    if counts.get("财", 0) == 0:
        add("财运", "钱财观偏淡，适合把收入交给可信的人打理或设自动储蓄",
            "财星缺位")
    elif label == "偏强" and counts.get("财", 0) >= 2:
        add("财运", "身强能担财，敢投入也扛得住波动，但别上杠杆", "身强+财旺")
    elif label == "偏弱" and counts.get("财", 0) >= 2:
        add("财运", "财旺身弱，见钱快但守不住，先把现金流做厚", "身弱+财旺")

    # ---- 婚恋：夫妻宫（日支）
    day_zhi = bazi[2][1]
    spouse_ss = chart["shishen_zhi"][2]
    spouse_name = spouse_ss[0] if spouse_ss else ""
    if spouse_name:
        add("婚恋", f"另一半大概率是{SPOUSE_TRAIT[spouse_name]}的类型",
            f"夫妻宫{day_zhi}藏{spouse_name}")
    for rel in chart["relations"]:
        if "日柱" in rel and ("冲" in rel or "刑" in rel):
            add("婚恋", "感情里容易有一次明显的变动或拉扯，别在小事上较劲", f"夫妻宫{rel}")
            break
    if any(s["name"] == "桃花" for s in chart["shensha"]):
        add("婚恋", "异性缘不差，边界感要自己划清楚", "命中桃花")

    # ---- 神煞的具体用法
    ss_notes = {
        "天乙贵人": "关键节点常有人拉一把，遇事别硬撑，开口就有贵人",
        "文昌": "读书、考试、写东西这条线有天赋",
        "驿马": "动中求财，换城市、跑外勤反而顺",
        "华盖": "对玄学、艺术、独处有天然的兴趣",
        "羊刃": "冲劲足，但脾气上来容易过刚，注意收",
        "禄神": "衣食不愁，靠本事吃饭稳当",
        "红鸾": "近期或本命有喜事、姻缘的引子",
        "天喜": "喜事、添丁、好消息的引子",
        "空亡": "有的东西看着有、用起来打折，别把话说满",
    }
    seen = set()
    for s in chart["shensha"]:
        note = ss_notes.get(s["name"])
        if note and s["name"] not in seen:
            seen.add(s["name"])
            add("综合", note, f"{s['name']}在{s['pos']}")

    # ---- 大运
    dy = chart.get("current_dayun")
    if dy:
        gz = dy["ganzhi"]
        wxs = {GAN_WUXING[gz[0]], ZHI_WUXING[gz[1]]}
        good = bool(wxs & set(favor["favor"]))
        bad = bool(wxs & set(favor["avoid"]))
        if good and not bad:
            trend = "顺"
            text = "这步大运是帮你的，该做的决定可以往前推"
        elif bad and not good:
            trend = "逆"
            text = "这步大运偏耗，宜守成、少开新战场"
        else:
            trend = "平"
            text = "这步大运吉凶参半，成事靠具体选择，不靠运气"
        add("大运", f"{gz}运（{dy['start_year']}起）{text}", f"大运{dy['shishen']}·{trend}")

    # ---- 流年
    ln = chart["liunian"][0] if chart["liunian"] else None
    if ln:
        gz = ln["ganzhi"]
        wxs = {GAN_WUXING[gz[0]], ZHI_WUXING[gz[1]]}
        bits = []
        if wxs & set(favor["favor"]):
            bits.append("今年有帮扶")
        if wxs & set(favor["avoid"]):
            bits.append("今年有耗泄")
        if ln["chong_day_zhi"]:
            bits.append("冲夫妻宫，感情或住处容易动")
        if ln["he_day_zhi"]:
            bits.append("合夫妻宫，人缘与合作运不错")
        add("流年", f"{ln['year']}年{gz}：" + ("；".join(bits) or "平年，按部就班"),
            f"流年{ln['shishen']}")

    # ---- 关系
    for rel in chart["relations"][:3]:
        add("综合", f"{rel}——这块是盘里最需要留意的地方", rel)

    return {
        "favor": favor,
        "facts": facts,
        "summary": f"{chart['day_master']}{label}·{chart['pattern']}，"
                   f"喜{'、'.join(favor['favor'])}，忌{'、'.join(favor['avoid'])}",
    }


def facts_text(analysis: dict) -> str:
    """把 facts 压成给 LLM 的纯文本清单（编号，便于它引用）。"""
    lines = [f"[喜用] {analysis['summary']}"]
    for i, f in enumerate(analysis["facts"], 1):
        lines.append(f"{i}. [{f['area']}] {f['conclusion']}（依据：{f['basis']}）")
    return "\n".join(lines)
