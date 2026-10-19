# -*- coding: utf-8 -*-
"""点名发图（发006 / 发第6张 / 发触发词）离线回归。

不连微信、不发任何消息，只验证：
  1. 编号索引认得 6 / 006 / 0006
  2. 触发词表能命中
  3. 解析器「该接管的接管、不该接管的不接管」
  4. named_only 默认空 = 006 仍在随机池里（内部群不设防）

用法：.venv\\Scripts\\python.exe _test_named_sticker.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.message_handler import MessageHandler            # noqa: E402
from src.sticker_library import StickerLibrary            # noqa: E402

# 只借 MessageHandler 的解析逻辑：不跑它的 __init__，只塞一个表情包库
class FakeHandler(MessageHandler):
    def __init__(self, lib):  # noqa: D401 - 测试桩
        self._stickers = lib


SHOULD_HIT = [
    ("发006", "006"),
    ("发 006", "006"),
    ("来张006", "006"),
    ("发图006", "006"),
    ("发第6张", "006"),
    ("编号006", "006"),
    ("#006", "006"),
    ("006号表情", "006"),
    ("给我发6", "006"),
    ("发直球核弹", "006"),
    ("发你看又急", "006"),
    ("发便宜货", "001"),
    ("发上错模型", "037"),
    ("发个006", "006"),
]

SHOULD_MISS = [
    "今天3点开会",
    "来两个笑话",
    "第2首歌",
    "发3个表情包",
    "这首歌唱了2遍",
    "006",
    "006号",
    "便宜货真好用",
    "你是不是上错模型了",
    "明天见",
    "发个表情包",
    "再来一个",
]


def main() -> int:
    lib = StickerLibrary(
        dirs=["./表情包/aigengtu"],
        cache_dir="./data/sticker_cache",
        v2_enabled=False,
        named_only=[],
        aliases_file="./表情包/aigengtu/点名表.json",
    )
    st = lib.stats()
    print("=== 表情包库 ===")
    print(f"总数 {st['total']}  可点编号 {st['indexed_numbers']}  "
          f"触发词 {st['named_aliases']}  点名专用 {st['named_only'] or '无（不设防）'}")
    if not lib.available:
        print("!! 表情包库不可用")
        return 1

    print("\n=== 编号索引 ===")
    for raw in ("6", "006", "0006", "612", "9999"):
        p = lib.by_number(raw)
        print(f"  {raw:<6} -> {Path(p).name if p else 'None'}")

    h = FakeHandler(lib)
    fails = 0

    print("\n=== 应命中 ===")
    for text, want in SHOULD_HIT:
        p = h._match_named_sticker(text)
        ok = p is not None and Path(p).stem == want
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} {text:<12} -> "
              f"{Path(p).name if p else 'None'}（期望 {want}.webp）")

    print("\n=== 不应命中 ===")
    for text in SHOULD_MISS:
        p = h._match_named_sticker(text)
        ok = p is None
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} {text:<14} -> {Path(p).name if p else 'None'}")

    print("\n=== 随机池（named_only 为空时应包含 006）===")
    pool = lib._random_pool()
    has_006 = any(Path(p).stem == "006" for p in pool)
    print(f"  池大小 {len(pool)}，包含 006: {has_006}")
    if not has_006:
        fails += 1
        print("  !! 006 不在随机池里（若确实想保护它，请把 named_only 设成 [006]）")
    print(f"  is_named_only(006) = {lib.is_named_only(lib.by_number('6') or '')}")

    print(f"\n==== 结果：{'全部通过' if fails == 0 else f'{fails} 项失败'} ====")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
