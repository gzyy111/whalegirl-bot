# -*- coding: utf-8 -*-
import asyncio, io, os, sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

BASE = r"D:\鲸鱼娘"
sys.path.insert(0, BASE)
os.chdir(BASE)
OUT = io.StringIO()
def w(*a):
    print(*a, file=OUT)

from src.sticker_library import StickerLibrary
from src.message_handler import MessageHandler

LOG = io.StringIO()
from loguru import logger
logger.remove()
logger.add(LOG, level="INFO", format="{message}")

lib = StickerLibrary(
    dirs=[r"./表情包/deepseek-hd", r"./表情包/blue-fish-hd", r"./表情包/owner-picks"],
    cache_dir=r"./data/sticker_cache", max_side=800, recent_avoid=30,
    tags_file=r"./表情包/tags.json", v2_enabled=True,
    v2_tags_file=r"./表情包/tags_v2.json", v2_mode="safe",
)
lib.prepare = lambda p: p   # 直接返回源路径，便于回读标签

class Ctx:
    def __init__(self, msgs): self.messages = list(msgs)
    def add(self, role, content, max_history=50): self.messages.append({'role': role, 'content': content})

class Sec:
    def can_proceed(self): return True
    def record_success(self): pass

def make_handler(context_msgs):
    h = object.__new__(MessageHandler)
    h._stickers = lib
    h._contexts = {"room": Ctx(list(context_msgs))}
    h._context_history = 20
    h._save_contexts = lambda: None
    h._mark_room_replied = lambda *a, **k: None
    h._emit = lambda *a, **k: None
    h.security = Sec()
    sent = []
    async def send_image(room, path, timeout=60.0):
        sent.append(path)
        return True
    h.wechat = SimpleNamespace(send_image=send_image)
    return h, sent

def msg(text, room="测试群"):
    return SimpleNamespace(room_name=room, room_id="room", content=text)

def v2(p):
    return lib._v2_of.get(p) or {}

async def run(text, context=None):
    h, sent = make_handler(context or [])
    LOG.seek(0); LOG.truncate(0)
    ok = await h._send_local_sticker(msg(text))
    return h, sent, ok, LOG.getvalue()

async def main():
    w("== 真实发送路径（_send_local_sticker）==")
    for text, ctx_msgs in [
        ("发个黑丝", []),
        ("发个裸足", []),
        ("给我来张泳装", []),
        ("发个躺着的", []),
        ("发个表情包", []),
        ("发个表情包", [{"role": "user", "content": "赵阳阳: 今天加班到现在，好累"}]),
        ("发个表情包", [{"role": "user", "content": "赵阳阳: 哈哈哈笑死我了"},
                       {"role": "assistant", "content": "笑点这么低"},
                       {"role": "user", "content": "赵阳阳: 快发个表情包"}]),
    ]:
        h, sent, ok, log = await run(text, ctx_msgs)
        rec = v2(sent[0]) if sent else {}
        w("  %-10s 上下文=%d 条 ok=%s file=%s" % (
            text, len(ctx_msgs), ok, Path(sent[0]).name if sent else "-"))
        w("     标签: %s" % {k: rec.get(k) for k in ("mood", "legwear", "outfit", "shot", "subject", "kind")})
        w("     日志: %s" % log.strip().replace("\n", " | "))

asyncio.run(main())
Path(BASE + r"\_probe_send_out.txt").write_text(OUT.getvalue(), encoding="utf-8")
print("WROTE")