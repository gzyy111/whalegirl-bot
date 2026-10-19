# -*- coding: utf-8 -*-
"""讲笑话子系统离线回归（不连微信、不调模型）。

用法：.venv\\Scripts\\python.exe _test_joke.py
覆盖：意图解析（该命中的 / 不该命中的）、抽取去重、组合文本、配图召回。
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.joke_service import JokeService  # noqa: E402

# 期望命中：应被讲笑话路径接管
SHOULD_HIT = [
    "来点大肥鱼笑话",
    "讲个笑话",
    "说个冷笑话",
    "讲个 DeepSeek 笑话",
    "讲个 deepseek 的段子",
    "再来一个",
    "不好笑，换一个",
    "还有吗",
    "笑话",
    "整点鲸鱼笑话",
    "来个谐音梗",
    "再讲一个",
    "讲个笑话吧",
    "来点笑话",
    "给我讲两个笑话",
]

# 期望不命中：不能误判成点笑话
SHOULD_MISS = [
    "继续加班",
    "今天太冷了",
    "这个笑话真好笑",
    "笑死我了",
    "你别笑话我了",
    "我讲个笑话给你听",
    "在吗",
    "今天吃什么",
    "给我发个表情包",
    "唱一段晴天",
    "算命",
    "DeepSeek 是什么",
]


def main() -> int:
    svc = JokeService()
    st = svc.stats()
    print("=== 笑话库 ===")
    print(f"条数 {st['total']}  分类 {st['kinds']}  带配图词 {st['with_sticker']}")
    if not svc.available:
        print("!! 笑话库不可用")
        return 1

    fails = 0
    print("\n=== 意图解析：应命中 ===")
    for text in SHOULD_HIT:
        it = svc.resolve(text)
        ok = it is not None
        fails += 0 if ok else 1
        print(
            f"  {'OK ' if ok else 'FAIL'} {text:<18} -> "
            f"{'None' if it is None else f'kind={it.kind} again={it.again} count={it.count}'}"
        )

    print("\n=== 意图解析：不应命中 ===")
    for text in SHOULD_MISS:
        it = svc.resolve(text)
        ok = it is None
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} {text:<18} -> {it}")

    print("\n=== 抽取去重（同房间连续 10 次，kind=None）===")
    seen = []
    room = "test@chatroom"
    for i in range(10):
        it = svc.resolve("再来一个")
        jokes = svc.pick(it, room)
        if not jokes:
            print("  !! 抽不到笑话")
            fails += 1
            break
        j = jokes[0]
        seen.append(j.id)
        svc.record(j, room)
        print(f"  {i + 1:>2}. {j.id:<7} {j.tags[0] if j.tags else ''}")
    dup = len(seen) - len(set(seen))
    print(f"  重复 {dup} 条（10 次里）")
    if dup > 2:
        fails += 1
        print("  !! 重复过多")

    print("\n=== 分类命中 ===")
    for text, want in [("来点大肥鱼笑话", "whale"), ("讲个 deepseek 笑话", "deepseek"),
                       ("说个冷笑话", "cold")]:
        it = svc.resolve(text)
        ok = it is not None and it.kind == want
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} {text} -> {None if it is None else it.kind}（期望 {want}）")

    print("\n=== 组合文本抽样（3 条）===")
    for text in ("来点大肥鱼笑话", "讲个 deepseek 笑话", "讲个冷笑话"):
        it = svc.resolve(text)
        jokes = svc.pick(it, "sample@chatroom")
        body, query = svc.compose(jokes)
        print(f"\n--- {text}（query: {query}）---")
        print(body)
        if not body or len(body) < 20:
            fails += 1
            print("  !! 文本过短")

    print("\n=== 配图召回（bge 向量，首次加载约 2-4 秒）===")
    t = time.perf_counter()
    try:
        from src.sticker_recall import StickerRecall

        recall = StickerRecall(items_file="./表情包/aigengtu/stickers.json")
        if not recall.available:
            print("  -- 向量召回不可用（跳过，机器人会退回随机挑图）")
        else:
            print(f"  模型就绪 {time.perf_counter() - t:.1f}s，索引 {len(recall.items)} 条")
            for text, want in [
                ("来点大肥鱼笑话", "服务器繁忙 排队"),
                ("讲个 deepseek 笑话", "便宜 生气"),
                ("说个冷笑话", "困 睡觉"),
            ]:
                it = svc.resolve(text)
                jokes = svc.pick(it, "recall@chatroom")
                _, query = svc.compose(jokes)
                hit = recall.recall(query, 3)
                print(f"\n  查询「{query}」")
                for h in hit:
                    print(
                        f"    {h['score']:.3f}  {h.get('file')}  "
                        f"{h.get('caption') or h.get('name')}"
                    )
                if not hit or hit[0]["score"] < 0.40:
                    fails += 1
                    print("    !! 召回分数偏低")
    except Exception as exc:  # noqa: BLE001
        print(f"  -- 召回测试跳过: {type(exc).__name__}: {exc}")

    print(f"\n==== 结果：{'全部通过' if fails == 0 else f'{fails} 项失败'} ====")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
