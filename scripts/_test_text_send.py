# -*- coding: utf-8 -*-
"""实测：走 bot 真实发送链路（WeChatBridge.send_text）"""
import asyncio, sys, time
sys.path.insert(0, r"D:\鲸鱼娘")

from src.wechat_bridge import WeChatBridge


async def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "文件传输助手"
    text = sys.argv[2] if len(sys.argv) > 2 else "文本发送测试"
    bridge = WeChatBridge(target_groups=[], listen_private=False)
    t0 = time.time()
    ok = await bridge.connect()
    print(f"connect -> {ok} ({time.time()-t0:.1f}s)  uia={bridge._uia_available}")
    if not ok:
        return
    t1 = time.time()
    sent = await bridge.send_text(target, text)
    print(f"send_text -> {sent} ({time.time()-t1:.1f}s)")
    await bridge.disconnect()


asyncio.run(main())
