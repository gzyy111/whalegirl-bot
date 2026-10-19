"""算命端到端实测：真实 handler + 真实 AI，发送端只记录。

不连微信、不发真实消息。打印完整回复全文，用来判断读起来像不像人话。

用法：.venv\\Scripts\\python.exe _test_divination_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import _sim_input as sim  # noqa: E402
from main import ChatBot  # noqa: E402

CASES = [
    # 2026-10-19 新增：塔罗（带牌面图）
    "塔罗 问感情 要不要主动一点",
    "塔罗一张 问今天顺不顺",
]


async def probe_should_reply(handler, bridge) -> None:
    """不经消息管线，直接问「这条要不要接」——查参与概率把追问吞掉的毛病。"""
    from src.wechat_bridge import WeChatMessage  # noqa: F401

    msg = sim.make_message("1999年7月1日 17.00，男", at_me=False)
    # 没挂起时
    handler.divination.clear_pending(msg.room_id)
    print("\n[should_reply] 无挂起、未 @：", await handler._should_reply(msg))
    # 挂起时
    handler.divination.set_pending(msg.room_id, "看看工作")
    print("[should_reply] 有挂起、未 @：", await handler._should_reply(msg),
          "（期望 True）")
    print("[护栏] 有挂起时是否被拦：",
          handler._should_suppress_consecutive(msg), "（期望 False）")
    handler.divination.clear_pending(msg.room_id)


async def main() -> int:
    bot = ChatBot()
    if not bot.initialize("config.yaml"):
        print("初始化失败")
        return 1
    handler = bot.handler
    bridge = sim.RecordingBridge()
    handler.wechat = bridge

    await probe_should_reply(handler, bridge)

    for text in CASES:
        bridge.reset()
        print("\n" + "=" * 70)
        print("输入：", text)
        print("=" * 70)
        try:
            await handler._handle_message(sim.make_message(text))
        except Exception as exc:  # noqa: BLE001
            print("!! 异常:", type(exc).__name__, exc)
            continue
        for _room, out in bridge.texts:
            print(out)
        await asyncio.sleep(3)   # 配图是后台任务，等它跑完
        for _room, img in bridge.images:
            print(f"[配图] {Path(img).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
