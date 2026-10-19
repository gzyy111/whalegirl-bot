# -*- coding: utf-8 -*-
"""端到端：在真实群里走 bot 的发送链路，文本 + 短语音，并回读数据库确认"""
import asyncio, sys, time
sys.path.insert(0, r"D:\鲸鱼娘")

from src.wechat_bridge import WeChatBridge
from wechatauto import WeChatDB

ROOM = "示例群"


def db_print(limit=4):
    db = WeChatDB()
    cid = db.group_name_to_id(ROOM) or ROOM
    for m in db.get_messages(cid, limit=limit):
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("create_time", 0)))
        print(f"   [{ts}] {m.get('type')} | {str(m.get('content'))[:60].replace(chr(10), ' ')}")


async def main():
    bridge = WeChatBridge(target_groups=[ROOM], listen_private=False)
    ok = await bridge.connect()
    print("connect:", ok, "uia:", bridge._uia_available)
    if not ok:
        return

    print("\n== 1) 文本 ==")
    t = time.time()
    sent = await bridge.send_text(ROOM, "鲸鱼娘自检：文本通道正常。")
    print(f"   send_text -> {sent}  ({time.time()-t:.1f}s)")

    print("\n== 2) 短语音 ==")
    t = time.time()
    voiced = await bridge.send_voice(ROOM, "鲸鱼娘自检：语音条通道也正常。")
    print(f"   send_voice -> {voiced}  ({time.time()-t:.1f}s)")

    print("\n== 数据库回读 ==")
    db_print(6)

    await bridge.disconnect()


asyncio.run(main())
