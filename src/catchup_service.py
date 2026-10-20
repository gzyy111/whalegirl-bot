# -*- coding: utf-8 -*-
"""开机补消息：把「睡着的时候攒下的话」用一句话接住。

为什么需要
----------
wechatauto 附着微信的瞬间，会把聊天窗口里原本就存在的消息整批回调进来
（实测一次约 200 条）。这类消息被标成 ``is_history=True``，只写进
``data/message_archive.jsonl`` 留档，**不进入回复管线**——本来是防止一开机
就把昨天的话全回一遍造成刷屏。

副作用是：晚上关机器人、早上开机，昨晚 @ 她的话一条都没接，群里看起来像
她睡死过去了。这个模块补的就是这一段。

做法
----
1. **收集**：只收 ``is_history`` 且「确实在找她」的消息——群聊要求 @ 她或
   拍一拍、且属于监控群；私聊默认全收（可关）。纯图片/语音占位符跳过。
2. **等待**：回放窗口结束后（``replay_grace_seconds`` + 缓冲）一次性处理，
   避免边回放边发消息。
3. **一句话**：每个会话只发**一句**。把攒下的消息交给模型，让它用傲娇口吻
   「接住」，明确要求不逐条回答；模型不可用时用固定话术，绝不因此不发。
4. **去重**：按「会话 + 发送者 + 内容」哈希记账（``data/catchup_state.json``）。
   同一句话不会因为重启两次被接两次；只有新消息才发。

关闭方式：``wechat.catchup.enabled: false``，行为回到改造前。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

# 系统提示：只约束语气，不重复人设的全部设定（人设在 styles/ 里锁定）
SYSTEM_PROMPT = (
    "你是「傲娇鲸鱼娘」，一个在微信群里聊天的 AI，原型是 DeepSeek 的蓝鲸。"
    "说话短、口语化、带一点傲娇和懒，不用书面语，不列条目，不解释自己在做什么。"
)

TASK_PROMPT = (
    "你刚睡醒，下面这些是**你睡着的时候**收到的消息：\n\n{lines}\n\n"
    "请用**一句话**把它们接住：不超过 {max_chars} 个字，只输出这一句。\n"
    "要求：不逐条回答、不编号、不罗列；可以承认自己刚睡醒；"
    "不要出现「主动发言」「任务」「收到 N 条」这类报表腔；不要 @ 任何人。"
)

# 模型不可用时的兜底：仍然是「一句」，不会变成逐条回复
FALLBACKS = (
    "呜……本鲸鱼娘刚睡醒，攒下的 {n} 条我都看到了，缓一下就来。",
    "才睡了一觉就攒了 {n} 条，你们还真能说，本鲸鱼娘这就一条条看。",
    "本鲸鱼娘醒了，{n} 条话先记在这儿，别急，我慢慢回。",
    "深海信号刚恢复，{n} 条消息都收到了，等我冒个泡就回你们。",
)

# 没有未接消息时的「开机问候」：只冒一句，不追着聊
GREETING_PROMPT = (
    "你刚刚回到群里（开机了）。请用一句话冒个泡打个招呼："
    "不超过 {max_chars} 个字，只输出这一句。\n"
    "要求：不要提「启动」「程序」「重启」「开机」这些字眼；不要 @ 任何人；"
    "不要罗列你在做什么，不要提问过多。"
)

GREETING_FALLBACKS = (
    "本鲸鱼娘醒了，尾巴拍了拍水花，有人在吗？",
    "冒个泡……本鲸鱼娘回来了，有事直接说。",
    "刚醒，深海信号正常，本鲸鱼娘上来看一眼。",
)

# 这些内容只有占位符、没有可回应的话，不参与补消息
SKIP_CONTENTS = {
    "[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[动画表情]",
    "[位置]", "[链接]", "[小程序]", "[转账]", "[红包]",
}


@dataclass
class _Missed:
    """一条「睡着时没接住」的消息。"""

    room: str
    sender: str
    content: str
    key: str
    at_me: bool = False
    poke: bool = False
    private: bool = False


class CatchupService:
    """开机补消息：收集历史回放里找她的消息，每个会话回一句。"""

    MAX_ACKED = 800          # 记账上限，超出按时间淘汰
    ACK_TTL = 7 * 86400      # 记账有效期（7 天）
    PENDING_TTL = 48 * 3600  # 没发出去的消息最多留 48 小时
    MAX_ARCHIVE_BYTES = 8 * 1024 * 1024   # 读归档只读末尾这么多

    def __init__(
        self,
        wechat,
        ai=None,
        *,
        enabled: bool = True,
        only_at_me: bool = False,
        include_private: bool = True,
        max_messages: int = 40,
        max_groups: int = 3,
        max_chars: int = 60,
        min_messages: int = 3,
        min_offline_minutes: float = 60.0,
        greet_min_gap_minutes: float = 10.0,
        delay_after_connect: float = 9.0,
        greet_without_messages: bool = True,
        state_path: str = "data/catchup_state.json",
        archive_path: str = "data/message_archive.jsonl",
        pending_path: str = "data/catchup_pending.json",
        skip_seen_live: bool = True,
        dry_run: bool = False,
    ) -> None:
        self.wechat = wechat
        self.ai = ai
        self.enabled = bool(enabled)
        self.only_at_me = bool(only_at_me)
        self.include_private = bool(include_private)
        self.max_messages = max(1, int(max_messages))
        self.max_groups = max(1, int(max_groups))
        self.max_chars = max(12, int(max_chars))
        self.min_messages = max(1, int(min_messages))
        self.min_offline_minutes = max(0.0, float(min_offline_minutes))
        self.greet_min_gap_minutes = max(0.0, float(greet_min_gap_minutes))
        self.delay_after_connect = max(0.0, float(delay_after_connect))
        self.greet_without_messages = bool(greet_without_messages)
        self.state_path = Path(state_path)
        self.archive_path = Path(archive_path)
        self.pending_path = Path(pending_path)
        self.skip_seen_live = bool(skip_seen_live)
        self.dry_run = bool(dry_run)

        self._missed: dict[str, list[_Missed]] = {}
        self._acked: dict[str, float] = {}
        self._seen_live: set[str] = set()
        self._running = False
        self._last_flush: float = 0.0
        self._last_start: float = 0.0
        self._offline_minutes: float = 0.0
        self._eligible = False
        self._greet_ok = False
        self._load_state()
        if self.skip_seen_live:
            self._load_seen_live()
        self._load_pending()
        # 判定这次启动算不算「睡了一觉回来」，并立刻刷新「上次存活时间」，
        # 这样同一天里再重启一次不会重复补消息。
        self._eligible = self._check_eligible()
        self._save_state()

    # ── 状态 ────────────────────────────────────────────────
    def _load_state(self) -> None:
        try:
            if not self.state_path.is_file():
                return
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            acked = data.get("acked") or {}
            if isinstance(acked, dict):
                now = time.time()
                self._acked = {
                    str(k): float(v)
                    for k, v in acked.items()
                    if now - float(v) <= self.ACK_TTL
                }
            self._last_flush = float(data.get("last_flush") or 0.0)
            self._last_start = float(data.get("last_start") or 0.0)
        except Exception as exc:  # 状态坏了不影响主链路
            logger.warning(f"[开机补消息] 读取状态失败，按空处理: {exc}")
            self._acked = {}

    def _check_eligible(self) -> bool:
        """判断这次启动要不要补消息。

        规则：首次运行（没有历史记录）补；距上次启动超过
        ``min_offline_minutes`` 也补；达不到就跳过——手动重启两次不该
        每次都「刚睡醒」一句。
        """
        now = time.time()
        if self._last_start <= 0:
            self._offline_minutes = 0.0
            self._last_start = now
            self._greet_ok = True          # 首次运行也算一次开机
            return True
        self._offline_minutes = max(0.0, (now - self._last_start) / 60.0)
        # 问候的闸门比补消息松：用户要的是「开机就冒一句」，
        # 只要不是一分钟内连点几次启动，就该说话。
        self._greet_ok = self._offline_minutes >= self.greet_min_gap_minutes
        if self._offline_minutes < self.min_offline_minutes:
            # 注意：这里**不刷新** last_start，否则每次手动重启都把计时归零，
            # 一天里连点几次启动就永远凑不满阈值（功能会被饿死）。
            logger.info(
                "[开机补消息] 距上次启动只有 %.0f 分钟（阈值 %.0f），本次不补"
                % (self._offline_minutes, self.min_offline_minutes)
            )
            return False
        self._last_start = now
        return True

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            if len(self._acked) > self.MAX_ACKED:
                kept = sorted(self._acked.items(), key=lambda kv: kv[1])[-self.MAX_ACKED:]
                self._acked = dict(kept)
            payload = {
                "acked": self._acked,
                "last_flush": self._last_flush,
                "last_start": self._last_start,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.state_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[开机补消息] 保存状态失败: {exc}")

    # ── 没发出去的，落盘等下次 ──────────────────────────────
    def _load_pending(self) -> None:
        """把上次没发出去的消息接回来。

        为什么需要：回放窗口里的消息只会在启动那一次被推过来。如果这次因为
        「启动间隔太短」被闸门挡掉，内存里的东西一清就永远没了。落盘后下次
        启动还能补上。
        """
        try:
            if not self.pending_path.is_file():
                return
            data = json.loads(self.pending_path.read_text(encoding="utf-8"))
            now = time.time()
            restored = 0
            for rec in data.get("items", []):
                try:
                    saved_at = float(rec.get("saved_at") or 0)
                    if now - saved_at > self.PENDING_TTL:
                        continue
                    room = str(rec.get("room") or "")
                    content = str(rec.get("content") or "")
                    if not room or not content:
                        continue
                    item = _Missed(
                        room=room,
                        sender=str(rec.get("sender") or ""),
                        content=content,
                        key=str(rec.get("key") or ""),
                        at_me=bool(rec.get("at_me")),
                        poke=bool(rec.get("poke")),
                        private=bool(rec.get("private")),
                    )
                    if not item.key:
                        item.key = self._key(room, item.sender, content)
                    if item.key in self._acked:
                        continue
                    bucket = self._missed.setdefault(room, [])
                    if any(x.key == item.key for x in bucket):
                        continue
                    bucket.append(item)
                    restored += 1
                except Exception:
                    continue
            if restored:
                logger.info(f"[开机补消息] 恢复上次没发出去的 {restored} 条")
        except Exception as exc:
            logger.debug(f"[开机补消息] 读取待补队列失败（忽略）: {exc}")

    def _save_pending(self) -> None:
        """把仍在队列里、尚未确认发出的消息写盘。"""
        now = time.time()
        items = []
        for bucket in self._missed.values():
            for item in bucket:
                if item.key in self._acked:
                    continue
                items.append(
                    {
                        "room": item.room,
                        "sender": item.sender,
                        "content": item.content,
                        "key": item.key,
                        "at_me": item.at_me,
                        "poke": item.poke,
                        "private": item.private,
                        "saved_at": now,
                    }
                )
        try:
            if not items:
                if self.pending_path.is_file():
                    self.pending_path.unlink()
                return
            self.pending_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "items": items,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.pending_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[开机补消息] 保存待补队列失败: {exc}")

    # ── 收集 ────────────────────────────────────────────────
    def _load_seen_live(self) -> None:
        """读归档，记下「当时在线收过的消息」。

        这些消息当时已经进过回复管线（回没回由她决定），开机回放时不该再被
        当成「睡着时没接住的话」。只读归档末尾若干 MB，避免启动变慢。
        """
        try:
            if not self.archive_path.is_file():
                return
            with self.archive_path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size > self.MAX_ARCHIVE_BYTES:
                    fh.seek(size - self.MAX_ARCHIVE_BYTES)
                    fh.readline()          # 丢掉被截断的半行
                else:
                    fh.seek(0)             # 小文件从头上读（seek 到 END 后必须回来）
                raw = fh.read().decode("utf-8", "replace")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("history"):
                    continue               # 历史回放本身不算「在线收过」
                room = str(rec.get("room") or "")
                sender = str(rec.get("sender") or "")
                content = str(rec.get("content") or "").strip()
                if room and content:
                    self._seen_live.add(self._key(room, sender, content))
            if self._seen_live:
                logger.info(
                    f"[开机补消息] 归档里 {len(self._seen_live)} 条是当时在线收过的，不再重复补"
                )
        except Exception as exc:
            logger.debug(f"[开机补消息] 读取归档失败（忽略）: {exc}")

    @staticmethod
    def _key(room: str, sender: str, content: str) -> str:
        raw = f"{room}|{sender}|{content}".encode("utf-8", "replace")
        return hashlib.sha1(raw).hexdigest()[:20]

    def note(self, message) -> bool:
        """收录一条历史消息；返回是否被收下（便于测试与日志）。"""
        if not self.enabled:
            return False
        if getattr(message, "is_self", False):
            return False
        if not getattr(message, "is_history", False):
            return False

        content = str(getattr(message, "content", "") or "").strip()
        if not content or content in SKIP_CONTENTS:
            return False

        is_group = bool(getattr(message, "is_group", False))
        at_me = bool(getattr(message, "is_at_me", False))
        poke = bool(getattr(message, "is_poke_me", False))
        if is_group:
            if not bool(getattr(message, "is_target_group", True)):
                return False
            if self.only_at_me and not (at_me or poke):
                return False
        else:
            if not self.include_private:
                return False
            at_me = poke = False

        room = str(
            getattr(message, "room_name", "")
            or getattr(message, "room_id", "")
            or ""
        ).strip()
        if not room:
            return False
        sender = str(
            getattr(message, "sender_name", "")
            or getattr(message, "sender", "")
            or ""
        ).strip()

        key = self._key(room, sender, content)
        if self.skip_seen_live and key in self._seen_live:
            logger.debug(
                f"[开机补消息] 跳过当时已在线的消息 {sender}: {content[:30]}"
            )
            return False
        bucket = self._missed.setdefault(room, [])
        if any(item.key == key for item in bucket):
            return False
        bucket.append(
            _Missed(
                room=room,
                sender=sender,
                content=content,
                key=key,
                at_me=at_me,
                poke=poke,
                private=not is_group,
            )
        )
        # 只保留最近 N 条，避免一次回放几百条把提示词撑爆
        if len(bucket) > self.max_messages:
            del bucket[: len(bucket) - self.max_messages]
        return True

    def pending(self) -> dict[str, int]:
        """当前攒下的会话与条数（诊断用，不发消息）。"""
        return {room: len(items) for room, items in self._missed.items() if items}

    @property
    def last_flush_time(self) -> str:
        if not self._last_flush:
            return ""
        return datetime.fromtimestamp(self._last_flush).isoformat(timespec="seconds")

    # ── 发送 ────────────────────────────────────────────────
    def _lines(self, items: list[_Missed], limit: int = 8) -> str:
        lines = []
        for item in items[:limit]:
            body = item.content.replace("\n", " ")[:50]
            prefix = f"{item.sender}：" if item.sender else ""
            tag = "（拍一拍）" if item.poke else ""
            lines.append(f"- {prefix}{body}{tag}")
        if len(items) > limit:
            lines.append(f"- 另有 {len(items) - limit} 条没列出来")
        return "\n".join(lines)

    def _fallback(self, items: list[_Missed]) -> str:
        return random.choice(FALLBACKS).format(n=len(items))

    def _one_sentence(self, text: str) -> str:
        """把模型输出压成一句话：去引号、取首句、限长。"""
        text = (text or "").strip()
        if not text:
            return ""
        # 只取第一行（模型偶尔会分行输出）
        text = text.splitlines()[0].strip()
        # 去掉包裹的引号/书名号/代码块标记
        text = text.strip("\"'“”‘’「」『』`*　 ")
        # 只保留第一句
        for mark in ("。", "！", "？", "!", "?", "\n"):
            idx = text.find(mark)
            if 0 <= idx < len(text) - 1:
                text = text[: idx + 1]
                break
        text = text.strip()
        if len(text) > self.max_chars:
            text = text[: self.max_chars].rstrip("，,；;：: ")
            if not text.endswith(("。", "！", "？", "~", "～")):
                text += "。"
        return text

    async def _compose(self, items: list[_Missed]) -> str:
        """生成「一句话」；模型不可用或失败时退回固定话术。"""
        ai = self.ai
        if ai is not None and bool(getattr(ai, "is_available", False)):
            try:
                raw = await ai.chat(
                    [
                        {
                            "role": "user",
                            "content": TASK_PROMPT.format(
                                max_chars=self.max_chars,
                                lines=self._lines(items),
                            ),
                        }
                    ],
                    system_prompt=SYSTEM_PROMPT,
                    temperature=0.9,
                    max_tokens=160,
                )
                sentence = self._one_sentence(raw)
                if sentence:
                    return sentence
            except Exception as exc:
                logger.warning(f"[开机补消息] 生成一句话失败，用兜底话术: {exc}")
        return self._fallback(items)

    # ── 没有消息时的开机问候 ────────────────────────────────
    def _greet_targets(self) -> list[str]:
        """问候发到哪：优先监控群，没有就用收集到的会话。"""
        groups = [
            str(g).strip()
            for g in (getattr(self.wechat, "target_groups", []) or [])
            if str(g).strip()
        ]
        if not groups:
            groups = [room for room in self._missed.keys() if room]
        return groups[: self.max_groups]

    async def _compose_greeting(self) -> str:
        ai = self.ai
        if ai is not None and bool(getattr(ai, "is_available", False)):
            try:
                raw = await ai.chat(
                    [
                        {
                            "role": "user",
                            "content": GREETING_PROMPT.format(
                                max_chars=self.max_chars
                            ),
                        }
                    ],
                    system_prompt=SYSTEM_PROMPT,
                    temperature=0.9,
                    max_tokens=120,
                )
                sentence = self._one_sentence(raw)
                if sentence:
                    return sentence
            except Exception as exc:
                logger.warning(f"[开机补消息] 生成问候失败，用兜底话术: {exc}")
        return random.choice(GREETING_FALLBACKS)

    async def _greet_groups(self, results: list[dict]) -> None:
        targets = self._greet_targets()
        if not targets:
            logger.info("[开机补消息] 没有可问候的会话（target_groups 为空）")
            return
        text = await self._compose_greeting()
        if not text:
            return
        for index, room in enumerate(targets):
            ok = True
            if self.dry_run:
                logger.info(f"[开机补消息][dry-run][问候] {room}: {text}")
            else:
                try:
                    ok = bool(await self.wechat.send_text(room, text))
                except Exception as exc:
                    ok = False
                    logger.warning(f"[开机补消息] 问候发送失败 [{room}]: {exc}")
            if ok:
                now = time.time()
                self._last_start = now
                self._last_flush = now
            results.append(
                {"room": room, "count": 0, "text": text, "ok": ok, "greeting": True}
            )
            logger.info(
                f"[开机补消息] {room}：开机问候 -> {text!r}"
                f"（{'已发' if ok else '发送失败'}）"
            )
            if index < len(targets) - 1:
                await asyncio.sleep(1.2)

    async def flush(self) -> list[dict]:
        """把攒下的消息一次性接住；返回每个会话的处理结果。

        判定分两种：
        * 有人 @ 她 / 拍一拍 / 私聊 —— **无条件接**，不受启动间隔闸门限制
          （停机期间有人叫她，起来必须应一声，否则就是"装死"）；
        * 群里没人叫 —— 要同时满足「距上次启动够久」与「攒够 min_messages」。
        """
        if not self.enabled or self._running:
            return []
        rooms = [room for room, items in self._missed.items() if items]
        # 没有攒下任何消息时不直接返回：开机问候是独立的一件事。
        # 问候有自己的闸门（greet_min_gap_minutes），比补消息松。
        if not rooms and not (self.greet_without_messages and self._greet_ok):
            return []

        self._running = True
        results: list[dict] = []
        sent_any = False
        try:
            rooms.sort(key=lambda r: len(self._missed.get(r, [])), reverse=True)
            selected = rooms[: self.max_groups]
            for index, room in enumerate(selected):
                items = [i for i in self._missed.get(room, []) if i.key not in self._acked]
                if not items:
                    logger.debug(f"[开机补消息] {room} 的消息此前已接过，跳过")
                    continue

                # 有人 @ 她 / 拍一拍 / 私聊找她 -> 无条件必接；
                # 群里没人叫她 -> 既要启动间隔够久，也要攒够 min_messages
                called = any(i.at_me or i.poke or i.private for i in items)
                if not called:
                    if not self._eligible:
                        logger.info(
                            f"[开机补消息] {room} 没人叫她，且距上次启动只有 "
                            f"{self._offline_minutes:.0f} 分钟（阈值 "
                            f"{self.min_offline_minutes:.0f}），本次不冒泡"
                        )
                        continue
                    if len(items) < self.min_messages:
                        logger.info(
                            f"[开机补消息] {room} 只有 {len(items)} 条且没人叫她，"
                            f"这次不冒泡（阈值 {self.min_messages}）"
                        )
                        continue

                text = await self._compose(items)
                if not text:
                    continue

                ok = True
                if self.dry_run:
                    logger.info(f"[开机补消息][dry-run] {room}: {text}")
                else:
                    try:
                        ok = bool(await self.wechat.send_text(room, text))
                    except Exception as exc:
                        ok = False
                        logger.warning(f"[开机补消息] 发送失败 [{room}]: {exc}")

                if ok:
                    now = time.time()
                    for item in items:
                        self._acked[item.key] = now
                    self._last_flush = now
                    self._last_start = now      # 真补过，闸门从这一刻重新计时

                results.append(
                    {"room": room, "count": len(items), "text": text, "ok": ok}
                )
                if ok:
                    sent_any = True
                logger.info(
                    f"[开机补消息] {room}：攒了 {len(items)} 条 -> "
                    f"{text!r}（{'已发' if ok else '发送失败'}）"
                )
                # 多会话之间隔一下，别一开机连发；最后一个不用等
                if index < len(selected) - 1:
                    await asyncio.sleep(1.2)

            # 没有任何消息可补，但确实是「睡了一觉回来」——冒一句开机问候
            if not sent_any and not rooms and self.greet_without_messages:
                await self._greet_groups(results)
            self._save_state()
        finally:
            self._running = False
            # 没发出去的（被闸门跳过 / 发送失败）落盘，下次启动还能补
            self._save_pending()
            self._missed.clear()
        return results
