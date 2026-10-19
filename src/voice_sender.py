# -*- coding: utf-8 -*-
"""语音条发送器：把文本/音频以「微信原生语音消息」发出去。

原理（2026-10-16 本机实测确认）
--------------------------------
1. 微信 4.1.9+ 在输入框右下角提供语音按钮：**点一下开始录音**，
   界面变成「× 取消 / 波形 / 绿色 ↑ 发送」，点绿色按钮即发出语音条。
2. 微信客户区画在 ``MMUIRenderSubWindowHW`` 上，该窗口带
   ``WS_EX_LAYERED | WS_EX_TRANSPARENT``：``mouse_event`` 的命中测试会被
   丢弃（点了没反应），只有 ``SendInput`` + 绝对坐标能穿透。
   wechatauto 的 ``WinInput.send_input_click`` 正是这个实现。
3. 录音取的是**系统默认录音设备**。本机已把默认录音设备设为
   VB-CABLE 的 ``CABLE Output``，因此只要把音频播进 ``CABLE Input``，
   微信就会把这段音频录成语音条。

依赖：wechatauto、sounddevice、imageio-ffmpeg（均已在 .venv 中）
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Optional, Tuple

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("voice_sender")


class VoiceSenderError(Exception):
    pass


class VoiceSender:
    """把 TTS 音频以微信语音条发出的发送器（线程内串行使用）"""

    #: 标定用的基准窗口尺寸（本机微信窗口 1200x1540 物理像素时测得）
    BASE_W, BASE_H = 1200, 1540
    #: 内容区（wechatauto 的 render 窗口）在 1200x1540 主窗口内是 (11,0,1189,1529)，
    #: 也就是**左/右/下各内缩 11px**。按主窗口矩形做比例换算会整体偏 11px，
    #: 这就是「坐标看着对、点了没反应」的来源。改为按内容区边缘锚定的固定像素
    #: 偏移：既绕开边框，也不会因为窗口移动/改尺寸而漂移。
    VOICE_EDGE = (145, 52)     # 正常态语音按钮：距内容区右边 145、下边 52
    SEND_EDGE = (64, 58)       # 录音态绿色 ↑：距右边 64、下边 58
    CANCEL_EDGE = (367, 58)    # 录音态 × 取消：距右边 367、下边 58
    EMOJI_LEFT = 434           # 表情按钮：距内容区左边 434
    #: 录音态判定阈值（整图像素平均差）。
    #: 2026-10-18 实测：微信 4.x 下真正进入录音态时，全窗灰度平均差只有
    #: 0.23—0.56（旧值 1.5 是按早期版本标的，那会儿能到 9），于是每次都
    #: 被判成「没进入录音态」而中止——群里的语音条全军覆没。
    #: 降到 0.15：静止画面通常 <0.05，仍能挡住误点；真进去了就不再误杀。
    REC_STATE_DIFF = 0.15
    TARGET_SR = 48000

    def __init__(self, tts_voice: str = "zh-CN-XiaoxiaoNeural",
                 cache_dir: Optional[str] = None,
                 max_seconds: float = 55.0,
                 warmup: bool = False,
                 verify: bool = True):
        self.tts_voice = tts_voice
        self.max_seconds = max_seconds
        #: 是否先点一下工具栏（历史遗留的「热身」）。实测不再需要，且旧坐标会
        #: 误点收藏按钮弹出模态框，默认关闭。
        self.warmup = warmup
        #: 发送后是否回读微信数据库确认语音条真的落地
        self.verify = verify
        self._gui = None
        self._sd = None
        self._np = None
        self._ffmpeg = None
        self._play_dev = None
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "data", "voice_out")
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ 环境
    def _lazy(self) -> None:
        if self._sd is None:
            import numpy as np
            import sounddevice as sd
            self._np, self._sd = np, sd
        if self._ffmpeg is None:
            import imageio_ffmpeg
            self._ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if self._play_dev is None:
            self._play_dev = self._find_cable_output_device()

    def _find_cable_output_device(self) -> Optional[int]:
        """找到 CABLE Input 的**播放**设备索引（WASAPI 优先）。"""
        sd = self._sd
        devs = sd.query_devices()
        for want_wasapi in (True, False):
            for i, d in enumerate(devs):
                if want_wasapi and d["hostapi"] != 2:  # 2 = WASAPI
                    continue
                name = d["name"]
                if ("CABLE Input" in name or "CABLE In" in name) \
                        and d["max_output_channels"] > 0 and "16ch" not in name:
                    return i
        return None

    def available(self) -> Tuple[bool, str]:
        """检查发语音所需的环境是否就绪，返回 (是否可用, 说明)"""
        try:
            self._lazy()
        except Exception as exc:
            return False, f"依赖导入失败: {exc}"
        if self._play_dev is None:
            return False, "没找到 VB-CABLE 的 CABLE Input 播放设备"
        try:
            from wechatauto.guia import WeChatGUI  # noqa: F401
        except Exception as exc:
            return False, f"wechatauto.guia 不可用: {exc}"
        return True, "就绪"

    # ------------------------------------------------------------------ GUI
    def _get_gui(self):
        if self._gui is None:
            from wechatauto.guia import WeChatGUI
            self._gui = WeChatGUI()
        return self._gui

    def _window_rect(self):
        import win32gui
        gui = self._get_gui()
        return win32gui.GetWindowRect(gui.main_hwnd)

    def _current_chat(self) -> Optional[str]:
        """取当前打开的会话名。

        2026-10-18 定位：旧代码调 ``gui.current_chat()``，但 wechatauto 的
        ``WeChatGUI`` **没有这个方法**（只有 ``_current_chat`` 属性且为 None），
        异常被吞掉后 ``cur`` 恒为 None，于是每次发语音都会走 ``open_chat()``
        重新切会话——而切会话会重置输入区状态，正是「点语音按钮没反应」的诱因。
        真正的会话名在 UIA 驱动上：``uia.current_chat()``。
        """
        gui = self._get_gui()
        try:
            uia = gui._get_uia()
            if uia is not None:
                name = uia.current_chat()
                if name:
                    return str(name)
        except Exception as exc:
            logger.debug(f"[语音条] UIA 取当前会话失败: {exc}")
        for attr in ("_current_chat", "current_chat"):
            try:
                val = getattr(gui, attr, None)
                if callable(val):
                    val = val()
                if val:
                    return str(val)
            except Exception:
                continue
        return None

    def _force_foreground(self) -> None:
        """把微信窗口抢到真正的前台。

        2026-10-18：SendInput 的点击只打给前台窗口。窗口被挪动/被别的程序压住时，
        `gui.bring_to_front()` 的 SetForegroundWindow 常被 Windows 前台锁挡掉，
        于是「录音态差异」直接是 0.000（一次点击都没落到微信上）。
        先模拟一次 Alt 按键解除前台锁，再 SetForegroundWindow 才稳定生效。
        """
        try:
            import ctypes
            gui = self._get_gui()
            hwnd = int(getattr(gui, "main_hwnd", 0) or 0)
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
                time.sleep(0.2)
            user32.keybd_event(0x12, 0, 0, 0)   # Alt 按下
            user32.keybd_event(0x12, 0, 2, 0)   # Alt 抬起
            user32.ShowWindow(hwnd, 9)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            if user32.GetForegroundWindow() != hwnd:
                logger.warning("[语音条] 抢前台没成功，点击可能打不到微信")
        except Exception as exc:
            logger.debug(f"[语音条] 抢前台异常（继续尝试）: {exc}")

    def _content_rect(self, rect=None):
        """真正的内容区矩形（render 窗口）。

        微信主窗口带一圈 11px 边框，工具栏坐标应该按内容区算，而不是主窗口。
        取不到 render_rect 时退回主窗口矩形，保证旧环境仍能用。
        """
        try:
            gui = self._get_gui()
            rr = getattr(gui, "render_rect", None)
            if rr and len(rr) == 4 and (int(rr[2]) - int(rr[0])) > 200:
                return tuple(int(v) for v in rr)
        except Exception:
            pass
        return tuple(int(v) for v in (rect or self._window_rect()))

    def _points(self, rect):
        """按内容区边缘锚定，算出 (语音, 发送, 取消) 三个点击点。"""
        left, top, right, bottom = self._content_rect(rect)
        def at(dx_right, dy_bottom):
            return (right - dx_right, bottom - dy_bottom)
        return (at(*self.VOICE_EDGE), at(*self.SEND_EDGE), at(*self.CANCEL_EDGE))

    def _emoji_point(self, rect):
        left, top, right, bottom = self._content_rect(rect)
        return (left + self.EMOJI_LEFT, bottom - self.VOICE_EDGE[1])

    def _shot(self):
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=self._window_rect())

    @staticmethod
    def _diff(img_a, img_b) -> float:
        import numpy as np
        a = np.asarray(img_a.convert("L"), dtype="float32")
        b = np.asarray(img_b.convert("L"), dtype="float32")
        return float(np.mean(np.abs(a - b)))

    def _green_send_visible(self, img) -> bool:
        """录音态的判据：绿色 ↑ 发送键在不在。

        整图平均差不可靠：发完语音后聊天区多一个气泡，平均差同样会超过阈值，
        于是「已发出」被误判成「还卡在录音态」。微信的绿色发送键是录音态独有的
        颜色（深色主题下实测 RGB≈(70,158,103)，按钮直径约 38px），比整图差异稳。
        注意按钮**中心是黑色的 ↑ 箭头**，所以要在按钮范围内找绿色像素，
        不能只取正中心那一个点。
        """
        try:
            left, top = self._window_rect()[:2]
            px, py = self._points(self._window_rect())[1]
            x0, y0 = int(px - left), int(py - top)
            patch = img.convert("RGB").crop((x0 - 18, y0 - 18, x0 + 18, y0 + 18))
            import numpy as np
            a = np.asarray(patch, dtype="int16")
            r, g, b = a[..., 0], a[..., 1], a[..., 2]
            mask = (g > 120) & ((g - r) > 40) & ((g - b) > 25)
            return int(mask.sum()) >= 50
        except Exception:
            return False

    # ------------------------------------------------------------------ 音频
    def _to_wav(self, src_path: str) -> str:
        """任意音频 → 48kHz 单声道 wav（微信录音对采样率敏感）"""
        self._lazy()
        base = os.path.splitext(os.path.basename(src_path))[0]
        dst = os.path.join(self.cache_dir, f"{base}_48k.wav")
        subprocess.run([self._ffmpeg, "-y", "-i", src_path,
                        "-ar", str(self.TARGET_SR), "-ac", "1", "-f", "wav", dst],
                       check=True, capture_output=True)
        return dst

    def synthesize(self, text: str, tag: str = "voice") -> str:
        """edge-tts 合成 mp3，返回路径"""
        import asyncio
        import edge_tts
        safe = "".join(ch for ch in text[:16] if ch.isalnum()) or tag
        mp3 = os.path.join(self.cache_dir, f"{tag}_{safe}_{int(time.time())}.mp3")
        async def _run():
            await edge_tts.Communicate(text, self.tts_voice).save(mp3)
        asyncio.run(_run())
        return mp3

    def _play(self, wav_path: str) -> float:
        """把 wav 播进 CABLE Input，返回时长（秒）"""
        self._lazy()
        import wave
        with wave.open(wav_path, "rb") as w:
            frames = w.readframes(w.getnframes())
            sr = w.getframerate()
        data = self._np.frombuffer(frames, dtype="int16").astype("float32") / 32768.0
        if len(data) / sr > self.max_seconds:
            data = data[:int(self.max_seconds * sr)]
        stereo = self._np.stack([data * 0.9, data * 0.9], axis=1)
        with self._sd.OutputStream(device=self._play_dev, samplerate=sr,
                                   channels=2, dtype="float32") as out:
            out.write(stereo)
        return len(data) / sr

    # ------------------------------------------------------------------ 校验
    def _db(self):
        if getattr(self, "_db_obj", None) is None:
            from wechatauto import WeChatDB
            self._db_obj = WeChatDB()
        return self._db_obj

    def _chat_id(self, room_name: str) -> Optional[str]:
        try:
            if str(room_name).endswith("@chatroom"):
                return str(room_name)
            db = self._db()
            try:
                cid = db.group_name_to_id(room_name)
            except Exception:
                cid = None
            if cid:
                return cid
            try:
                return db.username_by_nickname(room_name)
            except Exception:
                return None
        except Exception as exc:
            logger.debug(f"[语音条] 取会话 ID 失败: {exc}")
            return None

    def _voice_landed(self, room_name: str, since_ts: float,
                      tries: int = 3, interval: float = 1.0) -> Optional[bool]:
        """回读微信数据库，确认语音条真的落地。None = 无法判断。"""
        cid = self._chat_id(room_name)
        if not cid:
            return None
        for _ in range(max(1, tries)):
            try:
                msgs = self._db().get_messages(cid, limit=6)
            except Exception as exc:
                logger.debug(f"[语音条] 回读数据库失败: {exc}")
                return None
            for m in msgs:
                try:
                    ts = float(m.get("create_time") or 0)
                except Exception:
                    continue
                if ts + 2 < since_ts:
                    continue
                label = str(m.get("type") or "")
                if "语音" in label or "voice" in label.lower():
                    return True
            time.sleep(interval)
        return False

    # ------------------------------------------------------------------ 发送
    def send_audio(self, room_name: str, audio_path: str) -> bool:
        """把音频文件作为语音条发到指定会话

        :param room_name: 微信里显示的会话名（群名/好友名）
        :param audio_path: 本地音频（mp3/wav/m4a 均可）
        """
        ok, why = self.available()
        if not ok:
            logger.warning(f"[语音条] 环境未就绪：{why}")
            return False

        gui = self._get_gui()
        gui.bring_to_front()
        time.sleep(0.8)
        # 2026-10-18：bring_to_front() 不一定真的抢到前台（Windows 前台锁），
        # 再补一次带 Alt 解锁的强制置前，否则后面所有点击都会打空。
        self._force_foreground()

        # 打开目标会话：**已经是目标会话就不要动**。
        # 实测（2026-10-16）：切会话动作会把输入区状态重置，之后点语音按钮不生效。
        try:
            cur = self._current_chat()
            if not cur or room_name not in str(cur):
                logger.info(f"[语音条] 当前会话 {cur!r} ≠ 目标 {room_name!r}，切换会话")
                gui.open_chat(room_name)
                time.sleep(1.2)
                # 切换后必须确认，否则可能把语音发到错误的会话里
                after = self._current_chat()
                if after and room_name not in str(after):
                    logger.warning(
                        f"[语音条] 切换后仍在 {after!r}，为避免发错会话已中止"
                    )
                    return False
            else:
                logger.info(f"[语音条] 已在目标会话 {cur!r}，不切换")
        except Exception as exc:
            logger.warning(f"[语音条] 会话检查/切换失败：{exc}")

        rect = self._window_rect()
        # 切会话之后焦点可能又被挪走，点位计算前再抢一次
        self._force_foreground()
        p_voice, p_send, p_cancel = self._points(rect)
        p_warmup = self._emoji_point(rect)
        cl, ct, cr, cb = self._content_rect(rect)
        if abs((cr - cl) - 1178) > 60 or abs((cb - ct) - 1529) > 60:
            logger.warning(
                f"[语音条] 内容区尺寸 {cr-cl}x{cb-ct} 与标定值 1178x1529 差异较大，"
                "按钮坐标可能失准，需要重新标定 VOICE_EDGE/SEND_EDGE/CANCEL_EDGE"
            )
        logger.info(
            f"[语音条] 主窗口 {rect} 内容区 {(cl, ct, cr, cb)} "
            f"语音按钮={p_voice} 发送={p_send} 取消={p_cancel}"
        )

        # 1) 先取消可能残留的录音态（正常态下该位置是空白，无害）
        try:
            gui._input.send_input_click(*p_cancel)
            time.sleep(0.7)
        except Exception:
            pass

        # 2) 热身（默认关闭）：旧代码在这里点「表情」按钮，但坐标实际命中
        #    **收藏**，弹出的「发送收藏给 XXX」模态框会吞掉后续所有点击。
        if self.warmup:
            try:
                gui._input.send_input_click(*p_warmup)
                time.sleep(0.8)
            except Exception:
                pass

        # 基线必须在退出录音态、热身之后取
        before = self._shot()

        # 3) 点语音按钮，开始录音
        record_started_at = time.time()
        try:
            gui._input.send_input_click(*p_voice)
        except Exception as exc:
            logger.warning(f"[语音条] 点击语音按钮失败：{exc}")
            return False
        time.sleep(1.2)

        after = self._shot()
        delta = self._diff(before, after)
        green = self._green_send_visible(after)
        if not green and delta < self.REC_STATE_DIFF:
            logger.warning(
                f"[语音条] 没进入录音态（绿色发送键未见，差异 {delta:.3f}），已中止，不点发送"
            )
            # 2026-10-18 补：中止前必须点一次取消。否则微信被留在录音态，
            # 下一次调用会「点语音按钮 = 结束录音」，差异更小、越修越坏。
            try:
                gui._input.send_input_click(*p_cancel)
            except Exception:
                pass
            try:
                before.save(os.path.join(self.cache_dir, "fail_before.png"))
                after.save(os.path.join(self.cache_dir, "fail_after.png"))
                logger.warning(f"[语音条] 失败截图已存到 {self.cache_dir}")
            except Exception:
                pass
            return False
        logger.info(
            f"[语音条] 已进入录音态（绿色发送键={green}，差异 {delta:.3f}）"
        )

        # 3) 把音频喂进虚拟声卡
        try:
            wav = self._to_wav(audio_path)
            seconds = self._play(wav)
            logger.info(f"[语音条] 已播放 {seconds:.2f} 秒音频")
        except Exception as exc:
            logger.error(f"[语音条] 播放音频失败：{exc}")
            # 播放失败要取消录音，避免录到环境音后误发
            try:
                gui._input.send_input_click(*p_cancel)
            except Exception:
                pass
            return False

        time.sleep(0.4)

        # 4) 点绿色发送
        try:
            gui._input.send_input_click(*p_send)
        except Exception as exc:
            logger.error(f"[语音条] 点发送失败：{exc}")
            return False

        # 5) 回读数据库确认落地（日志说「已发送」不代表真的发出去了）。
        #    基准时间用**开始录音**的时刻：微信给语音消息记的 create_time 是
        #    录音过程中的时间，比「点发送」早 2—6 秒，拿点击时间当基准会把
        #    刚发出的语音条过滤掉，误判成失败。
        confirmed = None
        if self.verify:
            confirmed = self._voice_landed(room_name, record_started_at - 5)
        if confirmed is False:
            time.sleep(0.8)
            still_recording = self._green_send_visible(self._shot())
            if still_recording:
                try:
                    gui._input.send_input_click(*p_cancel)
                except Exception:
                    pass
                logger.error(
                    f"[语音条] 数据库里没有新语音条，且绿色发送键仍在（录音态）——判定发送失败 [{room_name}]"
                )
                return False
            logger.warning(
                f"[语音条] 已退出录音态但数据库暂未确认，按成功处理 [{room_name}]"
            )
        elif confirmed is None:
            logger.debug("[语音条] 无法回读数据库，跳过落地校验")
        else:
            logger.info(f"[语音条] 数据库已确认语音条落地 [{room_name}]")

        time.sleep(0.6)
        logger.info(f"[语音条] 已发送语音到 [{room_name}]（约 {seconds:.0f} 秒）")
        return True

    def send_text(self, room_name: str, text: str) -> bool:
        """文本 → TTS → 语音条"""
        try:
            mp3 = self.synthesize(text)
        except Exception as exc:
            logger.error(f"[语音条] TTS 合成失败：{exc}")
            return False
        return self.send_audio(room_name, mp3)


# --------------------------------------------------------------------- 自测
if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "文件传输助手"
    words = sys.argv[2] if len(sys.argv) > 2 else "这是鲸鱼娘的语音测试，主人听到这句话，就说明接入成功了。"
    vs = VoiceSender()
    print("环境检查:", vs.available())
    print("合成语音 ...")
    mp3 = vs.synthesize(words)
    print("合成完成:", mp3)
    print("发送 ...")
    print("结果:", vs.send_audio(target, mp3))
