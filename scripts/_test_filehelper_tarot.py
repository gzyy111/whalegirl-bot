# -*- coding: utf-8 -*-
"""塔罗真机验证：往「文件传输助手」发一条塔罗请求，等牌面图落地。

和 _test_filehelper_flow.py 的区别：那边用 time.sleep 阻塞事件循环，
后台发图任务根本没机会跑；这里改成 await asyncio.sleep 并轮询数据库。

用法：.venv\\Scripts\\python.exe _test_filehelper_tarot.py ["塔罗 问感情"]
"""
import asyncio
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from main import ChatBot                        # noqa: E402
from src.wechat_bridge import WeChatBridge, WeChatMessage   # noqa: E402
from wechatauto import WeChatDB                 # noqa: E402

ROOM = "文件传输助手"
ROOM_ID = "filehelper"
TEXT = sys.argv[1] if len(sys.argv) > 1 else "塔罗 问感情 要不要主动一点"


def make_message(text):
    now = time.time()
    return WeChatMessage(
        id=f"fh{int(now * 1000000)}",
        sender="wxid_sim",
        sender_name="本人",
        room_id=ROOM_ID,
        room_name=ROOM,
        content=text,
        type=1,
        timestamp=now,
        is_group=False,
        is_at_me=True,
        is_private=True,
    )


def snapshot(limit=8):
    out = []
    for m in WeChatDB().get_messages(ROOM_ID, limit=limit):
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("create_time", 0)))
        out.append((m.get("create_time", 0), ts, m.get("type"),
                    str(m.get("content"))[:60].replace("\n", " ")))
    return out


async def main() -> int:
    bot = ChatBot()
    if not bot.initialize("config.yaml"):
        print("初始化失败")
        return 1
    bridge = WeChatBridge(target_groups=[], listen_private=False)
    if not await bridge.connect():
        print("微信连接失败")
        return 1
    bot.handler.wechat = bridge
    print(f"微信已连接 UIA={getattr(bridge, '_uia_available', None)}")

    started = time.time()
    print(f"\n=== 输入: {TEXT}")
    await bot.handler._handle_message(make_message(TEXT))

    print("\n=== 等牌面图落地（最多 150 秒，轮询数据库）===")
    image_seen = False
    for i in range(30):
        await asyncio.sleep(5)
        rows = snapshot(10)
        fresh = [r for r in rows if r[0] >= started - 2]
        types = [r[2] for r in fresh]
        print(f"  {(i + 1) * 5:>3}s 新消息 {len(fresh)} 条：{types}")
        if "图片" in types:
            image_seen = True
            break

    print("\n=== 文件传输助手最近消息 ===")
    for _ct, ts, typ, content in snapshot(8):
        print(f"  [{ts}] {typ} | {content}")

    print("\n结果：", "牌面图已落地 ✅" if image_seen else "没等到图片 ❌")
    await bridge.disconnect()
    return 0 if image_seen else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
