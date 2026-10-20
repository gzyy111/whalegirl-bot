# -*- coding: utf-8 -*-
"""在「文件传输助手」里真机验证讲笑话：文字 + 讲完跟上的表情包。

只往文件传输助手发，不碰任何群。跑之前必须先停机器人（微信窗口独占）。
用法：.venv\\Scripts\\python.exe _test_joke_live.py ["来点大肥鱼笑话"]
"""
import asyncio
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from main import ChatBot                                      # noqa: E402
from src.wechat_bridge import WeChatBridge, WeChatMessage     # noqa: E402
from wechatauto import WeChatDB                               # noqa: E402

ROOM = "文件传输助手"
ROOM_ID = "filehelper"


def make_message(text: str) -> WeChatMessage:
    now = time.time()
    return WeChatMessage(
        id=f"jk{int(now * 1000000)}",
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


def snapshot(limit: int = 20) -> list[dict]:
    db = WeChatDB()
    out = []
    for m in db.get_messages(ROOM_ID, limit=limit):
        out.append({
            "ts": m.get("create_time", 0),
            "type": m.get("type"),
            "content": str(m.get("content") or ""),
            "hdlength": m.get("hdlength") or 0,
        })
    return out


async def wait_new(before_ts: float, want_image: bool, want_text: bool,
                   timeout: float = 150.0):
    """轮询数据库，直到想要的都出现或超时。"""
    deadline = time.time() + timeout
    texts, images = [], []
    while time.time() < deadline:
        await asyncio.sleep(3.0)
        fresh = [m for m in snapshot() if float(m["ts"]) > before_ts - 2]
        texts = [m for m in fresh if m["type"] == "文本"]
        images = [m for m in fresh if m["type"] == "图片"]
        if (images or not want_image) and (texts or not want_text):
            break
    return texts, images


async def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "来点大肥鱼笑话"
    print("=== 初始化 bot（不启动监听）===")
    bot = ChatBot()
    if not bot.initialize("config.yaml"):
        print("初始化失败")
        return 1
    print(f"笑话服务: {getattr(bot, 'joke', None) is not None}, "
          f"配图开关: {getattr(bot, 'joke_sticker_after', None)}")

    bridge = WeChatBridge(target_groups=[], listen_private=False)
    ok = await bridge.connect()
    print(f"微信连接: {ok}  UIA={getattr(bridge, '_uia_available', None)}")
    if not ok:
        return 1
    bot.handler.wechat = bridge

    before = snapshot()
    before_ts = max((float(m["ts"]) for m in before), default=0.0)
    print(f"\n=== 发送: {text}")
    t = time.perf_counter()
    try:
        await bot.handler._handle_message(make_message(text))
    except Exception as exc:  # noqa: BLE001
        print(f"  异常: {type(exc).__name__}: {exc}")
    print(f"  文字环节用时 {time.perf_counter() - t:.1f}s（含后台配图等待）")

    texts, images = await wait_new(
        before_ts, want_image=True, want_text=("笑话" in text or "再来" in text)
    )
    print("\n=== 数据库回读（本轮新增）===")
    if texts:
        for m in texts:
            print(f"  [{time.strftime('%H:%M:%S', time.localtime(m['ts']))}] 文本: "
                  f"{m['content'][:120].replace(chr(10), ' / ')}")
    else:
        print("  !! 没有读到文本")
    if images:
        for m in images:
            print(f"  [{time.strftime('%H:%M:%S', time.localtime(m['ts']))}] 图片: "
                  f"hdlength={m['hdlength']}")
    else:
        print("  !! 没有读到图片（讲完没接上表情包）")

    await bridge.disconnect()
    want_text = "笑话" in text or "再来" in text
    ok_all = bool(images) and (bool(texts) or not want_text)
    print(f"\n==== 结果：{'通过' if ok_all else '未通过'}"
          f"（{'文字+表情包' if want_text else '单图'}）====")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
