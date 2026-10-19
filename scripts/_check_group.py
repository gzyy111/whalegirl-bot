# -*- coding: utf-8 -*-
"""只读：翻最近几条群消息，确认发送到底落没落地。"""
import sys
import time

sys.path.insert(0, r"D:\鲸鱼娘")

from wechatauto import WeChatDB  # noqa: E402

WXID = "12345678@chatroom"
db = WeChatDB()
for m in db.get_messages(WXID, limit=12):
    ts = time.strftime("%m-%d %H:%M:%S", time.localtime(m.get("create_time", 0)))
    content = str(m.get("content", "")).replace("\n", " ")[:70]
    sender = str(m.get("sender_id"))[-14:]
    print("[%s] type=%-6s %-14s | %s" % (ts, m.get("type"), sender, content))
