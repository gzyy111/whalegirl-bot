# -*- coding: utf-8 -*-
"""验证：网页 token 实时推送 + 落盘累计。

步骤：连 WS → 触发 /api/ai/test（会真的调一次 AI）→ 打印收到的 token_usage 事件
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets

BASE = "http://127.0.0.1:8080"
WS = "ws://127.0.0.1:8080/ws"


async def get_usage():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/api/status")
        data = r.json()
        return (data.get("ai") or {}).get("usage") or {}


async def main():
    before = await get_usage()
    print("调用前 usage:", before)

    got = []
    async with websockets.connect(WS) as ws:
        # 清掉历史缓冲里的事件
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.4)
        except asyncio.TimeoutError:
            pass

        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{BASE}/api/ai/test")
            print("AI 测试接口:", r.json())

        deadline = asyncio.get_event_loop().time() + 20
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("event") == "token_usage":
                got.append(msg["data"])

    print("\n收到 token_usage 推送:", len(got), "条")
    for item in got:
        print("  ", json.dumps(item, ensure_ascii=False))

    after = await get_usage()
    print("\n调用后 usage:", after)
    print("差值:", {
        k: after.get(k, 0) - before.get(k, 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "request_count")
    })

    p = Path("data/ai_usage.json")
    print("\n落盘文件:", p.resolve(), "存在=", p.exists())
    if p.exists():
        print("  内容:", p.read_text(encoding="utf-8").replace("\n", " "))
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
