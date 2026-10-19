"""塔罗牌：78 张 Rider-Waite 牌义 + 抽牌。

数据来源：npm `@cometpisces/tarot-kit` 0.2.0（MIT），中英双语牌义，
繁体已转简体、台版译名已对齐大陆叫法（女皇→皇后、钱币→星币…）。
Rider-Waite 牌义本身出自 A.E. Waite《The Pictorial Key to the Tarot》(1910)，已进公有领域。

只做「抽牌 + 摆牌阵」，解牌交给 reader 的提示词——和八字、紫微一样的分工。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DATA = Path(__file__).parent / "tarot_data.json"
_IMAGE_DIR = _ROOT / "塔罗牌" / "images"
_CACHE_DIR = _ROOT / "data" / "tarot_cache"
_DECK: list[dict] | None = None

#: 牌阵：位名 + 每张代表什么
SPREADS = {
    1: ("单张", ["当下"]),
    3: ("三张·过去现在未来", ["过去", "现在", "未来"]),
}

POSITION_HINT = {
    "当下": "这张牌是眼下的状态与提醒",
    "过去": "这张牌是事情的来处、已经发生的影响",
    "现在": "这张牌是当下正在起作用的力量",
    "未来": "这张牌是照现在这个走法，接下来容易走到的方向",
}


def deck() -> list[dict]:
    global _DECK
    if _DECK is None:
        data = json.loads(_DATA.read_text(encoding="utf-8"))
        _DECK = data["cards"]
    return _DECK


def card_text(card: dict, reversed_: bool) -> str:
    meaning = card["reversed"] if reversed_ else card["upright"]
    facing = "逆位" if reversed_ else "正位"
    aspect = (card.get("aspects") or {}).get("currentSituation", {})
    extra = aspect.get("reversed" if reversed_ else "upright", "")
    line = f"{card['name']}（{facing}·{card['keyword']}）：{meaning}"
    if extra:
        line += f"　现况：{extra}"
    return line


def cast(n: int = 3, seed: int | None = None) -> dict:
    """抽 n 张牌（1 或 3），带正逆位。"""
    n = 1 if n <= 1 else 3
    name, positions = SPREADS[n]
    rng = random.Random(seed)
    picked = rng.sample(deck(), n)
    drawn = []
    for pos, card in zip(positions, picked):
        reversed_ = rng.random() < 0.5
        drawn.append({
            "position": pos,
            "hint": POSITION_HINT.get(pos, ""),
            "card": card,
            "reversed": reversed_,
            "text": card_text(card, reversed_),
        })
    return {"spread": name, "positions": positions, "drawn": drawn}


def cast_text(result: dict) -> str:
    lines = [f"牌阵：{result['spread']}"]
    for d in result["drawn"]:
        lines.append(f"【{d['position']}】{d['text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 牌面图


def image_path(card_id: str) -> Path | None:
    """牌面图路径（RWS，1909 年版，公有领域）。"""
    path = _IMAGE_DIR / f"{card_id}.jpg"
    return path if path.exists() else None


def _font(size: int):
    from PIL import ImageFont

    for name in ("msyh.ttc", "simhei.ttf", "msyhbd.ttc"):
        candidate = Path("C:/Windows/Fonts") / name
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size)
            except Exception:
                continue
    return ImageFont.load_default()


def render(result: dict) -> Path | None:
    """把抽到的牌渲染成一张可发送的图片（缓存）。

    单张：直接用牌面，逆位旋转 180°（塔罗的规矩）。
    三张：横向拼成一张，标上位置与牌名，逆位同样倒过来。
    """
    from PIL import Image, ImageDraw

    drawn = [d for d in result["drawn"] if image_path(d["card"]["id"])]
    if not drawn:
        return None

    key = "_".join(f"{d['card']['id']}{'r' if d['reversed'] else 'u'}" for d in drawn)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = _CACHE_DIR / f"{key}.jpg"
    if out.exists():
        return out

    try:
        if len(drawn) == 1:
            d = drawn[0]
            im = Image.open(image_path(d["card"]["id"])).convert("RGB")
            if d["reversed"]:
                im = im.rotate(180)
            im.save(out, "JPEG", quality=88, optimize=True)
            return out

        # 三张拼图
        height = 430
        gap, margin, label_h = 14, 16, 34
        thumbs = []
        for d in drawn:
            im = Image.open(image_path(d["card"]["id"])).convert("RGB")
            w = round(im.width * height / im.height)
            im = im.resize((w, height), Image.LANCZOS)
            if d["reversed"]:
                im = im.rotate(180)
            thumbs.append((d, im, w))
        total_w = margin * 2 + sum(t[2] for t in thumbs) + gap * (len(thumbs) - 1)
        canvas = Image.new("RGB", (total_w, height + label_h + margin * 2),
                           (250, 248, 244))
        draw = ImageDraw.Draw(canvas)
        font = _font(20)
        x = margin
        for d, im, w in thumbs:
            canvas.paste(im, (x, margin))
            label = f"{d['position']}·{d['card']['name']}{'（逆）' if d['reversed'] else ''}"
            draw.text((x + 4, margin + height + 6), label, fill=(60, 55, 50), font=font)
            x += w + gap
        canvas.save(out, "JPEG", quality=88, optimize=True)
        return out
    except Exception:
        return None


def images_for(result: dict) -> list[Path]:
    """要发送的图片列表：单张发原牌面，三张发拼图。"""
    path = render(result)
    return [path] if path else []
