# -*- coding: utf-8 -*-
"""ACE-Step 服务：把「歌词」直接变成一首带编曲的歌。

与 singing_service 的区别
-------------------------
* ``singing_service`` 用 SoulX-Singer：**必须给旋律**，只能唱我们内置曲调的
  干声，音色是鲸鱼娘，但需要伴奏与混音才像个作品。
* 本模块用 ACE-Step：**只要歌词**，模型自己作曲、编曲、演唱，一次出成品
  （48kHz 立体声）。代价是音色不可指定、同一歌词每次结果不同（官方称
  "gacha-style"）。
所以「@鲸鱼娘 唱歌」这条路径优先走 ACE-Step，失败再退回 SoulX-Singer，
最后退回抬调朗读。

运行前提
--------
ACE-Step 是独立 conda 环境里的 FastAPI 服务（默认 8001 端口）。本模块会：
  1. 先探 /health；
  2. 不通且 auto_start 开启时，用 detached 方式拉起服务进程；
  3. 服务端其实会把音频写到仓库的 .cache 目录，若 HTTP 下载失败（实测
     /v1/audio 返回的 URL 偶发主机名不可解析），直接读本地文件兜底。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("acestep_service")

DEFAULT_CAPTION = "pop, ballad, female vocal, piano, warm, gentle"

#: 只在这几种失败上重试/兜底，避免把逻辑写复杂
_START_FLAGS = 0
if sys.platform == "win32":
    _START_FLAGS = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW


def _unwrap(obj):
    """API 响应外面套了一层 {data, code, error, timestamp}。"""
    if isinstance(obj, dict) and "data" in obj and ("code" in obj or "error" in obj):
        return obj["data"]
    return obj


class ServerDown(Exception):
    """ACE 服务进程已经不在了（连接被拒）。

    为什么单独定义：实测服务端连续生成几首后会原生崩溃（已知问题），
    若继续按普通超时轮询，会白等满 timeout（最坏 300 秒）才回退，
    对聊天场景完全不可接受。宁可 15 秒内判定死亡、重启后重试一次。
    """


class ACEStepService:
    """歌词 → 30 秒带编曲的中文歌（走 ACE-Step 的 HTTP API）。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8001",
        cache_dir: str | os.PathLike = "./data/song_cache/acestep",
        enabled: bool = True,
        duration: float = 30.0,
        caption: str = DEFAULT_CAPTION,
        timeout: float = 300.0,
        auto_start: bool = True,
        python_exe: str = "",
        repo: str = "",
        lm_model: str = "acestep-5Hz-lm-0.6B",
        dit_model: str = "acestep-v15-turbo",
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.cache_dir = Path(cache_dir).resolve()
        self.enabled = bool(enabled) and bool(self.base_url)
        self.duration = max(10.0, float(duration))
        self.caption = str(caption or DEFAULT_CAPTION)
        self.timeout = max(60.0, float(timeout))
        self.auto_start = bool(auto_start)
        self.python_exe = str(python_exe or "")
        self.repo = Path(repo).resolve() if repo else None
        self.lm_model = lm_model
        self.dit_model = dit_model
        self._lock = asyncio.Lock()      # 单卡串行
        self._server = None              # 我们拉起的服务进程
        self._starting = False
        self.stats = {"requests": 0, "rendered": 0, "cache_hits": 0, "failures": 0,
                      "last_seconds": 0.0}
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover
            logger.error(f"ACE 缓存目录创建失败: {exc}")

    # ------------------------------------------------------------ 环境
    def _health(self, timeout: float = 4.0) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def available(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "ACE-Step 未启用"
        if self._health():
            return True, "就绪"
        return False, "ACE-Step 服务未运行"

    def _start_server(self) -> bool:
        """拉起 API 服务（detached，随宿主存活）。"""
        if self._starting:
            return False
        if not self.python_exe or self.repo is None:
            logger.warning("[ACE] 未配置 python_exe / repo，无法自动启动服务")
            return False
        if not Path(self.python_exe).is_file() or not self.repo.is_dir():
            logger.warning("[ACE] python 或仓库路径不存在，无法自动启动")
            return False
        self._starting = True
        env = dict(os.environ)
        env.update({
            "ACESTEP_CHECKPOINTS_DIR": str(self.repo / "checkpoints"),
            "ACESTEP_CONFIG_PATH": self.dit_model,
            "ACESTEP_LM_MODEL_PATH": self.lm_model,
            "ACESTEP_LM_BACKEND": "pt",
            # 8GB 显存：两个卸载开关必须都开，否则服务端 OOM
            "ACESTEP_OFFLOAD_TO_CPU": "true",
            "ACESTEP_OFFLOAD_DIT_TO_CPU": "true",
            "PYTHONPATH": str(self.repo),
            "PYTHONIOENCODING": "utf-8",
        })
        log = self.cache_dir / "acestep_server.log"
        try:
            self._server = subprocess.Popen(
                [self.python_exe, "-m", "acestep.api_server", "--port",
                 str(urllib.parse.urlparse(self.base_url).port or 8001)],
                cwd=str(self.repo), env=env, creationflags=_START_FLAGS,
                stdout=open(log, "ab"), stderr=subprocess.STDOUT,
            )
            logger.info(f"[ACE] 已拉起服务 pid={self._server.pid}，日志 {log}")
        except Exception as exc:  # pragma: no cover
            logger.error(f"[ACE] 拉起服务失败: {exc}")
            self._starting = False
            return False
        self._starting = False
        return True

    def _wait_ready(self, seconds: float = 90.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < seconds:
            if self._health(2.0):
                return True
            time.sleep(2)
        return False

    # ------------------------------------------------------------ HTTP
    def _post(self, path: str, payload: dict, timeout: float = 60.0):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _unwrap(json.load(r))

    def _run_sync(self, lyrics: str, caption: str, out: Path) -> Optional[Path]:
        """提交 → 轮询 → 取文件。整段同步，由上层丢进线程池。"""
        payload = {
            "prompt": caption,
            "lyrics": lyrics,
            "vocal_language": "zh",          # 默认是 en，中文必须显式给
            "thinking": True,
            "model": self.dit_model,
            "lm_model_path": self.lm_model,
            "lm_backend": "pt",
            "inference_steps": 8,            # turbo 档推荐 8 步
            "audio_duration": self.duration,
            "batch_size": 1,                 # 8GB 显存固定 1
            "audio_format": "wav",           # 环境里没有 ffmpeg，mp3 会导出失败
            "use_random_seed": True,
            "constrained_decoding": True,
        }
        r = self._post("/release_task", payload)
        tid = r.get("task_id")
        if not tid:
            logger.error(f"[ACE] 未拿到 task_id: {r}")
            return None
        logger.info(f"[ACE] 已提交 task={tid} 队列位置={r.get('queue_position')}")

        deadline = time.time() + self.timeout
        item = None
        dead = 0
        while time.time() < deadline:
            time.sleep(4)
            try:
                q = self._post("/query_result", {"task_id_list": [tid]})
                dead = 0
            except Exception as exc:  # noqa: BLE001
                dead += 1
                logger.debug(f"[ACE] 查询异常（连续 {dead} 次）: {exc}")
                if dead >= 3:
                    raise ServerDown(str(exc)) from exc
                continue
            items = q if isinstance(q, list) else [q]
            for it in items:
                if str(it.get("task_id")) != str(tid):
                    continue
                st = it.get("status")
                if st == 1:
                    raw = it.get("result") or "[]"
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    item = parsed[0] if isinstance(parsed, list) and parsed else None
                elif st == 2:
                    logger.error(f"[ACE] 任务失败: {it.get('result')}")
                    return None
            if item:
                break
        if not item:
            logger.error("[ACE] 轮询超时")
            return None

        file_url = item.get("file", "")
        url = file_url if file_url.startswith("http") else self.base_url + file_url
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                out.write_bytes(resp.read())
        except Exception as exc:  # noqa: BLE001
            # 实测：/v1/audio 的 URL 偶发主机名不可解析。服务端已落盘，直接读。
            logger.warning(f"[ACE] HTTP 取音频失败（{exc}），改读服务端本地文件")
            qs = urllib.parse.urlparse(file_url).query
            local = urllib.parse.parse_qs(qs).get("path", [""])[0]
            cand = Path(local) if local else None
            if cand is None or not cand.is_file():
                pool = []
                root = (self.repo or Path(".")) / ".cache" / "acestep" / "tmp" / "api_audio"
                if root.is_dir():
                    pool = sorted(root.glob("*.wav"),
                                  key=lambda p: p.stat().st_mtime, reverse=True)
                cand = pool[0] if pool else None
            if cand is None:
                logger.error("[ACE] 找不到本地音频文件")
                return None
            out.write_bytes(cand.read_bytes())
        logger.info(f"[ACE] 成品已保存 {out.name} ({out.stat().st_size/1024/1024:.1f} MB)")
        # 写同名 sidecar：缓存文件是哈希名，光看文件名认不出是哪首歌，
        # 排查/抽样时极易取错（实测吃过这个亏）。把歌词与风格落盘可溯源。
        try:
            out.with_suffix(".json").write_text(
                json.dumps({
                    "task_id": tid,
                    "caption": caption,
                    "duration": self.duration,
                    "lyrics": lyrics,
                    "bpm": (item.get("metas") or {}).get("bpm"),
                    "seed": item.get("seed_value"),
                    "dit_model": item.get("dit_model"),
                    "lm_model": item.get("lm_model"),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            logger.debug(f"[ACE] 写 sidecar 失败: {exc}")
        return out

    # ------------------------------------------------------------ 对外
    def _cache_path(self, lyrics: str, caption: str) -> Path:
        key = f"{caption}|{self.duration:.0f}|{lyrics}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.wav"

    async def sing(self, lyrics: str, caption: str = "") -> Optional[Path]:
        """歌词 → 一首歌。失败返回 None，由调用方决定回退。

        :param caption: 覆盖本次的风格描述（默认用配置里的）
        """
        text = self._normalize_lyrics(lyrics)
        if not text:
            return None
        cap = str(caption or self.caption).strip() or DEFAULT_CAPTION
        self.stats["requests"] += 1
        cached = self._cache_path(text, cap)
        if cached.is_file() and cached.stat().st_size > 10240:
            self.stats["cache_hits"] += 1
            logger.info(f"[ACE] 命中缓存 {cached.name}")
            return cached

        ok, why = self.available()
        if not ok:
            if self.auto_start and self._start_server():
                logger.info("[ACE] 等待服务加载模型（首次约 1-3 分钟）…")
                ok = self._wait_ready(240)
                why = "就绪" if ok else "启动后仍未就绪"
            if not ok:
                logger.warning(f"[ACE] 不可用：{why}")
                self.stats["failures"] += 1
                return None

        out = cached.with_suffix(".part.wav")
        for attempt in (1, 2):
            try:
                async with self._lock:
                    t0 = time.time()
                    got = await asyncio.get_running_loop().run_in_executor(
                        None, self._run_sync, text, cap, out)
                    elapsed = time.time() - t0
                self.stats["last_seconds"] = round(elapsed, 2)
                if got is None or not got.is_file() or got.stat().st_size < 10240:
                    self.stats["failures"] += 1
                    return None
                got.replace(cached)
                # sidecar 写在 .part 名上，这里跟着改成正式名
                side = got.with_suffix(".json")
                if side.is_file():
                    try:
                        side.replace(cached.with_suffix(".json"))
                    except Exception:  # pragma: no cover
                        pass
                self.stats["rendered"] += 1
                logger.info(f"[ACE] 生成完成，{elapsed:.0f} 秒 → {cached.name}")
                return cached
            except ServerDown as exc:
                # 已知崩溃：重启服务后重试一次；第二次还崩就放弃，交给上层回退
                logger.warning(f"[ACE] 服务已崩溃（{exc}），第 {attempt} 次尝试重启")
                self._server = None
                if attempt == 1 and self.auto_start and self._start_server():
                    if self._wait_ready(240):
                        logger.info("[ACE] 服务已重启，重试生成")
                        continue
                self.stats["failures"] += 1
                return None
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[ACE] 生成异常: {exc}")
                self.stats["failures"] += 1
                return None
        self.stats["failures"] += 1
        return None

    @staticmethod
    def _normalize_lyrics(text: str) -> str:
        """把一行歌词按标点切成多行 —— ACE-Step 用换行分句，单行长句效果差。"""
        raw = str(text or "").strip()
        if not raw:
            return ""
        if "\n" in raw:
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
        else:
            import re
            parts = re.split(r"[，,。.！!？?；;、]+", raw)
            lines = [p.strip() for p in parts if p.strip()]
            # 太长的句子再按字数硬切，每行不超过 12 字
            if len(lines) <= 1:
                s = lines[0] if lines else raw
                lines = [s[i:i + 12] for i in range(0, len(s), 12)]
        return "\n".join(lines[:8])

    def status(self) -> dict:
        ok, why = self.available()
        return {
            "enabled": self.enabled,
            "available": ok,
            "reason": why,
            "base_url": self.base_url,
            "duration": self.duration,
            "caption": self.caption,
            "cache_dir": str(self.cache_dir),
            **self.stats,
        }
