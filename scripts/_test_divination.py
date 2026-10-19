"""算命子系统烟测：不连微信、不调 AI（加 --ai 才真调一次）。

用法：
    .venv\\Scripts\\python.exe _test_divination.py
    .venv\\Scripts\\python.exe _test_divination.py --ai
"""

from __future__ import annotations

import sys
from datetime import datetime

from src.divination import bazi, gua, reader, rules
from src.divination_service import DivinationService


def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def test_parse() -> None:
    section("1. 意图与出生信息解析")
    svc = DivinationService(ai=None)
    cases = [
        ("算命 1998年5月12日 14:30 女 看看事业", "bazi"),
        ("1998-05-12 下午两点半 女", None),
        ("农历1998年四月十七 未时 女", "bazi"),   # 完整生辰可以不带触发词
        ("算命 2000年3月8日 早上8点 男", "bazi"),
        ("@大肥鱼 算个命", "bazi_ask"),            # 2026-10-19 真实漏触发的那句
        ("帮我看看八字", "bazi_ask"),
        ("排个盘", "bazi_ask"),
        ("起一卦 最近要不要换工作", "gua"),
        ("起个卦 要不要跳槽", "gua"),
        ("抽个签", "gua"),
        ("今日运势", "today"),
        ("你会算命吗", "help"),
        ("算命", "bazi_ask"),
        ("算命都是骗人的", None),
        ("今天天气不错", None),
        # 2026-10-19 新增的三种术数
        ("紫微 1999年7月1日 17:00 男", "ziwei"),
        ("看紫微斗数", "bazi_ask"),
        ("小六壬 问工作", "xlr"),
        ("掐指一算 要不要出门", "xlr"),
        ("梅花易数 问考编", "meihua"),
        ("梅花易数 3 7 问搬家", "meihua"),
        ("塔罗 问感情", "tarot"),
        ("塔罗一张 问工作", "tarot"),
    ]
    for text, expect in cases:
        it = svc.resolve(text)
        got = it.kind if it else None
        flag = "OK" if got == expect else "MISMATCH"
        info = ""
        if it and it.birth:
            b = it.birth
            info = f" {b.year}-{b.month}-{b.day} {b.hour:02d}:{b.minute:02d} {b.gender} {b.calendar}"
        print(f"[{flag}] {text!r:<42} -> {got}{info}  问：{it.question if it else ''}")

    # 补生日（挂起）
    svc.set_pending("room1", "看看今年财运")
    p = svc.get_pending("room1")
    it = svc.pending_intent("2001年8月3日 凌晨1点 男", p)
    print("[补生日]", it.kind if it else None, it.birth if it else None,
          "接回问题：", it.question if it else "")

    # 只报了日期、缺时辰和性别 → 先问，别拿默认值硬排
    it = svc.resolve("算命 1999年7月1日 看看工作")
    print("[缺两项]", it.kind, getattr(it.birth, "year", None))
    svc.set_pending("room2", it.question, birth=it.birth, flags=it.flags)
    p2 = svc.get_pending("room2")
    it2 = svc.pending_intent("女 晚上8点", p2)
    print("[补齐]", it2.kind, it2.birth.hour, it2.birth.gender,
          "问：", it2.question)


def test_chart() -> None:
    section("2. 八字排盘（应等于 lunar-python 直接算的结果）")
    b = bazi.Birth(1998, 5, 12, 14, 30, "女")
    chart = bazi.build_chart(b)
    print(bazi.chart_brief(chart))
    print("五行:", chart["wuxing"]["pct"])
    print("十神干:", chart["shishen_gan"], "十神支:", chart["shishen_zhi"])
    print("神煞:", [(s["name"], s["pos"]) for s in chart["shensha"]])
    print("关系:", chart["relations"] or "无")
    print("当前大运:", chart["current_dayun"])
    print("流年:", chart["liunian"][:2])
    print("黄历:", chart["huangli"]["yi"], "/", chart["huangli"]["ji"])
    assert chart["bazi"] == ["戊寅", "丁巳", "己未", "辛未"], chart["bazi"]

    # 农历输入应与公历同一天
    b2 = bazi.Birth(1998, 4, 17, 14, 30, "女", calendar="lunar")
    chart2 = bazi.build_chart(b2)
    assert chart2["bazi"] == chart["bazi"], chart2["bazi"]

    # 真太阳时：乌鲁木齐经度会让时柱变化
    b3 = bazi.Birth(1998, 5, 12, 14, 30, "女", longitude=87.6)
    chart3 = bazi.build_chart(b3)
    print("真太阳时:", chart3["birth"]["note"], "->", chart3["bazi"])


def test_rules() -> None:
    section("3. 断命规则")
    chart = bazi.build_chart(bazi.Birth(1998, 5, 12, 14, 30, "女"))
    analysis = rules.analyze(chart)
    print(analysis["summary"])
    for f in analysis["facts"]:
        print(f"  [{f['area']}] {f['conclusion']}  <{f['basis']}>")
    print("\n给模型的要点文本：\n" + rules.facts_text(analysis)[:600])


def test_gua() -> None:
    section("4. 起卦与 64 卦数据")
    binaries = set()
    for num, name, upper, lower, kw, mean in gua.HEXAGRAMS:
        key = gua.TRIGRAMS[lower] + gua.TRIGRAMS[upper]
        assert key not in binaries, f"重复卦象 {name}"
        binaries.add(key)
    assert len(binaries) == 64, len(binaries)
    print("64 卦二进制映射唯一性：OK")

    for seed in (1, 7, 42):
        r = gua.cast(seed=seed)
        print(f"\nseed={seed}")
        print(gua.cast_text(r))
        print("六爻:", r["detail"])


def test_prompt() -> None:
    section("5. 解读提示词（不调用模型，只看拼装结果）")
    chart = bazi.build_chart(bazi.Birth(1998, 5, 12, 14, 30, "女"))
    analysis = rules.analyze(chart)
    system, user = reader.build_bazi_reading(chart, analysis, "看看今年事业")
    print("[system 前 200 字]\n" + system[:200] + " ...")
    print("\n[user]\n" + user)


def test_ai() -> None:
    section("6. 真调一次大模型（--ai）")
    import asyncio

    from loguru import logger

    logger.remove()

    async def run():
        # 延迟导入，避免没配 key 时影响其它用例
        from src.ai_engine import AIEngine
        from src.config_manager import ConfigManager
        from src.style_manager import StyleManager

        cfg = ConfigManager("config.yaml")
        data = cfg.load()
        ai_cfg = data.get("ai", {})
        key = cfg.get_api_key() if hasattr(cfg, "get_api_key") else ""
        ai = AIEngine(
            api_key=key,
            api_url=ai_cfg.get("api_url", ""),
            model=ai_cfg.get("model", "deepseek-chat"),
            api_style=ai_cfg.get("api_style", "auto"),
            temperature=0.88,
            max_tokens=700,
            timeout=60,
        )
        style = StyleManager(
            styles_dir="./styles",
            custom_dir="./styles/custom",
            locked_name="tsundere_whale_girl",
        )
        style.load_all()
        svc = DivinationService(ai=ai, persona=style.get_active().system_prompt)
        it = svc.resolve("算命 1998年5月12日 14:30 女 看看今年事业")
        out = await svc.answer(it)
        print(out)
        await ai.close()

    asyncio.run(run())


def test_new_methods() -> None:
    section("6. 紫微 / 小六壬 / 梅花")
    from src.divination import meihua, xiaoliuren as xlr, ziwei

    ch = ziwei.build_chart(bazi.Birth(1999, 7, 1, 17, 0, "男"))
    print(ziwei.chart_brief(ch))
    assert ch["soul_palace"], "命宫没排出来"
    assert len(ch["palaces"]) == 12, len(ch["palaces"])
    cur = ch["current_decadal"]
    print("当前大限:", cur["name"] if cur else "未定位")

    for m, d, h in ((9, 10, 12), (1, 1, 0), (5, 18, 17)):
        r = xlr.cast(m, d, h)
        print(f"小六壬 {m}月{d}日{h}时 -> {r['palace']}（{r['luck']}）")

    r = meihua.cast_numbers(3, 7)
    print(meihua.cast_text(r))

    from src.divination import tarot

    print("牌库:", len(tarot.deck()), "张")
    assert len(tarot.deck()) == 78
    assert len({c["id"] for c in tarot.deck()}) == 78
    three = tarot.cast(3, seed=42)
    assert len(three["drawn"]) == 3
    assert len({d["card"]["id"] for d in three["drawn"]}) == 3, "抽到重复牌"
    print(tarot.cast_text(three))
    one = tarot.cast(1, seed=7)
    print(tarot.cast_text(one))


if __name__ == "__main__":
    started = datetime.now()
    test_parse()
    test_chart()
    test_rules()
    test_gua()
    test_new_methods()
    test_prompt()
    if "--ai" in sys.argv:
        test_ai()
    print(f"\n全部用例跑完，用时 {(datetime.now() - started).total_seconds():.1f}s")
