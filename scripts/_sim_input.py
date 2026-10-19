# -*- coding: utf-8 -*-
"""模拟输入调试：真实 handler + 真实服务，发送端换成「只记录不发送」的假桥。

不连微信、不发任何真实消息、不打开任何窗口。
用法：.venv\\Scripts\\python.exe _sim_input.py [文本1] [文本2] ...
"""
import asyncio
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from main import ChatBot                      # noqa: E402
from src.wechat_bridge import WeChatMessage   # noqa: E402


class RecordingBridge:
    """只记录、绝不发送的假桥。"""

    backend = "wechat"

    def __init__(self):
        self.texts = []
        self.audios = []
        self.files = []
        self.images = []
        self.voices = []
        self.calls = []

    @property
    def is_connected(self):
        return True

    def normalize_message_content(self, *a, **k):
        return a[0] if a else ""

    async def send_text(self, room_name, content, **kw):
        self.texts.append((room_name, str(content)))
        self.calls.append("send_text")
        return True

    async def send_audio(self, room_name, audio_path, duration=0.0,
                         as_file=False, prefer_voice=True, **kw):
        self.audios.append((room_name, str(audio_path), duration, as_file))
        self.calls.append("send_audio")
        return True

    async def send_file(self, room_name, file_path, **kw):
        self.files.append((room_name, str(file_path)))
        self.calls.append("send_file")
        return True

    async def send_image(self, room_name, image_path, **kw):
        self.images.append((room_name, str(image_path)))
        self.calls.append("send_image")
        return True

    async def send_voice(self, room_name, text, **kw):
        self.voices.append((room_name, str(text)))
        self.calls.append("send_voice")
        return True

    def reset(self):
        for name in ("texts", "audios", "files", "images", "voices", "calls"):
            getattr(self, name).clear()


def make_message(text, room_name="模拟会话", room_id="sim@chatroom",
                 group=True, at_me=True):
    now = time.time()
    return WeChatMessage(
        id=f"sim{int(now * 1000000)}",
        sender="wxid_sim",
        sender_name="模拟群友",
        room_id=room_id,
        room_name=room_name,
        content=text,
        type=1,
        timestamp=now,
        is_group=group,
        is_at_me=at_me,
    )


def instrument(handler):
    """给各快路径打点，记录这条消息命中了哪条分支。"""
    hits = []

    def wrap(name):
        original = getattr(handler, name)

        async def patched(*a, **k):
            hits.append(name)
            return await original(*a, **k)

        setattr(handler, name, patched)

    for name in ("_handle_cover", "_handle_song", "_handle_voice_request",
                 "_handle_quick_reply", "_handle_sticker_request",
                 "_handle_image_request", "_handle_poke",
                 "_handle_voice_message", "_handle_private_image",
                 "_handle_divination", "_handle_joke", "_handle_named_sticker"):
        wrap(name)
    return hits


def summarize(bridge):
    out = []
    for room, txt in bridge.texts:
        out.append(f"文字«{txt[:60]}»")
    for room, path, dur, as_file in bridge.audios:
        out.append(f"音频 {Path(path).name} ({Path(path).stat().st_size // 1024}KB"
                   f"{', as_file' if as_file else ''})")
    for room, path in bridge.files:
        out.append(f"文件 {Path(path).name}")
    for room, path in bridge.images:
        out.append(f"图片 {Path(path).name}")
    for room, txt in bridge.voices:
        out.append(f"语音«{txt[:40]}»")
    return out or ["（无输出）"]


async def main() -> int:
    texts = sys.argv[1:] or [
        "你好呀，在忙吗",
        "发个表情包",
        "唱首歌",
        "歌单",
        "能唱什么歌",
        "怎么补充曲库",
        "补充曲库",
    ]

    print("=== 初始化（不连微信）===")
    bot = ChatBot()
    if not bot.initialize("config.yaml"):
        print("初始化失败")
        return 1
    handler = bot.handler
    bridge = RecordingBridge()
    handler.wechat = bridge
    hits = instrument(handler)
    print(f"AI 可用: {bot.ai is not None}, 曲库: {bot.song.song_count() if bot.song else 0} 首, "
          f"真唱: {bot.cover.available() if bot.cover else None}, "
          f"表情包: {getattr(bot, '_sticker_library', None) is not None}")

    rows = []
    for text in texts:
        bridge.reset()
        hits.clear()
        t = time.perf_counter()
        try:
            await handler._handle_message(make_message(text))
            # 讲笑话会在 2.5 秒后由后台任务补表情包，等它跑完再收集输出
            if "笑话" in text or "再来" in text or "换一个" in text:
                await asyncio.sleep(3.5)
            err = ""
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        cost = time.perf_counter() - t
        path = hits[-1].replace("_handle_", "") if hits else "AI/主流程"
        rows.append((text, path, " | ".join(summarize(bridge)), f"{cost:.1f}s", err))
        print(f"\n--- 输入: {text}")
        print(f"    分支: {path}   用时 {cost:.1f}s")
        for line in summarize(bridge):
            print(f"    输出: {line}")
        if err:
            print(f"    !! {err}")

    print("\n==== 汇总 ====")
    for text, path, out, cost, err in rows:
        print(f"{text:<14} → {path:<20} {cost:>6}  {out[:90]}{'  ERR:' + err if err else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
