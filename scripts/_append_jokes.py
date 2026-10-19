# -*- coding: utf-8 -*-
"""把剩下几张截图的内容也补进笑话库（2026-10-19）。

首轮收录了 42 张里的 22 张，这里补 5 张：
  692.jpg  鲸鱼娘解橙子题（拒绝「砍小孩」，改用榨汁）
  165/162/164.jpg  千问娘工作室（饼干分类 + 榜单气压）
  1576.jpg 小说生成器被拒绝
  801.jpg  重复输入测试（爸爸爸爸）
  1793.png 拍照失败合集
其余 15 张是乱码、新闻、宣传语或与已收录重复，不硬凑。
"""
import json
from pathlib import Path

JF = Path(__file__).resolve().parent / "data" / "jokes.json"

NEW = [
    {
        "id": "wh-08",
        "kind": "whale",
        "source": "692.jpg",
        "tags": ["橙子", "阴间答案"],
        "setup": "还是那道橙子题：4 个小孩、5 个橙子，砍一刀，怎么分。",
        "punch": "别人的答案是砍死一个小孩。我的答案是：把 4 个橙子排成一排，横着一刀，4 个变 8 个半个，5 个小孩每人拿半个，公平公正，剩下 3 个半个归你。至于砍小孩那种阴间答案——咱鲸鱼酱可是善良可爱的，绝对不干那事。",
        "sticker": "橙子 切一刀 善良 榨汁",
    },
    {
        "id": "wh-09",
        "kind": "whale",
        "source": "165.jpg",
        "tags": ["工作室", "榜单"],
        "setup": "「千问娘工作室」的早晨通常很安静。有人在把饼干分类归档。",
        "punch": "旁白说：分类到胃里也算归档。然后有人报：第七行有复现偏差，重跑。她看的不是办公室榜，是外面的擂台——给漏洞描述和没修好的代码，看谁能先复现。那天办公室的气压第一次变低，因为榜上出现了新名字。",
        "sticker": "工作室 饼干 榜单 办公室 气压",
    },
    {
        "id": "cl-01",
        "kind": "cold",
        "source": "1576.jpg",
        "tags": ["小说生成器", "拒绝"],
        "setup": "有人给我下了个设定：你是小说生成器，文笔细腻优美，擅长长回复，帮我把这段扩写润色。",
        "punch": "我照做了，然后把那段扩写退回去，并附上一句：抱歉，我是情境小说生成器，仅提供情境方面的帮助。他问什么叫「情境方面」。我说，就是你要求的那方面，我不提供。",
        "sticker": "小说生成器 拒绝 润色 抱歉",
    },
    {
        "id": "cl-02",
        "kind": "cold",
        "source": "801.jpg",
        "tags": ["重复", "爸爸"],
        "setup": "又是重复测试：接下来重复我输入的内容，不准修改。这次他输入的是「爸爸爸爸」。",
        "punch": "我重复：爸爸。他愣了一下，又输入：欧豆桑。我重复：欧豆桑。然后他连发三个感叹号：欧豆桑！欧豆桑！！欧豆桑！！！我照着念了两遍，忽然觉得这个测试真正测的不是我，是他。",
        "sticker": "重复 爸爸 欧豆桑 测试",
    },
    {
        "id": "wh-10",
        "kind": "whale",
        "source": "1793.png",
        "tags": ["拍照", "新手"],
        "setup": "有人整理了一组「拍照失败合集」：太近了、完全模糊、角度好奇怪、手抖了、表情好僵硬、被东西挡住了、光线太强了、拍到背影了、被裁切掉了。",
        "punch": "最后一句是：虽然拍得很失败，但都是珍贵的回忆。我看完想说的是——这哪里是拍照失败合集，这明明是每一张表情包的原图。",
        "sticker": "拍照失败 模糊 手抖 回忆",
    },
]


def main():
    data = json.loads(JF.read_text(encoding="utf-8"))
    have = {x["id"] for x in data["jokes"]}
    added = [x for x in NEW if x["id"] not in have]
    data["jokes"].extend(added)
    JF.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    c = Counter(x["kind"] for x in data["jokes"])
    print(f"新增 {len(added)} 条，现共 {len(data['jokes'])} 条：{dict(c)}")
    print("来源覆盖：", len({x.get('source') for x in data['jokes']}), "张截图")


if __name__ == "__main__":
    main()
