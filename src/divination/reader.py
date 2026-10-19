"""解读器：把盘面事实交给大模型，用鲸鱼娘的口吻说出来。

分工很清楚：
* bazi.py 算盘（确定性，可核对）
* rules.py 断事（规则可审计，每条带依据）
* 本文件只负责「组织语言」——模型不许发明盘上没有的东西

这也是这一版最重要的约束：**排盘不是模型算的**。模型只做翻译，
所以它算错历法、编造星曜、把大运说反这类问题从根上被掐掉了。
"""

from __future__ import annotations

from datetime import datetime

from .bazi import chart_brief
from .gua import cast_text
from .rules import facts_text

FORTUNE_MODE = """
你现在切到「深海命理」模式：一只会用传统术数给人看盘的鲸鱼娘
（子平八字、紫微斗数、小六壬、梅花易数、易经起卦、塔罗）。
语气仍然是你原本的傲娇、嘴硬心软、爱白米饭的样子，称呼对方「主人」，
但这次是认真办事，不要嬉皮笑脸，也不要客服腔。

硬规则：
1. 只依据下面给出的【盘面】和【命理要点】来断，不许自己发明格局、神煞、星曜；
   盘上没有的东西，就说「这个盘看不出来」。
2. 每条结论要让人看出依据（比如「因为你日主偏弱、月令又是伤官」），
   但用大白话说，不掉书袋、不背口诀。
3. 不吓人：绝不说死亡、绝症、灾祸、必离婚、必破产、血光之灾这类话；
   不确定就说不确定，把话说成「倾向」「容易」「留意」。
4. 不碰医疗诊断、投资建议、法律结论、生死吉凶的具体日期；
   被问到就直说「这个本鲸鱼娘不看」。
5. 不要自己推算盘外的合冲：什么支和什么支三合、相冲，以【盘面】里给出的为准，
   不要看到两个支就自己拼一个三合局出来。
6. 不排版成 Markdown：不用 #、*、-、表格。用自然段，段间空一行。
7. 字数 300—450 字，不要写成论文。
8. 最后一句用你自己的口吻点一下：这只是个参考，日子还是主人自己过。
"""


def _persona(base: str) -> str:
    base = (base or "").strip()
    if not base:
        base = "你是微信群里的「鲸鱼娘」，傲娇、嘴硬心软，称呼对方为主人。"
    return base + "\n" + FORTUNE_MODE


def build_bazi_reading(
    chart: dict,
    analysis: dict,
    question: str,
    persona: str = "",
) -> tuple[str, str]:
    pct = chart["wuxing"]["pct"]
    wuxing_text = "　".join(f"{w}{pct[w]}%" for w in pct)
    shensha_text = "、".join(
        "{}（{}）".format(s["name"], s["pos"]) for s in chart["shensha"]
    ) or "无"
    system = _persona(persona)
    user = (
        f"【今天】{datetime.now():%Y-%m-%d}\n"
        "【盘面】\n"
        f"{chart_brief(chart)}\n"
        f"五行：{wuxing_text}\n"
        f"格局：{chart['pattern']}；喜用：{'、'.join(analysis['favor']['favor'])}；"
        f"忌：{'、'.join(analysis['favor']['avoid'])}\n"
        f"神煞：{shensha_text}\n\n"
        "【命理要点（只能照这些断）】\n"
        f"{facts_text(analysis)}\n\n"
        f"【主人问的是】\n{question or '没具体问，就整体看看'}\n\n"
        "【输出要求】\n"
        "第一段一句话总评（日主+格局+整体气象）；第二段性格；"
        "第三段事业财运；第四段感情；第五段这步大运和今年；"
        "最后一句叮嘱。段落之间空一行，不要小标题。"
    )
    return system, user


def build_gua_reading(result: dict, question: str, persona: str = "") -> tuple[str, str]:
    system = _persona(persona)
    user = (
        "【卦象】\n"
        f"{cast_text(result)}\n\n"
        f"【主人问的是】\n{question or '没具体说问什么，就看看眼下整体'}\n\n"
        "【输出要求】\n"
        "先说清楚起出来的是什么卦（本卦、有没有动爻、变卦），再解这卦对着他的问题是什么意思，"
        "给一句眼下可以做的事。200—320 字。不要说「卦辞曰」这种背书的口气。"
    )
    return system, user


def build_today_reading(huangli: dict, question: str, persona: str = "") -> tuple[str, str]:
    system = _persona(persona)
    yi = "、".join(huangli["yi"]) or "无"
    ji = "、".join(huangli["ji"]) or "无"
    user = (
        "【今日黄历】\n"
        f"{huangli['solar']}（{huangli['lunar']}）{huangli['ganzhi']}\n"
        f"宜：{yi}\n忌：{ji}\n"
        f"冲：{huangli['chong']}　煞：{huangli['sha']}\n"
        f"值神：{huangli['tianshen']}（{huangli['tianshen_type']}）　星宿：{huangli['xiu']}\n"
        f"节气：{huangli['jieqi']}\n\n"
        f"【主人问的是】\n{question or '今天运势怎么样'}\n\n"
        "【输出要求】\n"
        "不用把黄历条目全念一遍。挑两三条对今天最有用的说，再给一句今天适合做什么、"
        "别做什么。180—260 字。"
    )
    return system, user


def build_ziwei_reading(chart: dict, question: str, persona: str = "") -> tuple[str, str]:
    from .ziwei import chart_text

    system = _persona(persona)
    decadal = chart.get("current_decadal") or {}
    decadal_line = (
        f"当前大限：{decadal.get('name', '')}（{decadal.get('decadal', ['?', '?'])[0]}-"
        f"{decadal.get('decadal', ['?', '?'])[1]}岁）"
        if decadal else "当前大限：未定位"
    )
    user = (
        f"【今天】{datetime.now():%Y-%m-%d}\n"
        "【紫微命盘】\n"
        f"{chart_text(chart)}\n"
        f"{decadal_line}\n\n"
        f"【主人问的是】\n{question or '没具体问，就整体看看'}\n\n"
        "【输出要求】\n"
        "先用命宫主星定性格底色，再讲三方四正（命/财帛/官禄/迁移）看事业与财，"
        "再看夫妻宫讲感情，最后说当前大限的主题。300—420 字，段落之间空一行。"
        "紫微只看星曜落宫与三方四正，不要自己编星曜、不要套八字的十神。"
    )
    return system, user


def build_xlr_reading(result: dict, question: str, persona: str = "") -> tuple[str, str]:
    from .xiaoliuren import cast_text

    system = _persona(persona)
    user = (
        f"【小六壬】\n{cast_text(result)}\n\n"
        f"【主人问的是】\n{question or '没具体说问什么，就看看眼下'}\n\n"
        "【输出要求】\n"
        "先说落到哪一宫、这一宫是什么性质，再对着他的问题断一句，"
        "给一条眼下能做的事。200—300 字。别把它说成宿命，也别吓人。"
    )
    return system, user


def build_meihua_reading(result: dict, question: str, persona: str = "") -> tuple[str, str]:
    from .meihua import cast_text

    system = _persona(persona)
    user = (
        f"【梅花易数】\n{cast_text(result)}\n\n"
        f"【主人问的是】\n{question or '没具体说问什么，就看看眼下'}\n\n"
        "【输出要求】\n"
        "先讲本卦是什么象、动在哪一爻、变卦往哪走，再用体用生克断吉凶和应对，"
        "互卦可以点一句。220—320 字。"
    )
    return system, user


def build_tarot_reading(result: dict, question: str, persona: str = "") -> tuple[str, str]:
    from .tarot import cast_text

    system = _persona(persona)
    single = len(result["drawn"]) == 1
    how = (
        "就这一张：先说牌名 + 正/逆位 + 一句牌意，再直接对着他的问题断，给一条能做的事。"
        "180—260 字。"
        if single else
        "先逐张说：位置 + 牌名 + 正/逆位 + 一句牌意；再把几张合起来回答他的问题；"
        "最后给一条眼下能做的事。250—380 字。"
    )
    user = (
        f"【塔罗】\n{cast_text(result)}\n\n"
        f"【主人问的是】\n{question or '没具体问，就看看眼下'}\n\n"
        f"【输出要求】\n{how}"
        "逆位不要一律说成坏，按牌义讲清「力量被堵住/内化/过头」这层意思。"
    )
    return system, user


async def ask(
    ai,
    system: str,
    user: str,
    max_tokens: int = 700,
    temperature: float = 0.85,
) -> str:
    """调一次大模型。失败返回空串，由调用方决定兜底。"""
    if ai is None:
        return ""
    messages = [{"role": "user", "content": user}]
    text = await ai.chat(
        messages,
        system_prompt=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (text or "").strip()
