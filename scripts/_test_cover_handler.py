# -*- coding: utf-8 -*-
"""真唱点歌链路的烟测：不起微信、不起 GUI，只验证 handler 分派与发送。

用法：.venv\\Scripts\\python.exe _test_cover_handler.py
"""
import asyncio
import logging
import sys
import time
import yaml
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.message_handler import MessageHandler          # noqa: E402
from src.wechat_bridge import WeChatMessage             # noqa: E402
from src.cover_service import CoverService              # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")


class FakeBridge:
    def __init__(self):
        self.texts: list[tuple[str, str]] = []
        self.audios: list[tuple] = []

    async def send_text(self, room_name, content):
        self.texts.append((room_name, content))
        print(f"  [发文字] {content}")
        return True

    async def send_audio(self, room_name, audio_path, duration=0.0,
                         as_file=False, prefer_voice=True):
        self.audios.append((room_name, audio_path, duration, as_file))
        print(f"  [发音频] {Path(audio_path).name} ({Path(audio_path).stat().st_size} 字节, "
              f"as_file={as_file}, 时长={duration:.1f}s)")
        return True

    async def send_file(self, room_name, file_path):
        self.audios.append((room_name, str(file_path)))
        print(f"  [发文件] {Path(file_path).name}")
        return True


class FakeSecurity:
    def can_proceed(self):
        return True

    def record_success(self):
        pass

    def record_failure(self, *a, **k):
        pass


class FakeStyle:
    def get_active(self):
        return {"name": "tsundere_whale_girl"}


def make_message(text: str) -> WeChatMessage:
    now = time.time()
    return WeChatMessage(
        id=f"t{int(now * 1000)}",
        sender="wxid_test",
        sender_name="群友小明",
        room_id="12345678@chatroom",
        room_name="鲸鱼娘测试群",
        content=text,
        type=1,
        timestamp=now,
        is_group=True,
        is_at_me=True,
    )


async def main() -> int:
    cfg = yaml.safe_load(open(REPO / "config.yaml", encoding="utf-8"))
    cc = cfg["voice"]["songs"]["cover"]
    cover = CoverService(
        enabled=True,
        python_exe=cc["python_exe"],
        script=cc["script"],
        work_dir=cc["work_dir"],
        cache_dir=cc["cache_dir"],
        library_dirs=cc["library_dirs"],
        default_mode=cc["default_mode"],
        segment_seconds=cc["segment_seconds"],
        prompt=cc["prompt"],
        timeout=cc["timeout"],
        max_queue=cc["max_queue"],
        voice_version=cc["voice_version"],
    )
    print("CoverService:", cover.available())

    bridge = FakeBridge()
    handler = MessageHandler(
        wechat=bridge,
        ai_engine=None,
        style_manager=FakeStyle(),
        security=FakeSecurity(),
        on_event=lambda name, data: None,
        cover_service=cover,
        voice_enabled=True,
        song_service=None,
    )

    cases = [
        ("唱一段 洛阳纸", "命中曲库 + 片段"),      # 应已缓存，秒回
        ("唱《爱如潮水》", "曲库没有"),              # 应回「没有这首」
        ("今天天气不错", "不是点歌"),                # 应完全不理
        ("别唱了", "拒绝"),                          # 同上
    ]
    for text, expect in cases:
        print(f"\n=== {text}  （{expect}）===")
        req = cover.resolve(text)
        if req is None:
            print("  resolve -> None（交回原路径，本测试不动）")
            continue
        print(f"  resolve -> query={req.query!r} mode={req.mode} "
              f"song={(req.song or {}).get('title')}")
        started = time.perf_counter()
        await handler._handle_cover(make_message(text), req, started)
        print(f"  用时 {time.perf_counter() - started:.1f}s")

    print("\n---- 汇总 ----")
    print("发文字:", len(bridge.texts), "条")
    print("发音频:", len(bridge.audios), "条")
    for room, path, *_ in bridge.audios:
        print("  ", path)
    return 0 if bridge.audios else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
