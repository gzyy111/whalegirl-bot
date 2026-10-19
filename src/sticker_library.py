# -*- coding: utf-8 -*-
"""本地表情包库：挑图 + 转成微信稳当的格式（2026-10-16 新增）。

背景
----
项目自带的「收藏表情」走 UIA 打开微信表情面板，本机实测每次都失败
（日志「表情面板未打开或未被 UIA 暴露」），已在 config.yaml 里关闭。
群友 10-16 提议「给大肥鱼下载一个表情包库」，于是改走**本地图片文件**这条路：
从 `表情包/` 目录挑一张 → 转成微信认得的 PNG/JPEG → 复用既有的
`WeChatBridge.send_image()` 发出。

为什么必须转换
--------------
下载来的原图有两个毛病：一是格式五花八门（PNG/JPG/WebP/GIF），
WebP 在微信桌面端通过自动化粘贴时可能被当成「文件」而不是「图片」；
二是高清原图平均 1.7MB，直接发既慢又容易触发发送超时。
所以统一缩到长边 `max_side`（默认 800）并转成 PNG（有透明）或 JPEG，
结果缓存到 `data/sticker_cache/`，同一张只转一次。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

from loguru import logger

try:
    from PIL import Image

    _PIL_OK = True
except Exception:  # noqa: BLE001
    _PIL_OK = False

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class StickerLibrary:
    """从本地目录里挑表情包，并把它准备成可直接发送的图片文件。"""

    def __init__(
        self,
        dirs: list[str] | None = None,
        cache_dir: str = "./data/sticker_cache",
        max_side: int = 800,
        recent_avoid: int = 30,
        enabled: bool = True,
        v2_enabled: bool = True,
        v2_tags_file: str = "./表情包/tags_v2.json",
        v2_mode: str = "safe",
        # 2026-10-19 新增：点名专用图（编号列表，如 ["006"]）。
        # 这些图不进随机池、也不参与笑话配图的语义召回，只能被「发006」这类
        # 显式点名叫出来——避免擦边图被随机甩到群里。
        named_only: list[str] | None = None,
        # 编号 -> 点名触发词 的映射文件（见 表情包/aigengtu/点名表.json）
        aliases_file: str = "",
        # 2026-10-19 新增：形态黑名单（长条截图 / 超大图），只影响随机池与召回，
        # 点名发图不受影响。见 _build_rejects.py 与 表情包/aigengtu/rejects.json。
        rejects_file: str = "",
    ):
        self.enabled = bool(enabled) and _PIL_OK
        self.max_side = max(160, int(max_side or 800))
        self.recent_avoid = max(0, int(recent_avoid or 0))
        self._cache_dir = Path(cache_dir)
        self._dirs = [Path(d) for d in (dirs or [])]
        # 点名发图：编号索引 / 别名索引 / 点名专用集合
        self._named_only_raw = [str(x) for x in (named_only or [])]
        self._by_number: dict[str, str] = {}
        self._by_alias: dict[str, str] = {}
        self._named_only: set[str] = set()
        self._aliases_file = Path(aliases_file) if aliases_file else None
        # 形态黑名单：长条截图 / 超大图，文件名集合
        self._rejects_file = Path(rejects_file) if rejects_file else None
        self._rejects: set[str] = set()
        # 2026-10-16 v2：多维标签（诊断脚本/表情包多维打标/）。
        # v1 只有 8 类情绪，挑图等于随机；v2 有版式/有无文字/载体/构图/场景/服装/主体/腿部，
        # 挑图分三层：A/B 级硬过滤 → C 级软排序 → 逐级放宽兜底。
        self._v2_enabled = bool(v2_enabled)
        self._v2_tags_file = Path(v2_tags_file) if v2_tags_file else None
        self._v2_mode = str(v2_mode or "safe")
        self._v2: dict[str, dict] = {}       # "目录/文件名" -> v2 记录
        self._v2_of: dict[str, dict] = {}    # 绝对路径 -> v2 记录
        self._v2_dims: dict = {}
        self._recent: list[str] = []
        self._sent = 0
        self._converted = 0
        self._sources: list[str] = []
        self._load_tags_v2()
        self._refresh()

    # ------------------------------------------------------------------ 标签
    def _load_tags_v2(self) -> None:
        """读多维标签 v2（由 诊断脚本/表情包多维打标/ 生成）。读不到就退回 v1 行为。"""
        if not self._v2_enabled or not self._v2_tags_file or not self._v2_tags_file.is_file():
            return
        try:
            data = json.loads(self._v2_tags_file.read_text(encoding="utf-8"))
            self._v2 = data.get("files") or {}
            self._v2_dims = data.get("dimensions") or {}
            if self._v2:
                flagged = sum(
                    1 for r in self._v2.values()
                    if r.get("kind") in self.V2_BAD_KIND
                    or (r.get("text_chars") or 0) > self.V2_MAX_TEXT
                )
                logger.info(
                    "多维标签 v2 已加载: %d 张，标记为「不适合当表情」的 %d 张"
                    % (len(self._v2), flagged)
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取多维标签 v2 失败（退回按情绪挑图）: {e}")
            self._v2 = {}

    # ------------------------------------------------------------------ 索引
    def _refresh(self) -> None:
        files: list[str] = []
        for d in self._dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                    files.append(str(p))
        self._collect = files
        self._sources = [str(d) for d in self._dirs if d.is_dir()]
        # 建立 绝对路径 -> v2 内容标签（标签文件里的 key 是「目录名/文件名」）
        self._v2_of = {}
        if self._v2:
            for p in files:
                path = Path(p)
                rec = self._v2.get(f"{path.parent.name}/{path.name}")
                if rec:
                    self._v2_of[p] = rec
        if self.enabled and not files:
            logger.warning(
                "表情包库没有可用图片，检查 wechat.sticker_library.dirs 配置"
            )
        self._build_named_index()

    # ------------------------------------------------------ 编号 / 点名（2026-10-19）
    @staticmethod
    def _norm_number(name: str | Path) -> str:
        """把 006.webp / 6.webp / 0006 统一成 '6'，用于编号比对。"""
        digits = re.sub(r"\D", "", Path(str(name)).stem)
        if not digits:
            return ""
        try:
            return str(int(digits))
        except ValueError:
            return ""

    def _build_named_index(self) -> None:
        """建「编号 -> 图」和「点名触发词 -> 图」两张表。

        - 编号索引覆盖库里所有 aigengtu 图，所以「发612」能点到任意一张；
        - 触发词来自 aliases_file（点名表.json），是给常用图配的短名；
        - named_only 是**可选**的保护名单，默认空 = 所有图都能随机发。
        """
        self._by_number = {}
        for p in self._collect:
            num = self._norm_number(p)
            if num:
                self._by_number.setdefault(num, p)

        self._named_only = {
            num
            for num in (self._norm_number(x) for x in self._named_only_raw)
            if num
        }

        self._by_alias = {}
        if self._aliases_file and self._aliases_file.is_file():
            try:
                data = json.loads(self._aliases_file.read_text(encoding="utf-8"))
                for num, names in (data or {}).items():
                    key = self._norm_number(str(num))
                    if not key or key not in self._by_number:
                        continue
                    if isinstance(names, str):
                        names = [names]
                    for alias in names:
                        alias = str(alias).strip()
                        if len(alias) >= 2:
                            self._by_alias[alias] = self._by_number[key]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"点名表读取失败（不影响按编号发图）: {e}")

        self._load_rejects()

        logger.info(
            "点名发图已就绪: 可点编号 %d 个，触发词 %d 个%s%s"
            % (
                len(self._by_number),
                len(self._by_alias),
                f"，点名专用 {len(self._named_only)} 张" if self._named_only else "",
                f"，形态剔除 {len(self._rejects)} 张（点名仍可发）" if self._rejects else "",
            )
        )

    def _load_rejects(self) -> None:
        """读形态黑名单（长条截图 / 超大图）。读不到就不做任何剔除。"""
        self._rejects = set()
        if not self._rejects_file or not self._rejects_file.is_file():
            return
        try:
            data = json.loads(self._rejects_file.read_text(encoding="utf-8"))
            items = data.get("files") if isinstance(data, dict) else data
            for x in items or []:
                name = x.get("file") if isinstance(x, dict) else x
                if name:
                    self._rejects.add(Path(str(name)).name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"形态黑名单读取失败（不做剔除）: {e}")

    def is_rejected(self, path: str | Path) -> bool:
        """这张图是否被形态规则剔除（只影响随机/召回，不影响点名）。"""
        return bool(self._rejects) and Path(str(path)).name in self._rejects

    def by_number(self, num: str) -> str | None:
        """按编号取图路径（6 / 006 / 0006 都认）。"""
        return self._by_number.get(self._norm_number(str(num)))

    def by_alias(self, text: str) -> tuple[str, str] | None:
        """在文本里找点名触发词，返回 (触发词, 路径)。长触发词优先。"""
        content = str(text or "")
        for alias in sorted(self._by_alias, key=len, reverse=True):
            if alias in content:
                return alias, self._by_alias[alias]
        return None

    def is_named_only(self, path: str | Path) -> bool:
        """这张图是否被配置成「只能点名发」（默认名单为空，即没有图受保护）。"""
        return bool(self._named_only) and self._norm_number(str(path)) in self._named_only

    def _random_pool(self) -> list[str]:
        """随机挑图的池子。

        - named_only 非空时排除保护名单（默认空，不排除）
        - rejects 非空时排除形态黑名单（长条截图 / 超大图）
        """
        pool = [
            p for p in self._collect if not self.is_named_only(p)
        ]
        if self._rejects:
            pool = [p for p in pool if not self.is_rejected(p)]
        return pool

    @property
    def available(self) -> bool:
        return bool(self.enabled and self._collect)

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "sources": self._sources,
            "total": len(self._collect),
            "tagged": len(self._v2_of),
            "v2_enabled": bool(self._v2_enabled),
            "v2_tagged": len(self._v2_of),
            "v2_mode": self._v2_mode,
            "v2_sendable": len(self._v2_candidates(self._v2_mode)) if self._v2_of else 0,
            "sent": self._sent,
            "converted": self._converted,
            "max_side": self.max_side,
            # 2026-10-19：点名发图
            "indexed_numbers": len(self._by_number),
            "named_aliases": len(self._by_alias),
            "named_only": sorted(self._named_only),
            # 2026-10-19：形态黑名单（长条截图 / 超大图）
            "rejected": len(self._rejects),
            "sendable": len(self._random_pool()),
        }

    # ------------------------------------------------------------------ 挑图
    def pick(self, strict_soft: bool = False, **soft) -> str | None:
        """按内容标签挑一张并准备好（2026-10-19 去掉情绪体系）。

        soft 的键：scene / outfit / shot / subject / kind / legwear / action / props。
        点名（strict_soft=True）时先只从满足全部条件的池里挑，空了再逐级放宽；
        情绪不再参与任何环节，挑图只看画面内容。
        """
        if not self.available:
            return None
        if self._v2_of:
            ready = self.pick_v2(strict_soft=strict_soft, **soft)
            if ready:
                return ready
        base = self._random_pool()
        pool = [p for p in base if p not in self._recent] or base
        random.shuffle(pool)
        for src in pool:
            ready = self.prepare(src)
            if ready:
                self._recent.append(src)
                if len(self._recent) > self.recent_avoid:
                    self._recent = self._recent[-self.recent_avoid:]
                return ready
        return None

    # ------------------------------------------------------------------ v2 多维挑图
    # 硬过滤用的三类：截图/漫画（发出去不像表情包）、带字过多（像刷屏）、超长条图。
    # 依据是 2026-10-16 的人眼抽检：A/B 级标签准确率 95~100%，可以当硬条件；
    # C 级（场景/服装/构图/载体）只有 50~75%，只能当软偏好，当硬条件会把候选砍空或挑错。
    V2_BAD_KIND = {"screenshot", "comic"}
    V2_MAX_TEXT = 60
    V2_BAD_LAYOUT = {"wide_long", "tall_long"}
    # 软条件候选少于这个数就只当排序偏好，不当门槛（避免砍空）
    V2_TIGHT_MIN = 8
    # 软条件逐级放宽的顺序：先丢最不确定的
    V2_RELAX_ORDER = ("subject", "outfit", "scene", "shot", "kind")

    def _v2_candidates(self, mode: str = "safe", require: dict | None = None,
                       **soft) -> list[str]:
        require = require or {}
        out = []
        for p in self._random_pool():
            rec = self._v2_of.get(p)
            if not rec:
                continue
            if mode == "safe":
                if rec.get("kind") in self.V2_BAD_KIND:
                    continue
                if (rec.get("text_chars") or 0) > self.V2_MAX_TEXT:
                    continue
                if rec.get("layout") in self.V2_BAD_LAYOUT:
                    continue
            ok = True
            for dim, want in require.items():
                if want is None:
                    continue
                wants = want if isinstance(want, (list, tuple, set)) else [want]
                if dim == "props":
                    if not (set(wants) & set(rec.get("props") or [])):
                        ok = False
                        break
                    continue
                if rec.get(dim) not in wants:
                    ok = False
                    break
            for dim, want in soft.items():
                if not ok or want is None:
                    continue
                wants = want if isinstance(want, (list, tuple, set)) else [want]
                if dim == "props":
                    if not (set(wants) & set(rec.get("props") or [])):
                        ok = False
                    continue
                if rec.get(dim) not in wants:
                    ok = False
            if ok:
                out.append(p)
        return out

    def _v2_rank(self, paths: list[str], soft: dict | None = None) -> list[str]:
        """内容匹配越多分越高；同分看尺寸/版式。只做排序，不做过滤。"""
        soft = soft or {}

        def score(p: str) -> float:
            rec = self._v2_of.get(p) or {}
            s = 0.0
            for dim, want in soft.items():
                if want is None:
                    continue
                wants = want if isinstance(want, (list, tuple, set)) else [want]
                if dim == "props":
                    hit = bool(set(wants) & set(rec.get("props") or []))
                else:
                    hit = rec.get(dim) in wants
                if hit:
                    s += 2.0 + max(0.0, float(rec.get(dim + "_margin") or 0.0))
            if rec.get("size_tier") == "large":
                s += 0.3
            if rec.get("layout") in ("square", "tall"):
                s += 0.2
            return s

        return sorted(paths, key=score, reverse=True)

    def pick_v2(self, mode: str | None = None, strict_soft: bool = False,
                **soft) -> str | None:
        """内容维度挑图：点名硬条件 → 软条件并集 → 排序 → 全库兜底。"""
        if not self._v2_of:
            return None
        mode = mode or self._v2_mode
        soft = {k: v for k, v in soft.items() if v}

        if strict_soft and soft:
            strict_pool = self._v2_candidates(mode=mode, **soft)
            if strict_pool:
                picked = self._take_from(strict_pool, soft)
                if picked:
                    return picked

        tight = self._v2_candidates(mode=mode, **soft) if soft else []
        if tight and len(tight) >= self.V2_TIGHT_MIN:
            picked = self._take_from(tight, soft)
            if picked:
                return picked

        pool = tight or self._v2_candidates(mode=mode)
        return self._take_from(pool, soft) if pool else None

    def _take_from(self, pool: list[str], soft: dict) -> str | None:
        """从候选池里排序、随机取头部、挑出第一张能转换成功的图。"""
        fresh = [p for p in pool if p not in self._recent]
        if fresh:
            pool = fresh
        ranked = self._v2_rank(pool, soft=soft)
        head = ranked[: max(6, len(ranked) // 3)]      # 在靠前的候选里随机，避免每次同一张
        random.shuffle(head)
        for src in head:
            ready = self.prepare(src)
            if ready:
                self._recent.append(src)
                if len(self._recent) > self.recent_avoid:
                    self._recent = self._recent[-self.recent_avoid:]
                logger.debug("v2 挑图命中: %s" % Path(src).name)
                return ready
        return None

    # ------------------------------------------------------------------ 预处理
    def prepare(self, path: str) -> str | None:
        """把一张图转成微信稳当的格式并缓存；返回可直接发送的路径。"""
        if not self.enabled or not _PIL_OK:
            return None
        src = Path(path)
        if not src.is_file():
            return None

        # GIF 保留原样：转成静态图会把动图毁了；体积过大的跳过
        if src.suffix.lower() == ".gif":
            try:
                if src.stat().st_size <= 5 * 1024 * 1024:
                    return str(src)
            except OSError:
                return None
            return None

        key = hashlib.md5(
            ("%s|%d" % (str(src.resolve()), self.max_side)).encode("utf-8")
        ).hexdigest()[:16]

        cached_png = self._cache_dir / (key + ".png")
        cached_jpg = self._cache_dir / (key + ".jpg")
        for c in (cached_png, cached_jpg):
            if c.is_file() and c.stat().st_size > 0:
                return str(c)

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                use_png = im.mode in ("RGBA", "LA") or (
                    im.mode == "P" and "transparency" in im.info
                )
                im = im.convert("RGBA" if use_png else "RGB")
                if max(im.size) > self.max_side:
                    im.thumbnail((self.max_side, self.max_side), Image.LANCZOS)
                if use_png:
                    im.save(cached_png, "PNG", optimize=True)
                    out = cached_png
                else:
                    im.save(cached_jpg, "JPEG", quality=88, optimize=True)
                    out = cached_jpg
            self._converted += 1
            return str(out)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"表情包转换失败，跳过 {src.name}: {e}")
            return None

    def mark_sent(self) -> None:
        self._sent += 1
