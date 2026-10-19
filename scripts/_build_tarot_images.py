"""把 Rider-Waite 塔罗牌面整理成 bot 用的图片库 + 标签表。

来源：npm `@cometpisces/tarot-kit-images@0.2.0`（代码 MIT；RWS 牌面 1909 年出版，
多数司法辖区已进公有领域）。原始是 78 张 ~300KB 的 PNG，这里统一缩到宽 600、
存 JPEG，微信发送更快、磁盘也从 21MB 降到几 MB。

产出：
    塔罗牌\\images\\<card-id>.jpg
    塔罗牌\\tags.json      # 仿表情包 tags.json 的结构：labels + files[路径] = 标签

用法：
    .venv\\Scripts\\python.exe _build_tarot_images.py <解包后的 images 目录>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "塔罗牌"
IMG_OUT = OUT / "images"

ELEMENT = {"wands": "火", "cups": "水", "swords": "风", "pentacles": "土"}
COURT = {11: "侍者", 12: "骑士", 13: "王后", 14: "国王"}


def parse_map(index_js: Path) -> dict[str, str]:
    text = index_js.read_text(encoding="utf-8")
    return dict(re.findall(r'"([a-z0-9-]+)":\s*"([A-Za-z0-9-]+\.png)"', text))


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(sys.argv[0]).parent
    index_js = src.parent / "dist" / "index.js"
    mapping = parse_map(index_js)
    cards = json.loads((ROOT / "src" / "divination" / "tarot_data.json")
                       .read_text(encoding="utf-8"))["cards"]
    by_id = {c["id"]: c for c in cards}

    IMG_OUT.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    total = 0
    for card_id, filename in mapping.items():
        source = src / filename
        if not source.exists():
            print("缺图:", card_id, filename)
            continue
        with Image.open(source) as im:
            im = im.convert("RGB")
            w, h = im.size
            new_w = 600
            new_h = round(h * new_w / w)
            im = im.resize((new_w, new_h), Image.LANCZOS)
            dest = IMG_OUT / f"{card_id}.jpg"
            im.save(dest, "JPEG", quality=88, optimize=True)
        card = by_id.get(card_id, {})
        tags = {
            "arcana": card.get("arcana", "major"),
            "suit": card.get("suit"),
            "number": card.get("number"),
            "element": ELEMENT.get(card.get("suit") or "", "大阿尔卡纳"),
            "court": COURT.get(card.get("number") or 0),
            "keyword": card.get("keyword", ""),
            "name": card.get("name", ""),
        }
        files[f"images/{card_id}.jpg"] = tags
        total += dest.stat().st_size

    payload = {
        "version": 1,
        "labels": ["大阿尔卡纳", "权杖", "圣杯", "宝剑", "星币", "火", "水", "风", "土"],
        "files": files,
    }
    (OUT / "tags.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"卡图: {len(files)} 张，总计 {total / 1024 / 1024:.1f} MB")
    print("目录:", IMG_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
