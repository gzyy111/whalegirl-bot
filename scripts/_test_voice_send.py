# -*- coding: utf-8 -*-
"""实测：修好后的 VoiceSender 发语音条到指定会话"""
import sys, time
sys.path.insert(0, r"D:\鲸鱼娘")

from src.voice_sender import VoiceSender

target = sys.argv[1] if len(sys.argv) > 1 else "文件传输助手"
words = sys.argv[2] if len(sys.argv) > 2 else "测试一下语音条，能听到就说明修好了。"

vs = VoiceSender()
print("环境:", vs.available())
t0 = time.time()
ok = vs.send_text(target, words)
print(f"结果: {ok}  用时 {time.time()-t0:.1f}s")
