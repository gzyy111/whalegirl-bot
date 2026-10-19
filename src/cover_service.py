# -*- coding: utf-8 -*-
"""点歌真唱服务：把任意一首本地歌曲，用鲸鱼娘的音色唱出来。

与 SongService 的分工很清楚：

* ``SongService`` 管「唱什么」——曲库、意图解析、TTS / ACE-Step 兜底；
* ``CoverService`` 管「怎么唱成她的声音」——SVC 翻唱、GPU 串行队列、缓存。

调用链：群友 @鲸鱼娘 「唱首晴天」→ CoverService.resolve() 解析出歌名与
长度模式 → render() 排队跑 cover.py（demucs 分轨 → SVC 换音色 → 混音）
→ 返回 mp3 路径 → 由 message_handler 发出去。

设计要点：

1. **GPU 串行**。8 GB 显存一次只跑一个 SVC，用 ``asyncio.Semaphore(1)``
   保证；排队中的请求能拿到预计等待时间，好回复群友。
2. **缓存按「歌 + 长度模式 + 音色版本」**。同一首歌唱过一次就不再占卡，
   群友催单时秒回。
3. **不碰 SongService**。这个模块只加不减，原来的点歌、唱歌、TTS 兜底
   逻辑一条都没改，出问题可以只关 ``cover.enabled`` 回退。
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: 音频后缀。QQ 音乐下载的是 .ogg，其它来源多在 mp3 / flac / m4a。
AUDIO_EXTS = (".ogg", ".mp3", ".flac", ".m4a", ".wav", ".ape", ".aac")

#: 曲库目录里可选的别名表（同一个歌的其它叫法），见 _load_aliases()
ALIAS_FILE = "别名.json"

#: 触发「她唱歌」的说法。与 song_service.PLAY_TRIGGERS 保持同源，
#: 但这里只在**能解析出歌名**时才接管，所以多一点少一点不影响兜底。
PLAY_HINTS = (
    "点歌", "点一首", "点首", "来一首", "来首", "来一个", "来一段", "来段",
    "唱首歌", "唱一首", "唱首", "唱一个", "唱个歌", "唱支歌", "唱一段",
    "唱两句", "唱一下", "唱歌", "唱给我", "给我唱", "帮我唱", "献唱",
    "整一首", "整首", "我要听", "想听", "来唱", "唱",
)

#: 只要一段
SEGMENT_HINTS = (
    "唱一段", "来一段", "一段", "两句", "唱两句", "副歌", "高潮", "片段",
    "一点点", "一小段", "短一点", "短的", "来点",
)

#: 要整首
FULL_HINTS = (
    "整首", "整一首", "全唱", "唱完", "全部唱", "完整", "完整的", "整首歌",
    "从头唱", "从头到尾", "都唱", "全部", "整曲", "唱全",
)

#: 明确拒绝，不能被当成歌名
DENY_HINTS = (
    "别唱", "不要唱", "不用唱", "不唱", "停止", "闭嘴", "别念", "难听", "唱得难听",
)

#: 列表 / 咨询类意图（问「会唱什么」而不是点某一首）。
#: 2026-10-18 实测：「能唱什么歌」先被真唱路径接走 —— PLAY_HINTS 里的「唱」命中，
#: 剥掉量词「歌」后剩「能唱什么」，被当成歌名去撞曲库，最后回一句
#: 「曲库里没有《能唱什么》」。命中这里直接交回 SongService 出歌单。
LIST_HINTS = (
    "歌单", "曲库", "歌库",
    "能唱什么", "会唱什么", "会什么歌", "有哪些歌", "有什么歌",
    "能点哪些", "怎么点歌", "歌曲列表", "都会唱",
)

#: 需要从歌名里剥掉的语气词 / 量词（顺序有意义：长的在前）
FILLERS = (
    "一下", "一首", "一个", "一段", "两句", "整首", "完整版", "完整",
    "这首", "那个", "这个", "的", "歌", "曲子", "吧", "啊", "呀", "嘛",
    "哦", "噢", "呗", "呢", "来", "唱", "点", "给我", "帮我", "谢谢",
    "好听的", "好听点的", "please", "行不行", "可以吗", "好吗",
)

_MODE_SEGMENT = "segment"
_MODE_FULL = "full"


@dataclass
class CoverRequest:
    """一条点歌请求。``song`` 为 None 表示解析出了歌名但曲库里没有。"""

    query: str
    mode: str = _MODE_SEGMENT
    song: dict | None = None
    raw: str = ""

    @property
    def mode_label(self) -> str:
        return "一段" if self.mode == _MODE_SEGMENT else "整首"


@dataclass
class _Job:
    song: dict
    mode: str
    future: asyncio.Future = field(default_factory=asyncio.Future)


class CoverService:
    """SVC 翻唱的服务端封装。"""

    def __init__(
        self,
        enabled: bool = True,
        python_exe: str = r"D:\miniconda\envs\soulxsinger\python.exe",
        script: str = r"D:\BaiduNetdiskDownload\harness\鲸鱼娘SVC翻唱_20261017\cover.py",
        work_dir: str = r"D:\BaiduNetdiskDownload\harness\鲸鱼娘SVC翻唱_20261017",
        cache_dir: str = "./data/song_cache/cover",
        library_dirs: list[str] | None = None,
        default_mode: str = _MODE_SEGMENT,
        segment_seconds: float = 30.0,
        prompt: str = "",
        timeout: float = 900.0,
        max_queue: int = 8,
        voice_version: str = "zh11",
        # 2026-10-18：混音配比。原先是 人声 -19 / 伴奏 -14（伴奏盖过人声），
        # 现在默认 人声 -15 / 伴奏 -20。差值是听感平衡，绝对值影响总响度。
        mix_vocal_db: float = -15.0,
        mix_inst_db: float = -20.0,
    ) -> None:
        self.enabled = bool(enabled)
        self.python_exe = str(python_exe)
        self.script = Path(script)
        self.work_dir = Path(work_dir)
        self.cache_dir = Path(cache_dir)
        self.library_dirs = [Path(p) for p in (library_dirs or [])]
        self.segment_seconds = max(15.0, float(segment_seconds))
        self.default_mode = (
            _MODE_FULL if str(default_mode).lower() == "full" else _MODE_SEGMENT
        )
        self.prompt = str(prompt or "")
        self.timeout = max(120.0, float(timeout))
        self.max_queue = max(1, int(max_queue))
        self.voice_version = str(voice_version or "v1")
        self.mix_vocal_db = float(mix_vocal_db)
        self.mix_inst_db = float(mix_inst_db)

        self._songs: list[dict] = []
        self._sem = asyncio.Semaphore(1)
        self._waiting = 0
        self._running = ""
        self._last_job_seconds: dict[str, float] = {}
        #: 按长度模式分开记平均耗时：一段 ~50 秒、整首 ~190 秒，
        #: 混在一起算会让 ETA 忽快忽慢。
        self._avg_by_mode: dict[str, float] = {}
        self.stats = {
            "requests": 0,
            "cache_hits": 0,
            "rendered": 0,
            "failures": 0,
            "last_seconds": 0.0,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[点歌] 缓存目录创建失败: {exc}")
        self.reload_library()

    # ---------------------------------------------------------------- 可用性

    def available(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "cover.enabled=false"
        if not self.script.exists():
            return False, f"脚本不存在 {self.script}"
        if not Path(self.python_exe).exists():
            return False, f"python 不存在 {self.python_exe}"
        if not self.prompt or not Path(self.prompt).exists():
            return False, f"音色参考不存在 {self.prompt or '(未配置)'}"
        if not self._songs:
            return False, "曲库为空（检查 library_dirs）"
        return True, f"就绪，曲库 {len(self._songs)} 首"

    # ---------------------------------------------------------------- 曲库

    def reload_library(self) -> None:
        songs: list[dict] = []
        seen: set[str] = set()
        for base in self.library_dirs:
            if not base.exists():
                logger.warning(f"[点歌] 曲库目录不存在: {base}")
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in AUDIO_EXTS:
                    continue
                key = str(path.resolve()).casefold()
                if key in seen:
                    continue
                seen.add(key)
                songs.append(self._make_song(path))
        self._songs = songs
        self._load_aliases()
        logger.info(f"[点歌] 曲库载入 {len(songs)} 首（{len(self.library_dirs)} 个目录）")

    def _load_aliases(self) -> None:
        """读曲库目录下的 别名.json，给同一首歌挂多个叫法。

        梗歌经常有好几个名字（兰亭鸡序 / 兰亭坤序 / 坤亭序 都是一首），
        文件名只能用一个，所以留一个旁挂表：

            {"兰亭鸡序": ["兰亭坤序", "坤亭序", "兰亭序", "鸡你太美"]}
        """
        for base in self.library_dirs:
            f = base / ALIAS_FILE
            if not f.exists():
                continue
            try:
                mapping = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"[点歌] 别名文件解析失败 {f}: {exc}")
                continue
            if not isinstance(mapping, dict):
                continue
            added = 0
            for key, extras in mapping.items():
                if not isinstance(extras, list):
                    continue
                for song in self._songs:
                    if song["title"] == key or key in (song.get("title_full") or ""):
                        for alias in extras:
                            alias = str(alias).strip()
                            if alias and alias not in song["aliases"]:
                                song["aliases"].append(alias)
                                added += 1
                        break
            if added:
                logger.info(f"[点歌] 从 {f.name} 挂了 {added} 个别名")

    @staticmethod
    def _make_song(path: Path) -> dict:
        stem = path.stem
        # 「歌手 - 歌名」是 QQ 音乐 / 网易云的默认命名；「_」是多个歌手的连接符
        if " - " in stem:
            artist, title = stem.rsplit(" - ", 1)
        elif "-" in stem and stem.count("-") == 1:
            artist, title = stem.split("-", 1)
        else:
            artist, title = "", stem
        title = title.strip()
        artist = re.sub(r"\s*[_&]\s*", " / ", artist.strip())
        # 去掉括号注释做别名：「(Live)」「(独唱版)」「[Single Version]」
        clean = re.sub(r"\s*[\(\（\[【].*?[\)\）\]】]", "", title).strip()
        aliases = {title, clean, path.stem}
        if artist:
            aliases.add(f"{artist} - {title}")
            aliases.add(f"{artist}{title}")
        name = clean or title
        return {
            "id": "cv_" + hashlib.md5(str(path).encode("utf-8")).hexdigest()[:10],
            "title": name,
            "title_full": title,
            "artist": artist or "未知歌手",
            "path": str(path),
            "aliases": sorted(a for a in aliases if a),
            "is_local": True,
        }

    def list_songs(self, limit: int = 0) -> list[dict]:
        return self._songs[:limit] if limit else list(self._songs)

    def song_count(self) -> int:
        return len(self._songs)

    def titles(self, limit: int = 30) -> list[str]:
        return [s["title"] for s in self._songs[:limit]]

    def find(self, query: str) -> dict | None:
        q = re.sub(r"\s+", "", str(query or "")).casefold()
        q = re.sub(r"[《》<>\"'“”‘’]", "", q)
        if len(q) < 1:
            return None

        norm = [
            (re.sub(r"\s+", "", a).casefold().replace(" ", ""), s)
            for s in self._songs
            for a in (s["aliases"] or [s["title"]])
        ]
        norm = [(a, s) for a, s in norm if a]

        # 1) 完全相等
        for a, s in norm:
            if a == q:
                return s
        # 2) 包含关系（长别名优先，避免「晴天」被「雨天」类短名截胡）
        hits = [(a, s) for a, s in norm if q in a or a in q]
        if hits:
            hits.sort(key=lambda item: len(item[0]), reverse=True)
            best = hits[0]
            # 只有当命中名字足够长、或者双方长度接近时才认，防误判
            if len(best[0]) >= 2 and (
                best[0] in q or len(best[0]) - len(q) <= 4
            ):
                return best[1]
        # 3) 模糊匹配
        pool = {a: s for a, s in norm}
        close = difflib.get_close_matches(q, list(pool.keys()), n=1, cutoff=0.75)
        if close:
            return pool[close[0]]
        return None

    # ---------------------------------------------------------------- 意图

    @staticmethod
    def _strip_at(text: str) -> str:
        # 微信的 @ 分隔符是 U+2005，中文空格是 U+3000，一并吃掉
        return re.sub(r"@[^\s\u2005\u3000]+[\s\u2005\u3000]*", "", text).strip()

    @staticmethod
    def _clean_query(text: str) -> str:
        """从一句话里抠出歌名（只在整句撞不上歌名时才用）。

        只剥**多字**语气词；单字（的/来/唱/点/歌）只在首尾剥。
        2026-10-18 踩坑：早先把单字也全局替换，结果「后来」被剥成「后」、
        带「的」「来」的歌名全废。
        """
        out = re.sub(r"[《》<>\"'“”‘’]", " ", text)
        words = [w for w in (list(PLAY_HINTS) + list(FILLERS)) if len(w) >= 2]
        for word in sorted(words, key=len, reverse=True):
            out = out.replace(word, " ")
        out = re.sub(r"[\s\u2005\u3000,，.。!！?？~～:：、]+", " ", out).strip()
        single = "的来了唱点歌吧啊呀嘛哦呗呢个给帮我你他她它这那"
        changed = True
        while changed and out:
            changed = False
            for ch in single:
                if out.startswith(ch):
                    out = out[1:].strip()
                    changed = True
                if out.endswith(ch):
                    out = out[:-1].strip()
                    changed = True
        return out.strip()

    def _exact_match(self, text: str, min_len: int = 3) -> dict | None:
        """整句 == 某个歌名/别名（去空白、忽略大小写），且名字够长。

        只给「群里直接甩歌名」这种情况用，所以刻意不做包含/模糊匹配。
        """
        q = re.sub(r"[\s\u2005\u3000]+", "", str(text or "")).casefold()
        if len(q) < min_len:
            return None
        for song in self._songs:
            for alias in song["aliases"]:
                a = re.sub(r"[\s\u2005\u3000]+", "", str(alias)).casefold()
                if len(a) >= min_len and a == q:
                    return song
        return None

    def resolve(self, content: str) -> CoverRequest | None:
        """解析一条消息。返回 None 表示「这条不该由真唱接管」。"""
        if not self.enabled or not self._songs:
            return None
        raw = str(content or "").strip()
        if not raw:
            return None
        text = self._strip_at(raw)
        if not text:
            return None

        lowered = text.casefold()
        # 「别唱了」「不要唱」这类明确拒绝，不能被当成歌名（实测「别唱了」→ 歌名「别」）
        if any(h in text for h in DENY_HINTS):
            return None
        # 问「会唱什么/歌单/曲库」这类是咨询，不是点某一首，交回 SongService
        if any(h in text for h in LIST_HINTS):
            return None

        mode = self.default_mode
        if any(h in text for h in FULL_HINTS):
            mode = _MODE_FULL
        elif any(h in text for h in SEGMENT_HINTS):
            mode = _MODE_SEGMENT

        # 2026-10-18：群里最常见的其实是「只打歌名」——实测群友直接发「肺痒痒」，
        # 没有触发词，于是掉进 AI 闲聊、根本没唱。这里补一条只认**完全相等**的窄口子：
        # 整句去空白后必须和某个歌名/别名一模一样，且名字至少 3 个字。
        # 卡 3 个字是为了放过「后来」「晴天」「朋友」这类日常词，不然聊天会被打断。
        if not any(h in lowered for h in PLAY_HINTS) and "《" not in text:
            song = self._exact_match(text, min_len=3)
            if song is None:
                return None
            return CoverRequest(query=song["title"], mode=mode, song=song, raw=raw)

        # 先拿整句原文去撞歌名：句子里只要含有完整歌名就能直接命中，
        # 不依赖脆弱的分词清理。命中不了再用清理后的残句重试一次。
        song = self.find(text) or self.find(self._clean_query(text))
        query = str((song or {}).get("title") or self._clean_query(text))
        # 「随便唱一首」这类没有具体歌名，交回原来的随机逻辑
        if not song and (
            not query or any(k in query for k in ("随便", "随机", "任意", "都行"))
        ):
            return None
        # 抠出来的东西短到只剩一个字，基本是误判，别接管
        if not query or (song is None and len(query) < 2):
            return None
        return CoverRequest(query=query, mode=mode, song=song, raw=raw)

    # ---------------------------------------------------------------- 缓存

    def _slug(self, song: dict) -> str:
        base = f"{song.get('artist', '')}_{song.get('title', '')}"
        base = re.sub(r"[^\w\u4e00-\u9fff]+", "_", base).strip("_")
        return base[:60] or "song"

    def cache_path(self, song: dict, mode: str) -> Path:
        # 文件名直接就是群里看到的名字：整首走文件发送时，用户看到的是
        # 「鲸鱼娘-后来-整首.mp3」而不是「刘若英_后来__full__zh11.mp3」。
        # 一段的长度必须写进文件名：改了 segment_seconds 之后旧缓存要自动失效，
        # 否则群友点歌还在听老时长的成品（2026-10-18 从 45 秒改 30 秒时踩过）。
        if mode == _MODE_SEGMENT:
            label = f"一段{int(round(self.segment_seconds))}s"
        else:
            label = "整首"
        title = re.sub(r'[\\/:*?"<>|]', "_", str(song.get("title") or "song"))[:40]
        artist = re.sub(r'[\\/:*?"<>|]', "_", str(song.get("artist") or ""))[:20]
        tag = f"({artist})" if artist and artist not in ("未知歌手", "") else ""
        stem = f"鲸鱼娘-{title}{tag}-{label}"
        # 必须是绝对路径：子进程的 cwd 是 SVC 工作目录，相对路径会落到别处
        # （2026-10-18 踩坑：产物被写到 SVC 目录下的 data/... 里，父进程找不到）
        out = (self.cache_dir / self.voice_version / f"{stem}.mp3").resolve()
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return out

    def is_cached(self, song: dict, mode: str) -> bool:
        try:
            p = self.cache_path(song, mode)
            return p.exists() and p.stat().st_size > 4096
        except Exception:
            return False

    # ---------------------------------------------------------------- 队列

    def queue_status(self) -> dict:
        avg = self._avg_by_mode.get(
            self.default_mode, 75.0 if self.default_mode == _MODE_SEGMENT else 190.0
        )
        return {
            "busy": self._running != "",
            "running": self._running,
            "waiting": max(0, self._waiting),
            "eta_seconds": round(avg * (self._waiting + (1 if self._running else 0))),
        }

    def eta_text(self, mode: str | None = None) -> str:
        """给群友看的预计等待。

        注意要把「当前这一单」算进去：原来是按队列里已有的单数算，
        第一单永远是 0 秒，于是回「马上就来」，实际等了 3 分钟。
        """
        mode = mode or self.default_mode
        default = 75.0 if mode == _MODE_SEGMENT else 190.0
        avg = self._avg_by_mode.get(mode, default)
        total = avg * (max(0, self._waiting) + 1)
        if total <= 60:
            return "马上就来"
        return f"约 {max(1, round(total / 60))} 分钟"

    # ---------------------------------------------------------------- 渲染

    async def render(self, song: dict, mode: str | None = None) -> Path | None:
        """跑一首，返回 mp3 路径。命中缓存秒回。"""
        if not song:
            return None
        mode = mode or self.default_mode
        self.stats["requests"] += 1

        if self.is_cached(song, mode):
            self.stats["cache_hits"] += 1
            return self.cache_path(song, mode)

        if self._waiting >= self.max_queue:
            logger.warning(f"[点歌] 队列已满（{self._waiting}），拒绝 {song['title']}")
            return None

        label = f"{song['title']}（{ '一段' if mode == _MODE_SEGMENT else '整首' }）"
        self._waiting += 1
        try:
            async with self._sem:
                if self.is_cached(song, mode):
                    self.stats["cache_hits"] += 1
                    return self.cache_path(song, mode)
                self._running = label
                started = time.perf_counter()
                try:
                    path = await self._run_cover(song, mode)
                finally:
                    self._running = ""
                elapsed = time.perf_counter() - started
                if path is None:
                    self.stats["failures"] += 1
                    return None
                self.stats["rendered"] += 1
                self.stats["last_seconds"] = round(elapsed, 2)
                prev = self._avg_by_mode.get(mode)
                self._avg_by_mode[mode] = (
                    round(elapsed, 2) if prev is None else round(prev * 0.7 + elapsed * 0.3, 2)
                )
                logger.info(f"[点歌] 《{label}》唱完，耗时 {elapsed:.1f} 秒")
                return path
        finally:
            self._waiting -= 1

    def _work_dir_for(self, song: dict) -> Path:
        """复用已有的 song_{歌名} 工作目录，省掉重复的 demucs 分轨。

        2026-10-18：昨天手工跑过的 8 首歌已经在 song_晴 天 之类的目录里存了
        分轨结果，如果这里换一套命名，第一首点歌会白等 40 秒重新分轨。
        同名的不同版本（例如两个歌手都唱《晴天》）用哈希目录隔开，
        靠 title.txt 里的原文判断是不是同一首。
        """
        title = str(song.get("title") or "song")
        safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:40]
        cand = self.work_dir / f"song_{safe}"
        if cand.exists():
            note = cand / "title.txt"
            try:
                content = note.read_text(encoding="utf-8") if note.exists() else ""
            except Exception:
                content = ""
            full = str(song.get("title_full") or "")
            if not content or title in content or (full and full in content):
                return cand
        hashed = hashlib.md5(str(song.get("path", "")).encode("utf-8")).hexdigest()[:6]
        return self.work_dir / f"song_{safe}_{hashed}"

    async def _run_cover(self, song: dict, mode: str) -> Path | None:
        out = self.cache_path(song, mode)
        tmp = out.with_name(out.stem + ".part.mp3")
        work = self._work_dir_for(song)

        cmd = [
            self.python_exe,
            str(self.script),
            str(song["path"]),
            "--tag", str(song["title"]),
            "--out", str(tmp),
            "--workdir", str(work),
            "--no-stats",
            "--vocal-db", str(self.mix_vocal_db),
            "--inst-db", str(self.mix_inst_db),
        ]
        if self.prompt:
            cmd += ["--prompt", self.prompt]
        if mode == _MODE_SEGMENT:
            cmd += ["--segment", str(self.segment_seconds)]

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["HF_ENDPOINT"] = "https://hf-mirror.com"

        logger.info(f"[点歌] 开始翻唱 {song['title']}（{mode}）")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.work_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                raw, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                logger.error(f"[点歌] 《{song['title']}》超时（{self.timeout:.0f}s）")
                return None
        except Exception as exc:  # pragma: no cover
            logger.error(f"[点歌] 启动翻唱进程失败: {exc}")
            return None

        text = (raw or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            tail = "\n".join(text.strip().splitlines()[-12:])
            logger.error(
                f"[点歌] 《{song['title']}》翻唱失败（exit={proc.returncode}）\n{tail}"
            )
            return None
        if not tmp.exists() or tmp.stat().st_size < 4096:
            logger.error(f"[点歌] 《{song['title']}》没有产出音频")
            return None

        try:
            tmp.replace(out)
        except Exception as exc:  # pragma: no cover
            logger.error(f"[点歌] 归档失败: {exc}")
            return None

        for line in reversed(text.splitlines()):
            if line.startswith("RESULT_JSON "):
                try:
                    info = json.loads(line[len("RESULT_JSON "):])
                    logger.info(
                        f"[点歌] 结果 {info.get('title')} "
                        f"{info.get('seconds')}s shift={info.get('shift')}"
                    )
                except Exception:
                    pass
                break
        return out

    # ---------------------------------------------------------------- 状态

    def status(self) -> dict:
        ok, why = self.available()
        q = self.queue_status()
        return {
            "enabled": self.enabled,
            "available": ok,
            "reason": why,
            "songs": len(self._songs),
            "default_mode": self.default_mode,
            "segment_seconds": self.segment_seconds,
            "cache_dir": str(self.cache_dir),
            "busy": q["busy"],
            "waiting": q["waiting"],
            "stats": dict(self.stats),
        }
