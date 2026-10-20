# -*- coding: utf-8 -*-
"""在「文件传输助手」里做少量真实测试：基本对话 / 表情包 / 唱歌 / 曲库关键词。

只往文件传输助手发，不碰任何群。
用法：.venv\\Scripts\\python.exe _test_filehelper_flow.py
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


def recent(limit=12):
    db = WeChatDB()
    out = []
    for m in db.get_messages(ROOM_ID, limit=limit):
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("create_time", 0)))
        content = str(m.get("content"))
        if m.get("type") == "语音":
            import re
            mm = re.search(r'voicelength="(\d+)"', content)
            content = f"语音 {int(mm.group(1))/1000:.1f}s" if mm else "语音"
        elif m.get("type") == "图片":
            content = "图片"
        out.append((ts, m.get("type"), content[:40].replace("\n", " ")))
    return out


async def main() -> int:
    print("=== 初始化 bot（不启动监听）===")
    bot = ChatBot()
    if not bot.initialize("config.yaml"):
        print("初始化失败")
        return 1

    bridge = WeChatBridge(target_groups=[], listen_private=False)
    ok = await bridge.connect()
    print(f"微信连接: {ok}  UIA={getattr(bridge, '_uia_available', None)}")
    if not ok:
        return 1
    bot.handler.wechat = bridge

    cases = [
        ("你好呀，今天过得怎么样", "基本对话"),
        ("发个表情包看看", "表情包"),
        ("唱一段晴天", "唱歌（真唱·一段）"),
        ("补充曲库", "曲库关键词"),
    ]
    if len(sys.argv) > 1:
        cases = [(t, "自定义") for t in sys.argv[1:]]
    before = {c[0] for c in recent(12)}
    for text, label in cases:
        print(f"\n=== [{label}] 输入: {text}")
        t = time.perf_counter()
        try:
            await bot.handler._handle_message(make_message(text))
        except Exception as exc:
            print(f"  异常: {type(exc).__name__}: {exc}")
        print(f"  用时 {time.perf_counter()-t:.1f}s")
        time.sleep(1.0)

    print("\n=== 文件传输助手最近消息（数据库回读）===")
    for ts, typ, content in recent(10):
        print(f"  [{ts}] {typ} | {content}")
    await bridge.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
