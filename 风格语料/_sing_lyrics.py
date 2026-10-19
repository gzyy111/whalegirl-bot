# -*- coding: utf-8 -*-
"""用鲸鱼娘自己的唱腔参数把填词「唱」出来。

调用的是项目自身的 TTSService，参数与 SongService.render_lyrics() 完全一致
（sing_rate / sing_pitch 取自 config.yaml 的 voice.songs），
所以产物就是机器人点歌时会发出的那种音频。
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config_manager import ConfigManager  # noqa: E402
from src.tts_service import TTSService  # noqa: E402


async def main():
    cfg = ConfigManager("config.yaml")
    cfg.load("default")
    v = cfg.get("voice", {}) or {}
    songs = v.get("songs", {}) or {}

    tts = TTSService(
        cache_dir=v.get("cache_dir", "./data/voice_cache"),
        enabled=v.get("enabled", True),
        engine=v.get("engine", "auto"),
        default_voice=v.get("default_voice"),
        rate=v.get("rate", "+0%"),
        pitch=v.get("pitch", "+0Hz"),
        timeout=v.get("timeout", 25.0),
        max_chars=v.get("max_chars", 400),
        retries=v.get("retries", 2),
    )
    engine = tts.resolve_engine()
    print("语音引擎 : %s" % engine)
    if engine == "off":
        print("[失败] 引擎不可用，无法演唱")
        return 1

    sing_rate = songs.get("sing_rate", "+8%")
    sing_pitch = songs.get("sing_pitch", "+35Hz")
    print("唱腔参数 : rate=%s pitch=%s voice=%s" % (sing_rate, sing_pitch, tts.default_voice))

    ly = ROOT / "风格语料" / "vctcn_突然的陀螺_唱词.txt"
    lyrics = ly.read_text(encoding="utf-8").strip()
    print("唱词字数 : %d（上限 %d）" % (len(lyrics), songs.get("max_lyrics_chars", 300)))

    out = ROOT / "风格语料" / "鲸鱼娘唱_突然的陀螺_VCTCN.mp3"
    audio = await tts.synthesize(
        lyrics,
        out_path=out,
        rate=sing_rate,
        pitch=sing_pitch,
        salt="song:custom",
    )
    if audio is None or not Path(audio).is_file():
        print("[失败] 合成失败")
        return 1

    path = Path(audio)
    seconds = tts.estimate_duration(path)
    print()
    print("产物     : %s" % path)
    print("大小     : %d 字节" % path.stat().st_size)
    print("时长     : 约 %.1f 秒" % seconds)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
