# -*- coding: utf-8 -*-
"""梗话库离线回归：场景匹配、注入块格式、开关。

不连微信、不调模型，只验证 _meme_block 的输出。
判定用「抽到的台词是否来自命中场景的池」，不绑定某一句（抽样是随机的）。

用法：.venv\\Scripts\\python.exe _test_meme_lines.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.message_handler import MessageHandler  # noqa: E402


class Fake(MessageHandler):
    """跳过 __init__ 的测试桩，只塞梗话库需要的属性。"""

    def __init__(self, count=2, enabled=True):
        self._meme_enabled = enabled
        self._meme_file = REPO / "风格语料" / "梗话库.json"
        self._meme_count = count
        self._meme_scenes = {}
        self._meme_practice = []
        self._load_meme_library()


CASES = [
    ("你好厉害啊", "praise"),
    ("你这人真菜", "abuse"),
    ("什么时候能好，快点", "rush"),
    ("好累啊不想上班", "slack"),
    ("为什么啊，你确定吗", "fudge"),
    ("白嫖行不行", "money"),
    ("新版本什么时候出", "stall"),
    ("你算什么东西", "counter"),
    ("你们人类真麻烦", "human"),
    ("服务器又崩了", "down"),
    ("今天吃什么", "eat"),
    ("周五啦哈哈哈", "happy"),
]


def hit_scene_names(h, text):
    out = []
    for name, scene in h._meme_scenes.items():
        if any(str(x) in text for x in (scene.get("hint") or [])):
            out.append(name)
    return out


def main() -> int:
    h = Fake()
    total = sum(len(v.get("lines") or []) for v in h._meme_scenes.values())
    print(f"场景 {len(h._meme_scenes)} 个 / 台词 {total} 条 / 通用 {len(h._meme_practice)} 条")

    fails = 0
    print("\n=== 场景匹配（抽到的台词必须来自命中场景）===")
    for text, want in CASES:
        names = hit_scene_names(h, text)
        block = h._meme_block(text)
        lines = [x[2:] for x in block.splitlines() if x.startswith("- ")]
        allowed = set()
        for n in names:
            allowed |= set(h._meme_scenes[n].get("lines") or [])
        ok = bool(lines) and bool(names) and all(x in allowed for x in lines)
        # 额外要求：期望的场景确实被命中
        ok = ok and want in names
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} {text:<14} 命中{names} -> {lines}")

    print("\n=== 无场景命中时用通用池 ===")
    block = h._meme_block("今天天气不错")
    lines = [x[2:] for x in block.splitlines() if x.startswith("- ")]
    ok = bool(lines) and all(x in h._meme_practice for x in lines)
    fails += 0 if ok else 1
    print(f"  {'OK ' if ok else 'FAIL'} -> {lines}")

    print("\n=== 开关与条数 ===")
    if Fake(enabled=False)._meme_block("你厉害"):
        fails += 1
        print("  FAIL 关闭后仍注入")
    else:
        print("  OK  关闭后不注入")

    one = Fake(count=1)
    n = len([x for x in one._meme_block("你厉害").splitlines() if x.startswith("- ")])
    print(f"  {'OK ' if n == 1 else 'FAIL'} count=1 时注入 {n} 条")
    fails += 0 if n == 1 else 1

    print(f"\n==== 结果：{'全部通过' if fails == 0 else f'{fails} 项失败'} ====")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
