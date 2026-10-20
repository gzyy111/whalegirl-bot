# -*- coding: utf-8 -*-
"""开机补消息 · 接线验证（走 main.py 真实方法，dry-run 不发消息）。

验证三件事：
1. initialize 之后 ChatBot 上确实有 catchup 服务（配置被读进去）；
2. main._on_message 收到 is_history 消息时会喂给 catchup；
3. main._schedule_catchup 会在延时后触发一次 flush。
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger  # noqa: E402

logger.remove()

from main import ChatBot  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class StubWeChat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, room: str, content: str) -> bool:
        self.sent.append((room, content))
        return True


class StubAI:
    is_available = True

    async def chat(self, messages, system_prompt="", temperature=None, max_tokens=None):
        return "呜……本鲸鱼娘刚醒，你们说的话我都看到了，等我缓一下就回。"


class StubHandler:
    def __init__(self) -> None:
        self.enqueued: list = []

    async def enqueue(self, message) -> None:
        self.enqueued.append(message)


def history_msg(content: str, at_me: bool = True, room: str = "示例群"):
    return types.SimpleNamespace(
        id=f"h:{content}",
        content=content,
        room_id=room,
        room_name=room,
        sender="群友甲",
        sender_name="群友甲",
        is_group=True,
        is_private=False,
        is_at_me=at_me,
        is_poke_me=False,
        is_history=True,
        is_self=False,
        is_target_group=True,
        message_type="text",
    )


STATE = Path(__file__).parent / "data" / "_test_catchup_state.json"


def build_bot() -> ChatBot:
    bot = object.__new__(ChatBot)
    bot.wechat = StubWeChat()
    bot.ai = StubAI()
    bot.handler = StubHandler()
    bot.window = None
    bot._recent_messages = []
    bot._max_recent = 50
    # 归档会写 data/message_archive.jsonl，接线测试不需要落盘
    bot._archive_message = lambda record: None  # type: ignore[assignment]
    STATE.unlink(missing_ok=True)   # 每次从干净状态开始，否则会被「快速重启」闸门挡住
    bot._setup_catchup(
        {
            "replay_grace_seconds": 0.0,
            "catchup": {
                "enabled": True,
                "dry_run": True,
                "delay_after_connect": 0.1,
                "only_at_me": True,
                "min_offline_minutes": 0,
                "state_path": str(STATE),
                "archive_path": str(STATE.with_name("_test_catchup_archive.jsonl")),
            },
        }
    )
    return bot


async def main() -> int:
    print("[1] 服务被挂上 ChatBot")
    bot = build_bot()
    check("bot.catchup 存在", getattr(bot, "catchup", None) is not None)
    check("dry_run 生效（不会真发）", bot.catchup.dry_run is True)
    check("延时来自配置", abs(bot.catchup.delay_after_connect - 0.1) < 1e-6)

    print("\n[2] _on_message 把历史消息喂给 catchup")
    await bot._on_message(history_msg("@大肥鱼 在吗"))
    await bot._on_message(history_msg("@大肥鱼 昨晚那个 bug"))
    await bot._on_message(history_msg("普通闲聊", at_me=False))
    pending = bot.catchup.pending()
    check("收下 @ 她的 2 条", pending.get("示例群") == 2, str(pending))
    check("普通消息没进队列", bot.catchup.pending().get("示例群") == 2)
    check("历史消息没进回复管线", bot.handler.enqueued == [], str(bot.handler.enqueued))

    print("\n[3] 新消息仍然正常进管线（没被补消息吃掉）")
    fresh = history_msg("@大肥鱼 新来的")
    fresh.is_history = False
    await bot._on_message(fresh)
    check("新消息进 handler", len(bot.handler.enqueued) == 1, str(bot.handler.enqueued))

    print("\n[4] _schedule_catchup 延时后触发 flush")
    bot._schedule_catchup()   # 真实路径里由 _start_wechat 在连上微信后调用
    task = getattr(bot, "_catchup_task", None)
    check("任务已创建", task is not None)
    if task is not None:
        await asyncio.sleep(1.2)
        check("任务执行完", task.done(), "still running")
        check("dry-run 没真发消息", bot.wechat.sent == [], str(bot.wechat.sent))
        check("处理后队列清空", bot.catchup.pending() == {}, str(bot.catchup.pending()))
        check("记账写了状态文件", STATE.is_file())

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
