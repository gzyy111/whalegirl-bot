# -*- coding: utf-8 -*-
"""验证：重复点同一首歌，产出的音频是否完全一致（只查缓存，不重新渲染、不发声）。"""
import asyncio
import hashlib
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.cover_service import CoverService  # noqa: E402


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


async def main() -> int:
    cfg = yaml.safe_load(open(REPO / "config.yaml", encoding="utf-8"))
    cc = cfg["voice"]["songs"]["cover"]
    svc = CoverService(
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
    print("可用:", svc.available())
    print("缓存路径模板:", svc.cache_path({"title": "X", "artist": "Y"}, "segment").name)

    song = svc.find("晴天")
    print("\n命中曲目:", song and song.get("title"), "|", song and song.get("path"))
    if not song:
        return 1

    for mode in ("segment", "full"):
        path = svc.cache_path(song, mode)
        print(f"\n[{mode}] 缓存存在={path.exists()} 文件={path.name}")
        if path.exists():
            print(f"       大小={path.stat().st_size/1024:.0f}KB md5={md5(path)}")
            print(f"       mtime={time.strftime('%H:%M:%S', time.localtime(path.stat().st_mtime))}")

    print("\n连续两次 render('segment')：")
    t = time.perf_counter()
    p1 = await svc.render(song, "segment")
    t1 = time.perf_counter() - t
    t = time.perf_counter()
    p2 = await svc.render(song, "segment")
    t2 = time.perf_counter() - t
    print(f"  第1次: {p1.name if p1 else None}  用时 {t1:.3f}s")
    print(f"  第2次: {p2.name if p2 else None}  用时 {t2:.3f}s")
    if p1 and p2:
        print(f"  同一路径: {p1 == p2}")
        print(f"  同一内容: {md5(p1) == md5(p2)}  md5={md5(p1)}")
    print(f"\n统计: {svc.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
