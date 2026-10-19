"""把 @cometpisces/tarot-kit 的牌义数据整理成 bot 用的 tarot_data.json。

来源：npm @cometpisces/tarot-kit 0.2.0（MIT），Rider-Waite 体系，含中英双语牌义。
处理：繁→简、裁掉插画描述与多余维度，只留解牌用得到的字段。

用法：.venv\\Scripts\\python.exe _build_tarot_data.py <raw.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from zhconv import convert

KEEP_ASPECTS = ("currentSituation",)

#: 台版译名 → 大陆常见译名（只影响可读性，不改牌义）
NORMALIZE = {
    "女皇": "皇后",
    "隐士": "隐者",
    "吊人": "倒吊人",
    "高塔": "塔",
    "钱币": "星币",
}


def zh(text: str) -> str:
    out = convert(text or "", "zh-cn").strip()
    for old, new in NORMALIZE.items():
        out = out.replace(old, new)
    return out


def main() -> int:
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tarot_raw.json")
    cards = json.loads(raw_path.read_text(encoding="utf-8"))
    out = []
    for c in cards:
        aspects = {}
        for key in KEEP_ASPECTS:
            block = (c.get("readingAspects") or {}).get(key) or {}
            aspects[key] = {
                "upright": zh((block.get("upright") or {}).get("zh", "")),
                "reversed": zh((block.get("reversed") or {}).get("zh", "")),
            }
        out.append({
            "id": c["id"],
            "name": zh(c["name"]["zh"]),
            "name_en": c["name"]["en"],
            "arcana": c["arcana"],
            "suit": c.get("suit"),
            "number": c["number"],
            "keyword": zh(c["coreKeyword"]["zh"]),
            "upright": zh(c["meaning"]["upright"]["zh"]),
            "reversed": zh(c["meaning"]["reversed"]["zh"]),
            "aspects": aspects,
        })

    payload = {
        "_meta": {
            "source": "@cometpisces/tarot-kit 0.2.0 (npm)",
            "license": "MIT",
            "system": "Rider-Waite",
            "note": "牌义中文由繁体转简体；仅保留解牌字段",
            "count": len(out),
        },
        "cards": out,
    }
    dest = Path(__file__).parent / "src" / "divination" / "tarot_data.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("写入:", dest)
    print("牌数:", len(out), "大小:", dest.stat().st_size, "字节")
    print("示例:", out[0]["name"], "|", out[0]["keyword"], "|", out[0]["upright"][:40])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
