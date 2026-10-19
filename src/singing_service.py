# -*- coding: utf-8 -*-
"""歌声服务：把「唱歌」从「念歌词」换成真正的歌声合成。

背景
----
机器人此前的「唱歌」其实是把歌词交给 Edge 在线语音、抬个音调念出来
（见 ``song_service.py`` 的 ``render_lyrics`` / ``render_song`` / ``preview``），
群成员的评价是准确的：那是在念。

本模块接的是本机已装好的 SoulX-Singer（零样本歌声合成，Apache-2.0）。
它**必须有旋律**才能唱，所以内置了一个小曲库（《小星星》《两只老虎》
《生日快乐》《欢乐颂》），按「字数 ↔ 音符数」把歌词对到曲调上。

为什么要起子进程
----------------
SoulX-Singer 跑在独立的 conda 环境（Python 3.10 + torch cu121），
与机器人自己的虚拟环境不兼容，所以只能以子进程方式调用 ``sing_once.py``。
代价是每次约 9～11 秒模型加载；用文件缓存把同一首歌的第二次点播变成零成本。

能力边界（不要对外承诺超出的部分）
----------------------------------
* 只能唱**内置曲库里的曲调**。群友完全自填的词，会优先找字数匹配的曲调
  唱一句；找不到就返回 ``None``，由调用方回退到原来的念唱或委婉拒绝。
* 单句歌词的字数必须 ≤ 该曲调乐句的音符数；超出会明确失败，不做静默降级。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("singing_service")

#: 唱一句最长等多久（含模型加载）。8GB 卡上 20 秒的歌实测约 7 秒推理，
#: 加 10 秒加载留足余量。
DEFAULT_TIMEOUT = 180.0

_TITLE_FILLER = (
    "唱一首", "唱首", "唱个", "唱一个", "来一首", "来首", "来段", "点歌",
    "点一首", "点首", "给我唱", "帮我唱", "唱一下", "唱", "的", "歌", "吧",
    "啊", "呀", "嘛", "哦", "呗", "呢", "谢谢", "听听", "我要听", "想听",
)


class SingingError(Exception):
    pass


class SingingService:
    """把歌词/歌名交给 SoulX-Singer 唱成音频。"""

    def __init__(
        self,
        python_exe: str,
        script: str,
        cache_dir: str | os.PathLike = "./data/song_cache/singing",
        enabled: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        mode: str = "melody",
    ) -> None:
        self.python_exe = str(python_exe or "")
        self.script = Path(script).resolve()
        self.workdir = self.script.parent
        # 必须用绝对路径：子进程的 cwd 是 SoulX-Singer 目录，
        # 相对路径会把产物写到那边去，父进程再按自己的 cwd 找不到（实测踩过）。
        self.cache_dir = Path(cache_dir).resolve()
        self.enabled = bool(enabled) and bool(self.python_exe)
        self.timeout = max(30.0, float(timeout))
        self.mode = mode if mode in ("melody", "score") else "melody"
        self._library: dict | None = None
        self._lock = asyncio.Lock()      # 单卡串行：同一时刻只跑一个推理
        self.stats = {"requests": 0, "rendered": 0, "cache_hits": 0, "failures": 0,
                      "last_seconds": 0.0}
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover
            logger.error(f"歌声缓存目录创建失败: {exc}")

    # ------------------------------------------------------------ 探测
    def available(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "歌声服务未启用"
        if not Path(self.python_exe).is_file():
            return False, f"找不到歌声环境的 python：{self.python_exe}"
        if not self.script.is_file():
            return False, f"找不到歌声脚本：{self.script}"
        return True, "就绪"

    def _run_sync(self, args: list[str]) -> tuple[int, str]:
        """同步执行 sing_once.py，返回 (退出码, 输出)。"""
        import subprocess

        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run(
            [self.python_exe, str(self.script), *args],
            cwd=str(self.workdir),
            capture_output=True,
            timeout=self.timeout,
            env=env,
        )
        out = b""
        try:
            out = (proc.stdout or b"") + (proc.stderr or b"")
            text = out.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            text = ""
        return proc.returncode, text

    async def _run(self, args: list[str]) -> tuple[int, str]:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._run_sync, args
        )

    # ------------------------------------------------------------ 曲库
    def library(self) -> dict:
        """读取内置曲库（子进程拿 JSON，缓存一份）。"""
        if self._library is not None:
            return self._library
        ok, why = self.available()
        if not ok:
            logger.warning(f"[歌声] 曲库不可用：{why}")
            self._library = {}
            return self._library
        try:
            code, text = self._run_sync(["--list-json"])
            if code != 0:
                logger.warning(f"[歌声] 读曲库失败（退出码 {code}）：{text[:300]}")
                self._library = {}
                return self._library
            # 取最后一行合法 JSON，避免前面的加载日志干扰
            payload = None
            for line in reversed(text.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    payload = json.loads(line)
                    break
            self._library = payload or {}
        except Exception as exc:  # pragma: no cover
            logger.error(f"[歌声] 读曲库异常：{exc}")
            self._library = {}
        return self._library

    def song_names(self) -> list[str]:
        return list(self.library().keys())

    def find_song(self, text: str) -> Optional[str]:
        """从一句话里认出内置曲库的歌名。"""
        raw = str(text or "").strip()
        if not raw:
            return None
        lib = self.library()
        for name in lib:
            if name in raw:
                return name
        # 去掉语气词后再试一次
        cleaned = raw
        for w in sorted(_TITLE_FILLER, key=len, reverse=True):
            cleaned = cleaned.replace(w, "")
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", cleaned)
        for name in lib:
            if cleaned == name:
                return name
        return None

    def pick_song_for_length(self, n_chars: int) -> Optional[str]:
        """按字数挑一首「第一句音符数够放」的曲调。"""
        lib = self.library()
        best = None
        for name, info in lib.items():
            first = info.get("first_notes", 0)
            if first >= n_chars and (best is None or first < best[1]):
                best = (name, first)
        return best[0] if best else None

    # ------------------------------------------------------------ 合成
    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.wav"

    async def sing_song(self, name: str) -> Optional[Path]:
        """整首唱一首内置曲库的曲子。"""
        if name not in self.library():
            logger.warning(f"[歌声] 曲库里没有《{name}》")
            return None
        return await self._render(f"song|{name}|{self.mode}", ["--song", name],
                                  f"《{name}》")

    async def sing_lyrics(self, text: str, song: Optional[str] = None) -> Optional[Path]:
        """用某首曲调唱一句自填歌词。"""
        lyric = re.sub(r"[，。！？、；：,.!?;:~\s]+", "", str(text or "").strip())
        if not lyric:
            return None
        name = song or self.pick_song_for_length(len(lyric))
        if not name:
            logger.info(f"[歌声] 没有能容纳 {len(lyric)} 字的曲调，"
                        f"自填词「{lyric}」不做歌声合成")
            return None
        return await self._render(
            f"lyrics|{name}|{lyric}|{self.mode}",
            ["--lyrics", lyric, "--tune", name],
            f"用《{name}》的调唱「{lyric}」")

    async def _render(self, cache_key: str, args: list[str], label: str) -> Optional[Path]:
        self.stats["requests"] += 1
        cached = self._cache_path(cache_key)
        if cached.is_file() and cached.stat().st_size > 1024:
            self.stats["cache_hits"] += 1
            return cached
        ok, why = self.available()
        if not ok:
            logger.warning(f"[歌声] 不可用：{why}")
            self.stats["failures"] += 1
            return None

        out = self._cache_path(cache_key + "|pending")
        args = [*args, "--out", str(out), "--mode", self.mode]
        try:
            async with self._lock:            # 单卡必须串行
                import time
                started = time.perf_counter()
                code, text = await self._run(args)
                elapsed = time.perf_counter() - started
            self.stats["last_seconds"] = round(elapsed, 2)
            if code != 0 or not out.is_file():
                tail = "\n".join(text.strip().splitlines()[-6:])
                logger.error(f"[歌声] {label} 失败（退出码 {code}）：{tail}")
                self.stats["failures"] += 1
                return None
            out.replace(cached)
            self.stats["rendered"] += 1
            logger.info(f"[歌声] {label} 完成，{elapsed:.1f} 秒 → {cached.name}")
            return cached
        except asyncio.TimeoutError:
            logger.error(f"[歌声] {label} 超时（>{self.timeout:.0f} 秒）")
            self.stats["failures"] += 1
            return None
        except Exception as exc:  # pragma: no cover
            logger.error(f"[歌声] {label} 异常：{exc}")
            self.stats["failures"] += 1
            return None

    # ------------------------------------------------------------ 状态
    def status(self) -> dict:
        ok, why = self.available()
        return {
            "enabled": self.enabled,
            "available": ok,
            "reason": why,
            "python": self.python_exe,
            "script": str(self.script),
            "cache_dir": str(self.cache_dir),
            "mode": self.mode,
            "songs": self.song_names(),
            **self.stats,
        }
