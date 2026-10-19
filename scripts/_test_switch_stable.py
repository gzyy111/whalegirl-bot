# -*- coding: utf-8 -*-
"""稳定性：两次「目标会话未打开」的文本发送（双向各一次）+ 数据库校验"""
import sys, time, asyncio
sys.path.insert(0, r"D:\鲸鱼娘")

from wechatauto.guia import WeChatGUI
from wechatauto import WeChatDB
from src.wechat_bridge import WeChatBridge

GROUP = "示例群"
FILE = "文件传输助手"


def last_msgs(who, limit=2):
    db = WeChatDB()
    cid = db.group_name_to_id(who) or who
    out = []
    for m in db.get_messages(cid, limit=limit):
        out.append((time.strftime("%H:%M:%S", time.localtime(m.get("create_time", 0))),
                    m.get("type"), str(m.get("content"))[:40].replace("\n", " ")))
    return out


async def main():
    gui = WeChatGUI()
    gui.bring_to_front()
    bridge = WeChatBridge(target_groups=[GROUP], listen_private=False)
    print("connect:", await bridge.connect())

    cases = [(GROUP, FILE, "切会话测试A：从群切到文件传输助手"),
             (FILE, GROUP, "切会话测试B：从文件传输助手切到群")]
    for open_first, target, text in cases:
        gui.open_chat(open_first)
        time.sleep(2.0)
        cur = gui._get_uia().current_chat()
        t = time.time()
        ok = await bridge.send_text(target, text)
        print(f"  当前={cur!r} → 发送到[{target}]: {ok}  ({time.time()-t:.1f}s)")
        print("    落地:", last_msgs(target, 2))

    await bridge.disconnect()


asyncio.run(main())
