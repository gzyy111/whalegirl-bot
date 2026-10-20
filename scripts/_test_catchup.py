# -*- coding: utf-8 -*-
"""开机补消息 · 离线回归（不连微信、不调真实模型）。

覆盖：
1. 收哪些、不收哪些（@她 / 拍一拍 / 私聊 / 非监控群 / 非历史 / 自己发 / 占位符）
2. 每个会话只发一句（不是一条消息回一句）
3. 模型返回两句时只留第一句；超长截断
4. 同一批消息重启后不重复接（状态文件去重）
5. 模型不可用时用兜底话术，照样发一句
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.catchup_service import CatchupService  # noqa: E402

# 测试只看断言结果，日志不往 stderr 喷（否则 PowerShell 会当成报错）
from loguru import logger  # noqa: E402

logger.remove()

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
    def __init__(self, ok: bool = True) -> None:
        self.sent: list[tuple[str, str]] = []
        self.ok = ok

    async def send_text(self, room: str, content: str) -> bool:
        self.sent.append((room, content))
        return self.ok


class StubAI:
    def __init__(self, reply: str = "", available: bool = True) -> None:
        self.reply = reply
        self.is_available = available
        self.calls = 0

    async def chat(self, messages, system_prompt="", temperature=None, max_tokens=None):
        self.calls += 1
        return self.reply


def msg(
    content: str,
    room: str = "示例群",
    sender: str = "群友甲",
    is_group: bool = True,
    is_at_me: bool = False,
    is_poke_me: bool = False,
    is_history: bool = True,
    is_self: bool = False,
    is_target_group: bool = True,
):
    return types.SimpleNamespace(
        id=f"{room}:{sender}:{content}",
        content=content,
        room_id=room,
        room_name=room,
        sender=sender,
        sender_name=sender,
        is_group=is_group,
        is_private=not is_group,
        is_at_me=is_at_me,
        is_poke_me=is_poke_me,
        is_history=is_history,
        is_self=is_self,
        is_target_group=is_target_group,
        message_type="text",
    )


def make(tmp: str, **kw) -> CatchupService:
    # 测试里默认关掉「距上次启动不足 N 分钟不补」的闸门，各用例自己控制；
    # 归档也放临时目录，避免读到仓库里的真实消息。
    kw.setdefault("min_offline_minutes", 0)
    kw.setdefault("archive_path", str(Path(tmp) / "archive.jsonl"))
    kw.setdefault("pending_path", str(Path(tmp) / "pending.json"))
    return CatchupService(
        wechat=kw.pop("wechat", StubWeChat()),
        ai=kw.pop("ai", StubAI("呜……才睡了一觉就攒了这么多，本鲸鱼娘缓一下就来。")),
        state_path=str(Path(tmp) / "catchup_state.json"),
        **kw,
    )


async def case_collect() -> None:
    print("\n[1] 收集规则")
    with tempfile.TemporaryDirectory() as tmp:
        svc = make(tmp)                      # 默认 only_at_me=False：普通消息也收
        svc.note(msg("@大肥鱼 出来干活", is_at_me=True))
        svc.note(msg("普通闲聊，没叫我"))
        svc.note(msg("拍了拍本鲸鱼娘", is_poke_me=True))
        svc.note(msg("私聊找我", room="某好友", is_group=False))
        svc.note(msg("@大肥鱼 但不回复", is_at_me=True, is_history=False))
        svc.note(msg("@大肥鱼 自己发的", is_at_me=True, is_self=True))
        svc.note(msg("[图片]", is_at_me=True))
        svc.note(msg("@大肥鱼 别的群", is_at_me=True, room="别的群", is_target_group=False))
        pending = svc.pending()
        check("监控群收 3 条（@ + 闲聊 + 拍一拍）", pending.get("示例群") == 3, str(pending))
        check("私聊 -> 收", pending.get("某好友") == 1, str(pending))
        check("非监控群 -> 不收", "别的群" not in pending)
        check("非历史消息 -> 不收", svc.note(msg("@大肥鱼 新消息", is_at_me=True, is_history=False)) is False)
        check("占位符 -> 不收", svc.note(msg("[图片]", is_at_me=True)) is False)

        # 只收 @ 她的模式
        svc_at = make(tmp + "/at", only_at_me=True)
        svc_at.note(msg("@大肥鱼 在吗", is_at_me=True))
        svc_at.note(msg("普通闲聊"))
        svc_at.note(msg("拍了拍", is_poke_me=True))
        check("only_at_me=true -> 只收 @ 与拍一拍", svc_at.pending().get("示例群") == 2, str(svc_at.pending()))

        # 私聊开关
        svc2 = make(tmp + "/b", include_private=False)
        svc2.note(msg("私聊", room="某好友", is_group=False))
        check("include_private=false -> 私聊不收", svc2.pending() == {})


async def case_one_sentence() -> None:
    print("\n[2] 每个会话只发一句")
    with tempfile.TemporaryDirectory() as tmp:
        wx = StubWeChat()
        ai = StubAI("呜……才睡一觉就攒了这么多，本鲸鱼娘这就来看。第二句不该出现。")
        svc = make(tmp, wechat=wx, ai=ai)
        for text in ("@大肥鱼 在吗", "@大肥鱼 昨晚那个 bug 呢", "@大肥鱼 出来"): 
            svc.note(msg(text, is_at_me=True, sender="甲"))
        svc.note(msg("@大肥鱼 私聊也找", room="某好友", is_group=False))
        results = await svc.flush()

        check("两个会话各一条", len(wx.sent) == 2, str(wx.sent))
        check("同会话多条只发一次", sum(1 for r, _ in wx.sent if r == "示例群") == 1)
        text = [t for r, t in wx.sent if r == "示例群"][0]
        check("只留第一句", "第二句" not in text, text)
        check("模型被调用两次（每会话一次）", ai.calls == 2, str(ai.calls))
        check("返回结果带条数", any(r["count"] == 3 for r in results), str(results))
        check("结果可序列化", isinstance(results[0]["text"], str))


async def case_dedupe() -> None:
    print("\n[3] 重启不重复接")
    with tempfile.TemporaryDirectory() as tmp:
        wx1 = StubWeChat()
        svc1 = make(tmp, wechat=wx1)
        svc1.note(msg("@大肥鱼 在吗", is_at_me=True))
        await svc1.flush()
        check("第一次发了 1 条", len(wx1.sent) == 1)

        # 模拟重启：新实例，读同一个状态文件，同一条历史消息再来一次
        wx2 = StubWeChat()
        svc2 = make(tmp, wechat=wx2)
        svc2.note(msg("@大肥鱼 在吗", is_at_me=True))
        results2 = await svc2.flush()
        check("同一句不重复发", len(wx2.sent) == 0 and results2 == [], str(wx2.sent))

        # 换一条新消息则照发
        wx3 = StubWeChat()
        svc3 = make(tmp, wechat=wx3)
        svc3.note(msg("@大肥鱼 新的一句话", is_at_me=True))
        await svc3.flush()
        check("新消息照发", len(wx3.sent) == 1, str(wx3.sent))


async def case_fallback() -> None:
    print("\n[4] 模型不可用 / 发送失败")
    with tempfile.TemporaryDirectory() as tmp:
        wx = StubWeChat()
        svc = make(tmp, wechat=wx, ai=StubAI(available=False))
        for i in range(3):
            svc.note(msg(f"@大肥鱼 第{i}条", is_at_me=True, sender=f"甲{i}"))
        await svc.flush()
        check("模型不可用也发", len(wx.sent) == 1)
        check("兜底话术含条数", "3" in wx.sent[0][1], wx.sent[0][1] if wx.sent else "")

        wx_fail = StubWeChat(ok=False)
        svc2 = make(tmp + "/f", wechat=wx_fail)
        svc2.note(msg("@大肥鱼 在吗", is_at_me=True))
        results = await svc2.flush()
        check("发送失败不记已读", results and results[0]["ok"] is False)
        wx_retry = StubWeChat()
        svc3 = make(tmp + "/f", wechat=wx_retry)
        svc3.note(msg("@大肥鱼 在吗", is_at_me=True))
        results3 = await svc3.flush()
        check("下次启动会重试", len(wx_retry.sent) == 1 and results3[0]["ok"] is True, str(results3))


async def case_limits() -> None:
    print("\n[5] 长度与会话上限")
    with tempfile.TemporaryDirectory() as tmp:
        wx = StubWeChat()
        long_reply = "本鲸鱼娘刚睡醒就看见你们一堆话，我先看看是谁在找我，等我缓过来再一个个收拾你们这些不睡觉的。"
        svc = make(tmp, wechat=wx, ai=StubAI(long_reply), max_chars=30)
        svc.note(msg("@大肥鱼 在吗", is_at_me=True))
        await svc.flush()
        text = wx.sent[0][1]
        check("超长被截断", len(text) <= 31, f"{len(text)}: {text}")

        wx2 = StubWeChat()
        svc2 = make(tmp + "/g", wechat=wx2, max_groups=1)
        for room in ("群A", "群B", "群C"):
            svc2.note(msg("@大肥鱼 在吗", room=room, is_at_me=True))
        svc2.note(msg("@大肥鱼 也在", room="群A", is_at_me=True, sender="乙"))
        await svc2.flush()
        check("max_groups=1 只发一个会话", len(wx2.sent) == 1, str(wx2.sent))
        check("优先条数最多的会话", wx2.sent[0][0] == "群A", str(wx2.sent))


async def case_threshold() -> None:
    print("\n[6] 冒泡阈值（没人叫时攒够几条才说话）")
    with tempfile.TemporaryDirectory() as tmp:
        wx = StubWeChat()
        svc = make(tmp, wechat=wx, min_messages=3)
        svc.note(msg("今天好热", sender="甲"))
        svc.note(msg("空调坏了", sender="乙"))
        await svc.flush()
        check("2 条闲聊不冒泡", wx.sent == [], str(wx.sent))

        wx2 = StubWeChat()
        svc2 = make(tmp + "/b", wechat=wx2, min_messages=3)
        for text in ("今天好热", "空调坏了", "谁来修一下"):
            svc2.note(msg(text, sender="甲"))
        await svc2.flush()
        check("3 条闲聊冒一句", len(wx2.sent) == 1, str(wx2.sent))

        wx3 = StubWeChat()
        svc3 = make(tmp + "/c", wechat=wx3, min_messages=3)
        svc3.note(msg("@大肥鱼 在吗", is_at_me=True))
        await svc3.flush()
        check("只有 1 条但 @ 了她 -> 必接", len(wx3.sent) == 1, str(wx3.sent))

        wx4 = StubWeChat()
        svc4 = make(tmp + "/d", wechat=wx4, min_messages=3)
        svc4.note(msg("在吗", room="某好友", is_group=False))
        await svc4.flush()
        check("私聊 1 条也接", len(wx4.sent) == 1, str(wx4.sent))


async def case_offline_gate() -> None:
    print("\n[7] 启动间隔闸门（min_offline_minutes）")
    with tempfile.TemporaryDirectory() as tmp:
        wx = StubWeChat()
        svc = make(tmp, wechat=wx, min_offline_minutes=60)   # 首次运行：没历史，允许
        svc.note(msg("@大肥鱼 在吗", is_at_me=True))
        await svc.flush()
        check("首次运行会补", len(wx.sent) == 1, str(wx.sent))

        # 新规则：停机期间有人叫她，重启后必须应一声（不受闸门限制）
        wx2 = StubWeChat()
        svc2 = make(tmp, wechat=wx2, min_offline_minutes=60)
        svc2.note(msg("@大肥鱼 人呢", is_at_me=True))
        results2 = await svc2.flush()
        check("几分钟内重启但有 @ -> 仍然必接", len(wx2.sent) == 1, str(wx2.sent))
        check("闸门状态可见", svc2._eligible is False)

        # 没人叫的闲聊才受闸门限制
        wx3 = StubWeChat()
        svc3 = make(tmp, wechat=wx3, min_offline_minutes=60)
        for text in ("今天好热", "空调坏了", "谁来修一下"):
            svc3.note(msg(text, sender="甲"))
        await svc3.flush()
        check("几分钟内重启、只是闲聊 -> 不冒泡", wx3.sent == [], str(wx3.sent))

        wx4 = StubWeChat()
        svc4 = make(tmp + "/free", wechat=wx4, min_offline_minutes=0)
        for text in ("今天好热", "空调坏了", "谁来修一下"):
            svc4.note(msg(text, sender="甲"))
        await svc4.flush()
        check("阈值 0 时闲聊也补", len(wx4.sent) == 1, str(wx4.sent))


async def case_seen_live() -> None:
    print("\n[8] 当时在线收过的不再补")
    import json as _json

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "archive.jsonl"
        archive.write_text(
            "\n".join(
                [
                    _json.dumps(
                        {"room": "示例群", "sender": "甲", "content": "在线时说过的话"},
                        ensure_ascii=False,
                    ),
                    _json.dumps(
                        {"room": "示例群", "sender": "乙", "content": "回放里的旧话", "history": True},
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        svc = make(tmp)
        svc.archive_path = archive
        svc._seen_live.clear()
        svc._load_seen_live()

        check(
            "在线收到的消息 -> 不收",
            svc.note(msg("在线时说过的话", sender="甲")) is False,
        )
        check(
            "只在回放里出现过的消息 -> 仍收",
            svc.note(msg("回放里的旧话", sender="乙")) is True,
        )
        check("归档里只索引了 1 条", len(svc._seen_live) == 1, str(len(svc._seen_live)))


async def case_pending_persist() -> None:
    print("\n[9] 被闸门跳过的消息不丢，下次接着补")
    with tempfile.TemporaryDirectory() as tmp:
        # 预置状态：刚启动过 1 分钟，触发「间隔不足」闸门
        import json as _json

        (Path(tmp) / "catchup_state.json").write_text(
            _json.dumps({"acked": {}, "last_start": time.time() - 60, "last_flush": 0}),
            encoding="utf-8",
        )
        wx = StubWeChat()
        svc = make(tmp, wechat=wx, min_offline_minutes=60)
        for text in ("今天好热", "空调坏了", "谁来修一下"):
            svc.note(msg(text, sender="甲"))
        await svc.flush()
        check("本次不冒泡", wx.sent == [], str(wx.sent))
        pending_file = Path(tmp) / "pending.json"
        check("但落盘了", pending_file.is_file())
        if pending_file.is_file():
            data = __import__("json").loads(pending_file.read_text(encoding="utf-8"))
            check("落了 3 条", len(data.get("items", [])) == 3, str(len(data.get("items", []))))

        # 下次启动：闸门放开 -> 把上次没发出去的补上
        wx2 = StubWeChat()
        svc2 = make(tmp, wechat=wx2, min_offline_minutes=0)
        check("启动时恢复待补队列", svc2.pending().get("示例群") == 3, str(svc2.pending()))
        await svc2.flush()
        check("补发出去了", len(wx2.sent) == 1, str(wx2.sent))
        check("补完清掉落盘", not pending_file.is_file())


async def main() -> int:
    await case_collect()
    await case_one_sentence()
    await case_dedupe()
    await case_fallback()
    await case_limits()
    await case_threshold()
    await case_offline_gate()
    await case_seen_live()
    await case_pending_persist()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
