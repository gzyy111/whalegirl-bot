# -*- coding: utf-8 -*-
"""扩充战斗话术库：调用 DeepSeek 批量合成「犀利吐槽」样本，过滤后合并入库。

- 只读 config.yaml 解密 API Key，只写 风格语料/吐槽库.json，不碰微信。
- 合成样本带 source="ai-synth" 标记，与人工/真实语料区分。
- 过滤：长度、脏字黑名单、重复。
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config_manager import ConfigManager  # noqa: E402
from src.ai_engine import AIEngine  # noqa: E402

CORPUS = ROOT / "风格语料" / "吐槽库.json"

# 场景 → 生成要求（每个场景生成 PER_SCENE 条）
SCENES = {
    "被贬低": "对方骂你是废物、死鱼、没用、蠢，直接贬低你",
    "被质疑能力": "对方怀疑你不会、说你瞎编、说你不行",
    "被抬杠": "对方抬杠、阴阳怪气、说「就这」「不一定吧」「谁不知道」",
    "被夸": "对方夸你厉害、牛逼、比别的AI强",
    "被催干活": "对方催你快一点、嫌你慢、威胁要走",
    "群友吹牛": "群友在吹牛，说自己随便就第一、从没输过、一个人干三个人的活",
    "被挑衅": "对方挑衅你，问敢不敢、怕不怕、有种别回",
    "被调侃外貌": "对方说你胖、圆、该减肥（人设：这是抗压脂肪层）",
}

PER_SCENE = 9

BANNED = (
    "操", "草泥马", "尼玛", "傻逼", "煞笔", "妈逼", "去死", "死妈", "贱", "婊",
    "畜", "杂种", "他妈", "妈的", "卧槽", "我操", "屌", "鸡巴", "肏", "滚你",
    "脑残", "废物东西", "智障", "弱智", "全家",
)

SYSTEM_PROMPT = (
    "你在为一个中文微信群聊天机器人整理「怼人话术库」。"
    "机器人人设：深海鲸鱼娘，傲娇、嘴硬、聪明、爱白米饭，自称「本鲸鱼娘」。"
    "风格要求：犀利吐槽、阴阳怪气、抬杠、反问、反将一军，"
    "像贴吧老哥那种嘴贫，但**必须不带脏字**，"
    "不得涉及地域、性别、外貌、职业、家人等真实人身攻击，"
    "不得出现侮辱性称呼。回复必须是一两句口语化的简体中文，20 字以内。"
)


def build_user_prompt(scene: str, desc: str, n: int) -> str:
    return (
        f"场景「{scene}」：{desc}。\n"
        f"请生成 {n} 组对话样本，每组是「对方的挑衅/调侃」+「鲸鱼娘的反击」。\n"
        "严格要求：\n"
        "1. 只输出 JSON 数组，不要任何解释、不要 markdown 代码块。\n"
        '2. 每条形如 {"user": "对方说的话", "assistant": "鲸鱼娘的反击"}。\n'
        "3. assistant 必须是 20 个汉字以内的口语短句，犀利、有梗、不认怂。\n"
        "4. assistant 里绝对不能出现脏字或侮辱性词汇。\n"
        "5. 每条都不一样，不要重复句式。"
    )


def extract_json(text: str):
    """从模型输出里抠出 JSON 数组（容忍 ```json 包裹和前后废话）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return []


def is_clean(item) -> bool:
    if not isinstance(item, dict):
        return False
    user = str(item.get("user", "")).strip()
    assistant = str(item.get("assistant", "")).strip()
    if not user or not assistant:
        return False
    if len(assistant) > 40 or len(user) > 60:
        return False
    if any(word in assistant or word in user for word in BANNED):
        return False
    # assistant 里夹英文字母的丢掉（人设要求纯中文）
    if re.search(r"[A-Za-z]", assistant):
        return False
    return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-scene", type=int, default=PER_SCENE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = ConfigManager("config.yaml")
    cfg.load("default")
    ai_cfg = cfg.get("ai", {})
    key = cfg.get_decrypted("ai.api_key_encrypted")
    if not key:
        print("[错误] API Key 解密失败")
        return 1

    ai = AIEngine(
        api_url=ai_cfg.get("api_url", ""),
        api_key=key,
        model=ai_cfg.get("model", "deepseek-chat"),
        max_tokens=900,
        temperature=1.0,
        timeout=60,
        max_retries=2,
        api_style=ai_cfg.get("api_style", "auto"),
    )

    with open(CORPUS, "r", encoding="utf-8") as f:
        corpus = json.load(f)
    scenes = corpus.setdefault("scenes", {})

    added_total = 0
    for scene, desc in SCENES.items():
        existing = scenes.setdefault(scene, [])
        seen = {str(item.get("assistant", "")).strip() for item in existing}
        try:
            raw = await ai.chat(
                messages=[{"role": "user", "content": build_user_prompt(scene, desc, args.per_scene)}],
                system_prompt=SYSTEM_PROMPT,
                temperature=1.0,
                max_tokens=900,
            )
        except Exception as exc:
            print("  [%s] 生成失败: %s: %s" % (scene, type(exc).__name__, exc))
            continue

        items = extract_json(raw or "")
        good = []
        for item in items:
            if not is_clean(item):
                continue
            assistant = str(item["assistant"]).strip()
            if assistant in seen:
                continue
            seen.add(assistant)
            good.append(
                {
                    "user": str(item["user"]).strip(),
                    "assistant": assistant,
                    "source": "ai-synth",
                }
            )
        existing.extend(good)
        added_total += len(good)
        print("  [%s] 模型给出 %d 条，过滤后新增 %d 条" % (scene, len(items), len(good)))

    print()
    print("新增合计 %d 条" % added_total)
    if args.dry_run:
        print("（--dry-run 未写盘）")
        return 0

    corpus["updated"] = "2026-10-15"
    with open(CORPUS, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in scenes.values())
    print("写入 %s：现有 %d 条 / %d 个场景" % (CORPUS.name, total, len(scenes)))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
