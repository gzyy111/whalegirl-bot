"""微信聊天机器人 - 主入口"""

import faulthandler
import json
import re
import socket
import sys
import asyncio
import random
import threading
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QTimer, QObject, qInstallMessageHandler

from src.config_manager import ConfigManager, LOCKED_STYLE_NAME
from src.security import SecurityManager
from src.ai_engine import AIEngine
from src.style_manager import StyleManager
from src.wechat_bridge import WeChatBridge
from src.qq_bridge import QQBridge
from src.wecom_bridge import WxWorkBridge
from src.message_handler import MessageHandler
from src.tts_service import TTSService
from src.song_service import SongService
from src.cover_service import CoverService
from src.divination_service import DivinationService
from src.joke_service import JokeService
from src.catchup_service import CatchupService
from src.singing_service import SingingService
from src.acestep_service import ACEStepService
from src.music_provider import LXMusicProvider
from src.asr_service import ASRService
from src.error_logger import (
    install_exception_hooks,
    log_error_message,
    log_exception,
    log_loguru_error,
    mark_startup,
)
from src.gui.main_window import MainWindow
from src.web.app import app as web_app
from src.web.routes import init_routes
from src.web.websocket import broadcast

import uvicorn


_SINGLE_INSTANCE_SOCKET: socket.socket | None = None
_CRASH_STREAM = None
_RUNTIME_DIR = Path("runtime")
_HEARTBEAT_FILE = _RUNTIME_DIR / "bot.heartbeat"
_CLEAN_EXIT_FILE = _RUNTIME_DIR / "bot.clean-exit"


def _install_crash_logging() -> None:
    """记录原生崩溃堆栈，并安装 Python 未捕获异常钩子。"""
    global _CRASH_STREAM
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        _CRASH_STREAM = (log_dir / "crash.log").open(
            "a",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        faulthandler.enable(file=_CRASH_STREAM, all_threads=True)
    except Exception as exc:
        log_error_message(
            context="main._install_crash_logging",
            error_type=type(exc).__name__,
            error_message=str(exc),
            stack="启用 faulthandler 失败",
        )
        print(f"启用崩溃日志失败: {exc}", file=sys.stderr)

    install_exception_hooks()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_heartbeat() -> None:
    try:
        _write_text_atomic(_HEARTBEAT_FILE, str(datetime.now().timestamp()))
    except Exception as exc:
        log_error_message(
            context="main._write_heartbeat",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        print(f"写入机器人心跳失败: {exc}", file=sys.stderr)


def _mark_clean_exit() -> None:
    try:
        _write_text_atomic(
            _CLEAN_EXIT_FILE,
            datetime.now().isoformat(),
        )
    except Exception as exc:
        log_error_message(
            context="main._mark_clean_exit",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        print(f"写入正常退出标记失败: {exc}", file=sys.stderr)


_LOG_LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+\|\s+(?P<level>TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)"
    r"\s+\|\s+.*?\s+-\s+(?P<message>.*)$"
)


def _restore_recent_monitor_text(window, limit: int = 250) -> None:
    """恢复上次运行最近一段日志，避免重启后监控文本空白。"""
    path = Path("logs") / "chatbot.log"
    if not path.is_file():
        return

    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 1024 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except Exception as exc:
        window.log_tab.log_viewer.append_log(
            "WARNING",
            f"恢复上次监控日志失败: {exc}",
        )
        return

    entries: list[tuple[str, str, str]] = []
    for raw_line in text.splitlines()[-limit * 2:]:
        match = _LOG_LINE_RE.match(raw_line)
        if not match:
            continue
        entries.append(
            (
                match.group("level"),
                match.group("message"),
                match.group("time")[11:19],
            )
        )

    for level, message, timestamp in entries[-limit:]:
        window.log_tab.log_viewer.append_log(
            level,
            message,
            timestamp=timestamp,
        )
    if entries:
        window.log_tab.log_viewer.append_log(
            "SUCCESS",
            f"已恢复上次运行监控日志 {min(len(entries), limit)} 条",
        )


def acquire_single_instance(port: int = 47653) -> bool:
    """用本地端口占位确保同一时间只有一个机器人实例在运行。

    重复启动会导致两个进程同时操作微信界面：消息重复、发送超时、
    触发微信风控，看起来就像卡死。
    """
    global _SINGLE_INSTANCE_SOCKET
    if _SINGLE_INSTANCE_SOCKET is not None:
        return True

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        try:
            sock.close()
        except Exception:
            pass
        return False
    _SINGLE_INSTANCE_SOCKET = sock
    return True


def _report_duplicate_start(message: str) -> None:
    """重复启动时给出提示（不弹模态框，避免把进程挂住）。"""
    print(message)
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(
            log_dir / "duplicate-start.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(f"{datetime.now().isoformat()} {message}\n")
    except Exception:
        pass


def _install_qt_message_filter() -> None:
    """过滤 Qt/Windows 外壳产生的无文件名重复警告，保留其他错误。"""
    ignored = {
        "QFSFileEngine::open: No file name specified",
    }

    def handler(mode, context, message: str) -> None:
        if message.strip() in ignored:
            return
        if sys.stderr is not None:
            sys.stderr.write(f"{message}\n")
            sys.stderr.flush()

    qInstallMessageHandler(handler)


class AsyncBridge(QObject):
    """Qt 线程与 asyncio 线程之间的桥梁"""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop

    def call(self, coro_func):
        """传入一个返回协程的函数（不要传协程对象）"""
        try:
            coro = coro_func()
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            future.add_done_callback(self._handle_task_error)
        except Exception as e:
            logger.error(f"异步任务创建失败: {e}")

    def _handle_task_error(self, future):
        if future.cancelled():
            return
        exc = future.exception()
        if exc:
            logger.error(f"异步任务异常: {exc}")


class ChatBot:
    """聊天机器人主控制器"""

    def __init__(self):
        self.config: ConfigManager | None = None
        self.security: SecurityManager | None = None
        self.ai: AIEngine | None = None
        self.style: StyleManager | None = None
        self.wechat: WeChatBridge | None = None
        self.handler: MessageHandler | None = None
        # QQ 端：独立桥接、独立安全策略、独立处理管线
        self.qq: QQBridge | None = None
        self.qq_handler: MessageHandler | None = None
        self.qq_security: SecurityManager | None = None
        # 企业微信端：独立桥接、独立安全策略、独立处理管线
        self.wecom: WxWorkBridge | None = None
        self.wecom_handler: MessageHandler | None = None
        self.wecom_security: SecurityManager | None = None
        self._wecom_enabled = False
        self._wecom_running = False
        self._wecom_watchdog_task: asyncio.Task | None = None
        self.window: MainWindow | None = None
        self._async_bridge: AsyncBridge | None = None
        self._running = False
        self._qq_running = False
        self._sticker_task: asyncio.Task | None = None
        self._connection_watchdog_task: asyncio.Task | None = None
        self._qq_watchdog_task: asyncio.Task | None = None
        self._reconnect_lock: asyncio.Lock | None = None
        self._recent_messages: list[dict] = []
        self._logs: list[dict] = []
        self._max_recent = 500

    # ------------------------------------------------------------------
    # 算命（排盘 + 断命 + 人设解读）
    # ------------------------------------------------------------------
    def _setup_divination(self) -> None:
        """2026-10-19 新增：给鲸鱼娘加算命能力。

        排盘是确定性计算（lunar-python），断语来自 rules.py 的规则表，
        模型只做语言组织——所以不会出现「模型把大运算反」这类硬伤。
        """
        div_cfg = self.config.get("divination", {}) or {}
        persona = ""
        try:
            persona = self.style.get_active().system_prompt
        except Exception:  # 人设读不到也不能挡住启动
            persona = ""
        self.divination = DivinationService(
            ai=self.ai,
            enabled=bool(div_cfg.get("enabled", True)),
            persona=persona,
            max_tokens=int(div_cfg.get("max_tokens", 700)),
            temperature=float(div_cfg.get("temperature", 0.88)),
            pending_ttl=float(div_cfg.get("pending_ttl", 300.0)),
            true_solar=bool(div_cfg.get("true_solar", False)),
            longitude=div_cfg.get("longitude"),
            tarot_images=bool(div_cfg.get("tarot_images", True)),
        )
        ok = self.divination.enabled
        logger.info(
            f"[算命] {'就绪' if ok else '关闭'}：八字/起卦/黄历；"
            f"{'启用真太阳时' if self.divination.true_solar else '按北京时间排盘'}"
        )

    # ------------------------------------------------------------------
    # 讲笑话（2026-10-19 新增）
    # ------------------------------------------------------------------
    def _setup_joke(self) -> None:
        """给鲸鱼娘加「讲笑话」能力。

        笑话全部是本地库（data/jokes.json，DeepSeek 平台梗 + 鲸鱼/深海梗），
        命中意图就秒回，不调模型；讲完由 handler 后台补一张贴题表情包。
        """
        joke_cfg = self.config.get("joke", {}) or {}
        self.joke = JokeService(
            file=str(joke_cfg.get("file", "./data/jokes.json")),
            history_file=str(
                joke_cfg.get("history_file", "./data/joke_history.json")
            ),
            enabled=bool(joke_cfg.get("enabled", True)),
            recent_avoid=int(joke_cfg.get("recent_avoid", 8) or 8),
            global_avoid=int(joke_cfg.get("global_avoid", 14) or 14),
            max_count=int(joke_cfg.get("max_count", 2) or 2),
        )
        st = self.joke.stats()
        self.joke_sticker_after = bool(joke_cfg.get("sticker_after", True))
        logger.info(
            "[笑话] %s：%d 条（%s），可配图 %d 条"
            % (
                "就绪" if self.joke.available else "未启用/库为空",
                st["total"],
                "、".join(f"{k} {v}" for k, v in sorted(st["kinds"].items()))
                or "无",
                st["with_sticker"],
            )
        )

    # ------------------------------------------------------------------
    # 开机补消息
    # ------------------------------------------------------------------
    def _setup_catchup(self, wx_cfg: dict) -> None:
        """开机补消息：把睡着时攒下的 @ / 私聊用一句话接住。

        历史回放（``is_history=True``）里的消息本来只归档、不回复，避免一开机
        就把昨天的话全回一遍。副作用是昨晚 @ 她的话一条都没接，群里像没人。
        这里挑出「确实在找她」的那部分，回放窗口结束后**每个会话只发一句**。
        """
        raw = wx_cfg.get("catchup", {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        replay = float(wx_cfg.get("replay_grace_seconds", 6.0) or 0.0)
        self.catchup = CatchupService(
            wechat=self.wechat,
            ai=self.ai,
            enabled=bool(raw.get("enabled", True)),
            only_at_me=bool(raw.get("only_at_me", False)),
            include_private=bool(raw.get("include_private", True)),
            max_messages=int(raw.get("max_messages", 40) or 40),
            max_groups=int(raw.get("max_groups", 3) or 3),
            max_chars=int(raw.get("max_chars", 60) or 60),
            min_messages=int(raw.get("min_messages", 3) or 1),
            min_offline_minutes=float(
                raw.get("min_offline_minutes", 60) or 0.0
            ),
            greet_min_gap_minutes=float(
                raw.get("greet_min_gap_minutes", 10) or 0.0
            ),
            delay_after_connect=float(
                raw.get("delay_after_connect", replay + 3.0)
            ),
            state_path=str(raw.get("state_path", "./data/catchup_state.json")),
            archive_path=str(
                raw.get("archive_path", "./data/message_archive.jsonl")
            ),
            pending_path=str(
                raw.get("pending_path", "./data/catchup_pending.json")
            ),
            greet_without_messages=bool(
                raw.get("greet_without_messages", True)
            ),
            skip_seen_live=bool(raw.get("skip_seen_live", True)),
            dry_run=bool(raw.get("dry_run", False)),
        )
        logger.info(
            "[开机补消息] %s：只收@%s，私聊%s，最多 %d 个会话 / %d 条，"
            "没人叫时攒够 %d 条才冒泡，间隔 %.0f 分钟以上才补，"
            "没有未接消息时%s，回放结束后 %.0f 秒处理"
            % (
                "启用" if self.catchup.enabled else "关闭",
                "是" if self.catchup.only_at_me else "否(全收)",
                "收" if self.catchup.include_private else "不收",
                self.catchup.max_groups,
                self.catchup.max_messages,
                self.catchup.min_messages,
                self.catchup.min_offline_minutes,
                "也冒一句问候" if self.catchup.greet_without_messages else "不问候",
                self.catchup.delay_after_connect,
            )
        )

    def _schedule_catchup(self) -> None:
        """微信连上后，等历史回放窗口结束，再补一次「睡着时收到的消息」。"""
        service = getattr(self, "catchup", None)
        if not service or not service.enabled:
            return
        running = getattr(self, "_catchup_task", None)
        if running and not running.done():
            return

        async def _run() -> None:
            await asyncio.sleep(service.delay_after_connect)
            try:
                results = await service.flush()
            except Exception as exc:
                logger.warning(f"[开机补消息] 处理失败: {exc}")
                return
            if results:
                total = sum(int(item.get("count", 0)) for item in results)
                logger.info(
                    f"[开机补消息] 完成：{len(results)} 个会话 / {total} 条"
                )

        try:
            self._catchup_task = asyncio.create_task(
                _run(), name="catchup-after-replay"
            )
        except RuntimeError as exc:
            logger.warning(f"[开机补消息] 无法创建任务: {exc}")

    # ------------------------------------------------------------------
    # 语音 / 点歌（双端共用）
    # ------------------------------------------------------------------
    def _setup_voice(self) -> None:
        """构建双端共用的语音合成与点歌服务。"""
        voice_cfg = self.config.get("voice", {}) or {}
        songs_cfg = voice_cfg.get("songs", {}) or {}
        quick_cfg = voice_cfg.get("quick_replies", {}) or {}

        self.voice_enabled = bool(voice_cfg.get("enabled", True))
        self.voice_cfg = {
            "enabled": self.voice_enabled,
            "engine": str(voice_cfg.get("engine", "auto")),
            "prewarm": bool(voice_cfg.get("prewarm", True)),
            "prewarm_count": int(voice_cfg.get("prewarm_count", 6) or 6),
            "quick_enabled": bool(quick_cfg.get("enabled", True)),
            "quick_max_chars": int(quick_cfg.get("max_chars", 10) or 10),
            "songs_enabled": bool(songs_cfg.get("enabled", True)),
            "songs_max_duration": float(
                songs_cfg.get("max_duration", 55.0) or 55.0
            ),
        }

        self.tts = TTSService(
            cache_dir=voice_cfg.get("cache_dir", "./data/voice_cache"),
            enabled=self.voice_enabled,
            engine=voice_cfg.get("engine", "auto"),
            default_voice=voice_cfg.get(
                "default_voice",
                "zh-CN-XiaoxiaoNeural",
            ),
            rate=voice_cfg.get("rate", "+0%"),
            pitch=voice_cfg.get("pitch", "+0Hz"),
            timeout=voice_cfg.get("timeout", 25.0),
            max_chars=voice_cfg.get("max_chars", 400),
            retries=voice_cfg.get("retries", 2),
        )
        # 真实歌曲下载源（洛雪音乐服务器，校园网内直连）
        music_cfg = voice_cfg.get("music", {}) or {}
        self.music = LXMusicProvider(
            base_url=music_cfg.get("base_url", ""),
            password=music_cfg.get("password", ""),
            username=music_cfg.get("username", ""),
            user_password=music_cfg.get("user_password", ""),
            quality=music_cfg.get("quality", "128k"),
            enabled=bool(music_cfg.get("enabled", False)),
            timeout=music_cfg.get("timeout", 30.0),
            cache_dir=music_cfg.get("cache_dir", "./data/music_cache"),
            max_size_mb=music_cfg.get("max_size_mb", 50.0),
        )
        # 真唱后端（SoulX-Singer，跑在独立 conda 环境，以子进程调用）
        singing_cfg = songs_cfg.get("singing", {}) or {}
        self.singing = SingingService(
            python_exe=singing_cfg.get(
                "python_exe", r"D:\miniconda\envs\soulxsinger\python.exe"
            ),
            script=singing_cfg.get(
                "script",
                r"D:\\SoulX-Singer"
                r"\SoulX-Singer-main\sing_once.py",
            ),
            cache_dir=singing_cfg.get(
                "cache_dir", "./data/song_cache/singing"
            ),
            enabled=bool(singing_cfg.get("enabled", True)),
            timeout=singing_cfg.get("timeout", 180.0),
            mode=singing_cfg.get("mode", "melody"),
        )
        # 端到端出歌后端（ACE-Step，独立 conda 环境里的 HTTP 服务）
        ace_cfg = songs_cfg.get("acestep", {}) or {}
        self.acestep = ACEStepService(
            base_url=ace_cfg.get("base_url", "http://127.0.0.1:8001"),
            cache_dir=ace_cfg.get("cache_dir", "./data/song_cache/acestep"),
            enabled=bool(ace_cfg.get("enabled", True)),
            duration=ace_cfg.get("duration", 30.0),
            caption=ace_cfg.get(
                "caption", "pop, ballad, female vocal, piano, warm, gentle"),
            timeout=ace_cfg.get("timeout", 300.0),
            auto_start=bool(ace_cfg.get("auto_start", True)),
            python_exe=ace_cfg.get(
                "python_exe", r"D:\miniconda\envs\acestep\python.exe"),
            repo=ace_cfg.get(
                "repo", r"D:\\ACE-Step-1.5"
                        r"\ACE-Step-1.5-main"),
        )
        self.song = SongService(
            tts=self.tts,
            cache_dir=songs_cfg.get("cache_dir", "./data/song_cache"),
            enabled=self.voice_enabled and self.voice_cfg["songs_enabled"],
            max_duration=songs_cfg.get("max_duration", 55.0),
            sing_rate=songs_cfg.get("sing_rate", "+8%"),
            sing_pitch=songs_cfg.get("sing_pitch", "+35Hz"),
            voice=songs_cfg.get("voice", "") or "",
            max_lyrics_chars=songs_cfg.get("max_lyrics_chars", 300),
            music=self.music,
            music_timeout=music_cfg.get("timeout", 30.0),
            singing=self.singing,
            acestep=self.acestep,
        )
        # 2026-10-18 新增：真唱点歌（SVC 翻唱）。群友点具体某首歌时，
        # 用鲸鱼娘的音色把原曲翻唱出来，而不是回原唱或念歌词。
        cover_cfg = songs_cfg.get("cover", {}) or {}
        self.cover = CoverService(
            enabled=bool(cover_cfg.get("enabled", True)) and self.voice_enabled,
            python_exe=str(
                cover_cfg.get(
                    "python_exe", r"D:\miniconda\envs\soulxsinger\python.exe")
            ),
            script=str(
                cover_cfg.get(
                    "script",
                    r"D:\\鲸鱼娘SVC翻唱_20261017"
                    r"\cover.py")
            ),
            work_dir=str(
                cover_cfg.get(
                    "work_dir",
                    r"D:\\鲸鱼娘SVC翻唱_20261017")
            ),
            cache_dir=str(cover_cfg.get("cache_dir", "./data/song_cache/cover")),
            library_dirs=[
                str(p) for p in (cover_cfg.get("library_dirs") or [])
            ],
            default_mode=str(cover_cfg.get("default_mode", "segment")),
            segment_seconds=float(cover_cfg.get("segment_seconds", 45.0)),
            prompt=str(cover_cfg.get("prompt", "")),
            timeout=float(cover_cfg.get("timeout", 900.0)),
            max_queue=int(cover_cfg.get("max_queue", 8)),
            voice_version=str(cover_cfg.get("voice_version", "zh11")),
            mix_vocal_db=float(cover_cfg.get("mix_vocal_db", -15.0)),
            mix_inst_db=float(cover_cfg.get("mix_inst_db", -20.0)),
        )
        _cok, _cwhy = self.cover.available()
        logger.info(
            f"[点歌真唱] {'就绪' if _cok else '不可用'}：{_cwhy}"
            f"；默认{'一段' if self.cover.default_mode == 'segment' else '整首'}"
            f"（{self.cover.segment_seconds:.0f} 秒）"
        )
        if self.voice_enabled:
            _ok, _why = self.singing.available()
            logger.info(
                f"[真唱] 歌声后端 {'就绪' if _ok else '不可用（' + _why + '）'}"
                f"，内置曲目：{'/'.join(self.singing.song_names()) or '无'}"
            )
            _aok, _awhy = self.acestep.available()
            logger.info(
                f"[ACE] 端到端出歌 {'就绪' if _aok else '不可用（' + _awhy + '）'}"
                f"，{self.acestep.duration:.0f} 秒/首，{self.acestep.base_url}"
            )

        if not self.voice_enabled:
            logger.info("语音 / 点歌功能已关闭（voice.enabled=false）")
        else:
            status = self.tts.status()
            music_note = (
                f"，音乐源已启用({self.music.base_url})"
                if self.music.enabled
                else ""
            )
            logger.info(
                f"语音引擎就绪：{status['engine_label']}，"
                f"曲库 {self.song.song_count()} 首{music_note}"
            )
        self.tts.purge_tmp()

    def _setup_asr(self) -> None:
        """Build the shared local speech recognition service."""
        asr_cfg = self.config.get("asr", {}) or {}
        self.asr = ASRService(
            enabled=bool(asr_cfg.get("enabled", True)),
            model=str(asr_cfg.get("model", "base") or "base"),
            model_dir=str(
                asr_cfg.get(
                    "model_dir",
                    "./data/models/faster-whisper",
                )
                or "./data/models/faster-whisper"
            ),
            cache_dir=str(
                asr_cfg.get("cache_dir", "./data/voice_inbox")
                or "./data/voice_inbox"
            ),
            language=str(asr_cfg.get("language", "zh") or "zh"),
            compute_type=str(
                asr_cfg.get("compute_type", "int8") or "int8"
            ),
            cpu_threads=int(asr_cfg.get("cpu_threads", 4) or 4),
            beam_size=int(asr_cfg.get("beam_size", 1) or 1),
            max_audio_mb=float(asr_cfg.get("max_audio_mb", 20.0) or 20.0),
            hf_endpoint=str(
                asr_cfg.get("hf_endpoint", "https://hf-mirror.com") or ""
            ),
        )
        if self.asr.enabled:
            logger.info(
                f"语音识别已就绪：{self.asr.model_name} "
                f"({self.asr.compute_type}, {self.asr.cpu_threads} 线程)"
            )
        else:
            logger.info("语音识别已关闭（asr.enabled=false）")

    async def rebuild_voice(self) -> dict:
        """按当前 config 重建语音 / 点歌 / 音乐服务，并热更新到双端管线。"""
        old_music = getattr(self, "music", None)
        if old_music is not None:
            try:
                await old_music.close()
            except Exception:
                pass
        self._setup_voice()
        kwargs = self._voice_handler_kwargs({})
        for handler in (self.handler, getattr(self, "qq_handler", None)):
            if handler is not None and hasattr(handler, "apply_voice_config"):
                handler.apply_voice_config(**kwargs)
        return self.voice_status()

    def _voice_handler_kwargs(self, platform_cfg: dict) -> dict:
        """按平台生成传给 MessageHandler 的语音相关参数。"""
        platform_voice = (platform_cfg or {}).get("voice", {}) or {}
        enabled = self.voice_enabled and bool(
            platform_voice.get("enabled", True)
        )
        if not getattr(self, "voice_cfg", None):
            return {
                "song_service": None,
                "voice_enabled": False,
                "asr_service": getattr(self, "asr", None),
                "divination_service": getattr(self, "divination", None),
                "joke_service": getattr(self, "joke", None),
            }
        return {
            "song_service": self.song if enabled else None,
            "cover_service": (
                getattr(self, "cover", None) if enabled else None
            ),
            "divination_service": getattr(self, "divination", None),
            "joke_service": getattr(self, "joke", None),
            "voice_enabled": enabled,
            "asr_service": getattr(self, "asr", None),
            "quick_replies_enabled": bool(
                self.voice_cfg.get("quick_enabled", True)
            ),
            "quick_replies_max_chars": int(
                self.voice_cfg.get("quick_max_chars", 10)
            ),
            "song_max_duration": float(
                self.voice_cfg.get("songs_max_duration", 55.0)
            ),
        }

    def voice_status(self) -> dict:
        """语音 / 点歌状态，供 Web 与状态接口展示。"""
        tts_status = self.tts.status() if getattr(self, "tts", None) else {}
        song_status = self.song.status() if getattr(self, "song", None) else {}
        songs = self.song.list_songs() if getattr(self, "song", None) else []
        music_status = (
            self.music.status() if getattr(self, "music", None) else {}
        )
        return {
            "enabled": bool(getattr(self, "voice_enabled", False)),
            "tts": tts_status,
            "song": song_status,
            "music": music_status,
            "songs": songs,
            "asr": (
                self.asr.status()
                if getattr(self, "asr", None)
                else {"enabled": False}
            ),
        }

    def initialize(self, config_path: str = "config.yaml") -> bool:
        try:
            self.config = ConfigManager(config_path)
            password = self.config.get("security.password") or "default"
            self.config.load(password)

            log_cfg = self.config.get("logging", {})
            logger.remove()
            if sys.stderr is not None:
                logger.add(sys.stderr, level=log_cfg.get("level", "INFO"))
            log_file = log_cfg.get("file", "./logs/chatbot.log")
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            logger.add(
                log_file,
                level=log_cfg.get("level", "INFO"),
                rotation=log_cfg.get("rotation", "10 MB"),
                retention=log_cfg.get("retention", "7 days"),
            )
            logger.add(
                log_loguru_error,
                level="ERROR",
                format="{message}",
                backtrace=True,
                diagnose=True,
                catch=True,
                enqueue=False,
            )

            sec_cfg = self.config.get("security", {})
            self.security = SecurityManager(
                rate_limit=sec_cfg.get("rate_limit", 20),
                circuit_breaker_threshold=sec_cfg.get("circuit_breaker_threshold", 5),
                circuit_breaker_timeout=sec_cfg.get("circuit_breaker_timeout", 5),
            )

            ai_cfg = self.config.get("ai", {})
            api_key = self.config.get_decrypted("ai.api_key_encrypted")
            if not api_key:
                api_key = ai_cfg.get("api_key", "")
                if api_key:
                    encrypted = self.config.encrypt_value(api_key)
                    self.config.set("ai.api_key_encrypted", encrypted)
                    self.config.set("ai.api_key", "")
                    self.config.save()

            self.ai = AIEngine(
                api_url=ai_cfg.get("api_url", ""),
                api_key=api_key,
                model=ai_cfg.get("model", "gpt-4"),
                max_tokens=ai_cfg.get("max_tokens", 60),
                temperature=ai_cfg.get("temperature", 0.7),
                timeout=ai_cfg.get("timeout", 10),
                max_retries=ai_cfg.get("max_retries", 2),
                api_style=ai_cfg.get("api_style", "auto"),
            )
            # 2026-10-18：每次 AI 记账后把用量实时推给 Web 控制台。
            # 原先网页只在 10 秒轮询时读 /api/status，而且计数是纯内存的，
            # 一重启就归零——看着就是「Token 用量一直是 0」。
            self.ai.on_usage = self._on_ai_usage

            style_cfg = self.config.get("style", {})
            self.style = StyleManager(
                styles_dir="styles",
                custom_dir=style_cfg.get("custom_styles_dir", "styles/custom"),
                locked_name=LOCKED_STYLE_NAME,
            )
            self.style.load_all()
            self.style.set_active(LOCKED_STYLE_NAME)
            self.config.set("style.active", LOCKED_STYLE_NAME)

            # 算命：排盘用 lunar-python（确定性），断语由规则层给出，
            # 大模型只负责用鲸鱼娘的口吻组织语言。
            self._setup_divination()

            # 讲笑话：本地笑话库（DeepSeek 平台梗 + 鲸鱼梗），不调模型
            self._setup_joke()

            # 语音合成 + 点歌：双端共用同一套服务与磁盘缓存
            self._setup_voice()
            self._setup_asr()

            wx_cfg = self.config.get("wechat", {})
            demo_mode = self.config.get("demo_mode", False)

            if demo_mode:
                from src.demo_bridge import DemoBridge
                self.wechat = DemoBridge(
                    target_groups=["Demo群"],
                    on_message=self._on_message,
                    reply_delay=0.0,
                )
                logger.info("已启用 Demo 模式（无需微信连接）")
            else:
                self.wechat = WeChatBridge(
                    target_groups=wx_cfg.get("target_groups", []),
                    on_message=self._on_message,
                    reply_delay=wx_cfg.get("reply_delay", 0.0),
                    backend=wx_cfg.get("backend", "auto"),
                    listen_private=wx_cfg.get("listen_private", True),
                    replay_grace_seconds=wx_cfg.get(
                        "replay_grace_seconds", 6.0
                    ),
                )

            # 2026-10-20：开机补消息。回放里的 @ / 私聊不再默默归档，
            # 而是在回放结束后每个会话用一句话接住。
            self._setup_catchup(wx_cfg)

            proactive_cfg = wx_cfg.get("proactive", {}) or {}
            idle_cfg = proactive_cfg.get("idle", {}) or {}
            image_search_cfg = wx_cfg.get("image_search", {}) or {}
            savage_cfg = wx_cfg.get("savage", {}) or {}
            # 2026-10-16 新增：风格描述块 + 输出侧护栏。两者都允许写成
            # 布尔（简写）或 {enabled: bool, ...}（完整写法）。
            sg_raw = wx_cfg.get("style_guide", True)
            style_guide_enabled = (
                bool(sg_raw.get("enabled", True))
                if isinstance(sg_raw, dict) else bool(sg_raw)
            )
            og_raw = wx_cfg.get("output_guard", True)
            if isinstance(og_raw, dict):
                output_guard_enabled = bool(og_raw.get("enabled", True))
                output_guard_max_chars = int(og_raw.get("max_chars", 90))
            else:
                output_guard_enabled = bool(og_raw)
                output_guard_max_chars = 90
            # 2026-10-16 新增：非点名群消息的参与概率（P1-2 最小版）
            participation_cfg = wx_cfg.get("participation", {}) or {}
            if not isinstance(participation_cfg, dict):
                participation_cfg = {}
            # 2026-10-16 新增：本地 jevos 参与判定器。
            # 官方 TypeSafe Jev API 在本机被地域封锁（HTTP 451），改用同名
            # 开源实现 jevos 跑在本地 CPU 上，详见 src/jev_judge.py。
            jev_cfg = wx_cfg.get("jev", {}) or {}
            if not isinstance(jev_cfg, dict):
                jev_cfg = {}
            jev_judge = None
            if jev_cfg.get("enabled", True):
                jev_endpoint = jev_cfg.get(
                    "endpoint", "http://127.0.0.1:8017/v1/systemone"
                )
                try:
                    from src.jev_judge import JevJudge

                    jev_judge = JevJudge(
                        endpoint=jev_endpoint,
                        enabled=True,
                        addressed_threshold=jev_cfg.get(
                            "addressed_threshold", 0.55
                        ),
                        should_reply_threshold=jev_cfg.get(
                            "should_reply_threshold", 0.35
                        ),
                        fallback_participation=jev_cfg.get(
                            "fallback_participation", 0.15
                        ),
                        timeout=jev_cfg.get("timeout", 3.0),
                    )
                    logger.info(f"本地 jevos 参与判定已启用: {jev_endpoint}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"初始化 jevos 判定失败，退回概率参与: {e}")
                    jev_judge = None
            # 2026-10-16 新增：本地表情包库。
            # 项目自带的「收藏表情」依赖 UIA 打开微信表情面板，本机实测必失败，
            # 改成从本地图片目录挑图 → 转格式 → 走 send_image 发出。
            sticker_cfg = wx_cfg.get("sticker_library", {}) or {}
            if not isinstance(sticker_cfg, dict):
                sticker_cfg = {}
            sticker_library = None
            sticker_recall = None
            if sticker_cfg.get("enabled", True):
                try:
                    from src.sticker_library import StickerLibrary

                    sticker_library = StickerLibrary(
                        dirs=sticker_cfg.get("dirs") or [
                            "./表情包/deepseek-hd",
                            "./表情包/blue-fish-hd",
                            "./表情包/owner-picks",
                        ],
                        cache_dir=sticker_cfg.get(
                            "cache_dir", "./data/sticker_cache"
                        ),
                        max_side=sticker_cfg.get("max_side", 800),
                        recent_avoid=sticker_cfg.get("recent_avoid", 30),
                        # 2026-10-16 v2：多维标签（版式/有无文字/载体/构图/场景/服装/主体/腿部）
                        v2_enabled=sticker_cfg.get("v2_enabled", True),
                        v2_tags_file=sticker_cfg.get(
                            "v2_tags_file", "./表情包/tags_v2.json"
                        ),
                        v2_mode=sticker_cfg.get("v2_mode", "safe"),
                        # 2026-10-19：编号点名发图。
                        # named_only 默认空 = 不做任何保护，所有图都能随机发；
                        # 想保护某几张就填编号（如 [006]），它们只能被点名发出来。
                        named_only=sticker_cfg.get("named_only") or [],
                        aliases_file=sticker_cfg.get(
                            "aliases_file",
                            "./表情包/aigengtu/点名表.json",
                        ),
                        # 2026-10-19：形态黑名单（长条截图 / 超大图），只影响随机与召回
                        rejects_file=sticker_cfg.get(
                            "rejects_file",
                            "./表情包/aigengtu/rejects.json",
                        ),
                        enabled=True,
                    )
                    recall_cfg = sticker_cfg.get("recall", {}) or {}
                    sticker_recall = None
                    if recall_cfg.get("enabled", True):
                        try:
                            from src.sticker_recall import StickerRecall

                            sticker_recall = StickerRecall(
                                items_file=recall_cfg.get(
                                    "items_file", "./表情包/aigengtu/stickers.json"),
                                model=recall_cfg.get("model", "BAAI/bge-small-zh-v1.5"),
                                top_k=recall_cfg.get("top_k", 4),
                                min_score=recall_cfg.get("min_score", 0.45),
                                emb_cache=recall_cfg.get(
                                    "emb_cache", "./data/sticker_emb.npz"),
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"表情向量召回初始化失败（退回随机）: {e}")
                    _st = sticker_library.stats()
                    logger.info(
                        "本地表情包库已启用: %d 张，来自 %s；多维标签 v2: %s（%d 张，%s 模式可发 %d 张）"
                        % (_st["total"], ", ".join(_st["sources"]) or "无",
                           "开" if _st.get("v2_enabled") else "关",
                           _st.get("v2_tagged", 0), _st.get("v2_mode"),
                           _st.get("v2_sendable", 0))
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"初始化表情包库失败，表情请求走原路径: {e}")
                    sticker_library = None
                    sticker_recall = None
            self.handler = MessageHandler(
                wechat=self.wechat,
                ai_engine=self.ai,
                style_manager=self.style,
                security=self.security,
                max_queue_size=sec_cfg.get("max_queue_size", 100),
                on_event=self._on_event,
                context_file=wx_cfg.get(
                    "context_file",
                    "./data/conversation_contexts.json",
                ),
                context_history=wx_cfg.get("context_history", 50),
                ai_context_messages=wx_cfg.get("ai_context_messages", 12),
                trigger_keywords=wx_cfg.get(
                    "trigger_keywords",
                    ["大肥鲸", "deepseek", "鲸鱼娘", "吃大米", "token不够用了"],
                ),
                fast_reply_timeout=ai_cfg.get("fast_reply_timeout", 1.2),
                fast_reply_fallback=ai_cfg.get("fast_reply_fallback", True),
                answered_cache_file=wx_cfg.get(
                    "answered_cache_file",
                    "./data/answered_questions.json",
                ),
                dedupe_window=wx_cfg.get("dedupe_window", 21600),
                computer_issue_timeout=wx_cfg.get(
                    "computer_issue_timeout",
                    10,
                ),
                computer_max_tokens=wx_cfg.get("computer_max_tokens", 600),
                reply_sticker_enabled=wx_cfg.get("sticker", {}).get(
                    "enabled",
                    False,
                ),
                reply_sticker_interval=wx_cfg.get("sticker", {}).get(
                    "reply_interval",
                    3,
                ),
                reply_sticker_count=wx_cfg.get("sticker", {}).get(
                    "reply_sticker_count",
                    3,
                ),
                proactive_enabled=proactive_cfg.get("enabled", True),
                proactive_interval_hours=proactive_cfg.get(
                    "interval_hours",
                    2,
                ),
                proactive_start_hour=proactive_cfg.get("start_hour", 8),
                proactive_end_hour=proactive_cfg.get("end_hour", 20),
                proactive_groups=(
                    proactive_cfg.get("groups")
                    or wx_cfg.get("target_groups", [])
                ),
                proactive_timeout=proactive_cfg.get("timeout", 8.0),
                idle_greet_enabled=idle_cfg.get("enabled", True),
                idle_greet_minutes=idle_cfg.get("idle_minutes", 30),
                idle_greet_cooldown_minutes=idle_cfg.get(
                    "cooldown_minutes",
                    60,
                ),
                idle_greet_check_interval=idle_cfg.get("check_interval", 60),
                idle_greet_min_messages=idle_cfg.get("min_messages", 0),
                sensitivity=wx_cfg.get("sensitivity", "normal"),
                # 0 = 关闭「连发护栏」（不再跳过群友消息），改由攒批控制节奏
                consecutive_reply_guard=wx_cfg.get(
                    "consecutive_reply_guard", 0.0
                ),
                # 攒批回复：最后一条后静默多久一起回 / 从第一条起最多等多久
                reply_batch_quiet=wx_cfg.get("reply_batch_quiet", 2.5),
                reply_batch_max=wx_cfg.get("reply_batch_max", 12.0),
                # 战斗话术注入（群聊被怼时的犀利回应风格）
                # 2026-10-16（P0-1）：默认改为 **关闭**。改为由下面的 style_guide
                # 用「风格描述」表达同样的"嘴硬但有分寸"，不再注入怼人对白范例。
                savage_enabled=savage_cfg.get("enabled", False),
                savage_file=savage_cfg.get(
                    "file", "./风格语料/吐槽库.json"
                ),
                savage_sample_count=savage_cfg.get("sample_count", 5),
                savage_intensity=savage_cfg.get("intensity", "sharp"),
                # 2026-10-16（P0-1/P0-9）新增：
                #   style_guide    用描述而非对白来表达说话风格
                #   output_guard   发送前的正则/词表护栏（四态收敛）
                style_guide_enabled=style_guide_enabled,
                output_guard_enabled=output_guard_enabled,
                output_guard_max_chars=output_guard_max_chars,
                # 2026-10-16（P0-3）：@ 的是别人就不抢话
                address_check_enabled=bool(wx_cfg.get("address_check", True)),
                # 2026-10-16（P1-2 最小版）：非点名群消息的参与概率。
                # 现在只在 jevos 判定不可用（未启用/熔断/失败）时作为降级手段。
                participation_probability=(
                    float(participation_cfg.get("probability", 0.0))
                    if participation_cfg.get("enabled", False) else 0.0
                ),
                # 2026-10-16：本地 jevos 判定器（None = 不启用）
                jev_judge=jev_judge,
                # 2026-10-16：本地表情包库（None = 不启用，表情请求退回收藏表情）
                sticker_library=sticker_library,
                sticker_recall=sticker_recall,
                # 2026-10-19：讲笑话的「讲完配图」开关。
                # joke_service 本身由 **self._voice_handler_kwargs(wx_cfg) 统一传入，
                # 这里不能再显式传一次，否则会 "got multiple values"。
                joke_sticker_after=getattr(self, "joke_sticker_after", True),
                # 2026-10-19：梗话库（短句梗图台词，按场景注入到正常对话）
                meme_lines_enabled=bool(
                    (wx_cfg.get("meme_lines") or {}).get("enabled", True)
                ),
                meme_lines_file=str(
                    (wx_cfg.get("meme_lines") or {}).get(
                        "file", "./风格语料/梗话库.json"
                    )
                ),
                meme_lines_count=int(
                    (wx_cfg.get("meme_lines") or {}).get("count", 2)
                ),
                poke_cooldown_seconds=wx_cfg.get("poke_cooldown", 3.0),
                group_reply_limit=wx_cfg.get("group_reply_limit", 6),
                group_reply_window=wx_cfg.get("group_reply_window", 60),
                max_message_age=wx_cfg.get("max_message_age", 600),
                user_burst_cooldown=wx_cfg.get(
                    "user_burst_cooldown",
                    3.0,
                ),
                user_reply_timeout=wx_cfg.get(
                    "user_reply_timeout",
                    20.0,
                ),
                image_search_enabled=image_search_cfg.get(
                    "enabled",
                    True,
                ),
                image_search_timeout=image_search_cfg.get(
                    "timeout",
                    3.0,
                ),
                image_cache_dir=image_search_cfg.get(
                    "cache_dir",
                    "./data/images",
                ),
                **self._voice_handler_kwargs(wx_cfg),
            )

            qq_cfg = self.config.get("qq", {}) or {}
            self._qq_enabled = bool(qq_cfg.get("enabled", False))
            if self._qq_enabled:
                self._setup_qq(qq_cfg, sec_cfg, ai_cfg, wx_cfg)
            else:
                logger.info("QQ 端未启用（qq.enabled=false），仅运行微信端")

            # 企业微信端初始化
            wecom_cfg = self.config.get("wecom", {}) or {}
            self._wecom_enabled = bool(wecom_cfg.get("enabled", False))
            if self._wecom_enabled:
                self._setup_wecom(wecom_cfg, sec_cfg, ai_cfg, wx_cfg)
            else:
                logger.info("企业微信端未启用（wecom.enabled=false）")

            init_routes(self)
            logger.info("所有模块初始化完成")
            return True

        except Exception as e:
            logger.error(f"初始化失败: {e}")
            return False

    def _setup_qq(
        self,
        qq_cfg: dict,
        sec_cfg: dict,
        ai_cfg: dict,
        wx_cfg: dict,
    ) -> None:
        """构建 QQ 端：独立桥接、独立安全策略、独立消息处理管线。

        与微信端共享 AI 引擎和人设，但队列、上下文、去重缓存、
        限流熔断全部独立，确保两端互不干扰。
        """
        # QQ 端使用独立的限流与熔断，避免一端异常拖垮另一端
        self.qq_security = SecurityManager(
            rate_limit=qq_cfg.get("rate_limit", sec_cfg.get("rate_limit", 20)),
            circuit_breaker_threshold=qq_cfg.get(
                "circuit_breaker_threshold",
                sec_cfg.get("circuit_breaker_threshold", 5),
            ),
            circuit_breaker_timeout=qq_cfg.get(
                "circuit_breaker_timeout",
                sec_cfg.get("circuit_breaker_timeout", 5),
            ),
        )

        client_secret = self.config.get_decrypted("qq.client_secret_encrypted")
        if not client_secret:
            client_secret = str(qq_cfg.get("client_secret", "") or "")
            if client_secret:
                self.config.set(
                    "qq.client_secret_encrypted",
                    self.config.encrypt_value(client_secret),
                )
                self.config.set("qq.client_secret", "")
                self.config.save()

        # QQ 走纯 HTTP 接口，没有微信那种窗口风控，
        # 把发送间隔和消息有效期压下来即可显著提升回复手感
        qq_eff = qq_cfg.get("efficiency", {}) or {}
        eff_on = bool(qq_eff.get("enabled", True))
        qq_send_interval = float(
            qq_eff.get("send_interval", qq_cfg.get("send_interval", 1.2))
            if eff_on
            else qq_cfg.get("send_interval", 1.2)
        )
        qq_max_age = float(
            qq_eff.get("max_message_age", qq_cfg.get("max_message_age", 600))
            if eff_on
            else qq_cfg.get("max_message_age", 600)
        )
        qq_fast_timeout = float(
            qq_eff.get("fast_reply_timeout", ai_cfg.get("fast_reply_timeout", 1.2))
            if eff_on
            else ai_cfg.get("fast_reply_timeout", 1.2)
        )

        self.qq = QQBridge(
            app_id=qq_cfg.get("app_id", ""),
            client_secret=client_secret,
            sandbox=qq_cfg.get("sandbox", False),
            target_groups=qq_cfg.get("target_groups", []) or [],
            on_message=self._on_qq_message,
            reply_delay=qq_cfg.get("reply_delay", 0.0),
            backend=qq_cfg.get("backend", "official"),
            intents=qq_cfg.get("intents", 0) or 0,
            group_aliases=qq_cfg.get("group_aliases", {}) or {},
            listen_private=qq_cfg.get("listen_private", True),
            send_interval=qq_send_interval,
        )

        qq_proactive = qq_cfg.get("proactive", {}) or {}
        qq_idle = qq_proactive.get("idle", {}) or {}
        qq_image = qq_cfg.get("image_search", {}) or {}
        self.qq_handler = MessageHandler(
            wechat=self.qq,
            ai_engine=self.ai,
            style_manager=self.style,
            security=self.qq_security,
            max_queue_size=sec_cfg.get("max_queue_size", 100),
            on_event=self._make_event_callback("qq"),
            # 独立的数据文件，避免与微信端互相覆盖
            context_file=qq_cfg.get(
                "context_file",
                "./data/qq_conversation_contexts.json",
            ),
            context_history=qq_cfg.get(
                "context_history",
                wx_cfg.get("context_history", 50),
            ),
            ai_context_messages=qq_cfg.get(
                "ai_context_messages",
                wx_cfg.get("ai_context_messages", 12),
            ),
            trigger_keywords=qq_cfg.get("trigger_keywords")
            or wx_cfg.get("trigger_keywords", []),
            fast_reply_timeout=qq_fast_timeout,
            fast_reply_fallback=ai_cfg.get("fast_reply_fallback", True),
            answered_cache_file=qq_cfg.get(
                "answered_cache_file",
                "./data/qq_answered_questions.json",
            ),
            dedupe_window=qq_cfg.get("dedupe_window", 21600),
            computer_issue_timeout=qq_cfg.get(
                "computer_issue_timeout",
                wx_cfg.get("computer_issue_timeout", 8),
            ),
            computer_max_tokens=qq_cfg.get(
                "computer_max_tokens",
                wx_cfg.get("computer_max_tokens", 600),
            ),
            # QQ 官方 API 没有收藏表情，关闭表情回复
            reply_sticker_enabled=False,
            proactive_enabled=qq_proactive.get("enabled", False),
            proactive_interval_hours=qq_proactive.get("interval_hours", 2),
            proactive_start_hour=qq_proactive.get("start_hour", 8),
            proactive_end_hour=qq_proactive.get("end_hour", 20),
            proactive_groups=qq_proactive.get("groups") or [],
            proactive_timeout=qq_proactive.get("timeout", 8.0),
            idle_greet_enabled=qq_idle.get("enabled", False),
            idle_greet_minutes=qq_idle.get("idle_minutes", 30),
            idle_greet_cooldown_minutes=qq_idle.get("cooldown_minutes", 60),
            idle_greet_check_interval=qq_idle.get("check_interval", 60),
            idle_greet_min_messages=qq_idle.get("min_messages", 0),
            sensitivity=qq_cfg.get("sensitivity", "normal"),
            poke_cooldown_seconds=qq_cfg.get("poke_cooldown", 3.0),
            group_reply_limit=qq_cfg.get(
                "group_reply_limit",
                wx_cfg.get("group_reply_limit", 6),
            ),
            group_reply_window=qq_cfg.get(
                "group_reply_window",
                wx_cfg.get("group_reply_window", 60),
            ),
            max_message_age=qq_max_age,
            user_burst_cooldown=qq_cfg.get(
                "user_burst_cooldown",
                wx_cfg.get("user_burst_cooldown", 3.0),
            ),
            user_reply_timeout=qq_cfg.get(
                "user_reply_timeout",
                wx_cfg.get("user_reply_timeout", 20.0),
            ),
            image_search_enabled=qq_image.get("enabled", True),
            image_search_timeout=qq_image.get("timeout", 3.0),
            image_cache_dir=qq_image.get("cache_dir", "./data/qq-images"),
            **self._voice_handler_kwargs(qq_cfg),
        )
        logger.info(
            f"QQ 官方机器人已启用（后端: {qq_cfg.get('backend', 'official')}，"
            f"沙箱: {bool(qq_cfg.get('sandbox', False))}）"
        )

    def _setup_wecom(
        self,
        wecom_cfg: dict,
        sec_cfg: dict,
        ai_cfg: dict,
        wx_cfg: dict,
    ) -> None:
        """构建企业微信端：独立桥接、独立安全策略、独立消息处理管线。"""
        self.wecom_security = SecurityManager(
            rate_limit=wecom_cfg.get("rate_limit", sec_cfg.get("rate_limit", 20)),
            circuit_breaker_threshold=wecom_cfg.get(
                "circuit_breaker_threshold",
                sec_cfg.get("circuit_breaker_threshold", 5),
            ),
            circuit_breaker_timeout=wecom_cfg.get(
                "circuit_breaker_timeout",
                sec_cfg.get("circuit_breaker_timeout", 5),
            ),
        )

        # 企业微信secret直接读取明文（不加密，因为是本地配置）
        corp_secret = str(wecom_cfg.get("corpsecret", "") or "")
        if not corp_secret:
            corp_secret = self.config.get_decrypted("wecom.corpsecret_encrypted")

        wecom_fast_timeout = float(ai_cfg.get("fast_reply_timeout", 1.0))

        self.wecom = WxWorkBridge(
            bot_id=wecom_cfg.get("bot_id", ""),
            secret=corp_secret,
            agentid=wecom_cfg.get("agentid", ""),
            token=wecom_cfg.get("token", ""),
            aes_key=wecom_cfg.get("aes_key", ""),
            enabled=True,
            on_message=self._on_wecom_message,
        )

        self.wecom_handler = MessageHandler(
            wechat=self.wecom,
            ai_engine=self.ai,
            style_manager=self.style,
            security=self.wecom_security,
            max_queue_size=sec_cfg.get("max_queue_size", 100),
            on_event=self._make_event_callback("wecom"),
            context_file=wecom_cfg.get(
                "context_file",
                "./data/wecom_conversation_contexts.json",
            ),
            context_history=wecom_cfg.get("context_history", 30),
            ai_context_messages=wecom_cfg.get("ai_context_messages", 8),
            trigger_keywords=wecom_cfg.get("trigger_keywords")
            or ["@鲸鱼娘", "鲸鱼娘", "deepseek"],
            fast_reply_timeout=wecom_fast_timeout,
            fast_reply_fallback=ai_cfg.get("fast_reply_fallback", True),
            answered_cache_file=wecom_cfg.get(
                "answered_cache_file",
                "./data/wecom_answered_questions.json",
            ),
            dedupe_window=wecom_cfg.get("dedupe_window", 21600),
            computer_issue_timeout=wecom_cfg.get(
                "computer_issue_timeout",
                wx_cfg.get("computer_issue_timeout", 8),
            ),
            computer_max_tokens=wecom_cfg.get(
                "computer_max_tokens",
                wx_cfg.get("computer_max_tokens", 400),
            ),
            reply_sticker_enabled=False,
            image_search_enabled=False,
            song_service=getattr(self, "song", None),
            voice_enabled=getattr(self, "voice_enabled", True),
            quick_replies_enabled=getattr(self, "voice_cfg", {}).get(
                "quick_enabled", True
            ),
            song_max_duration=getattr(
                self, "voice_cfg", {}
            ).get("songs_max_duration", 55.0),
            asr_service=getattr(self, "asr", None),
        )

        self._apply_voice_handler_kwargs(self.wecom_handler, wecom_cfg)
        logger.info(
            f"企业微信机器人已初始化（agentid: {wecom_cfg.get('agentid', '')}）"
        )

    def _apply_voice_handler_kwargs(self, handler, cfg):
        """应用语音配置到 MessageHandler。"""
        if handler and getattr(self, "voice_enabled", True):
            handler.apply_voice_config(
                voice_enabled=self.voice_enabled,
                song_service=getattr(self, "song", None),
                quick_replies_enabled=getattr(self, "voice_cfg", {}).get("quick_enabled", True),
                divination_service=getattr(self, "divination", None),
            )

    async def _start_wecom(self) -> None:
        """启动企业微信端。"""
        if not self.wecom or not self.wecom_handler:
            logger.info("企业微信端未初始化，跳过启动")
            return

        if self._wecom_running and self.wecom.is_connected:
            logger.info("企业微信端已在运行，忽略重复启动请求")
            return

        try:
            if self.wecom.is_connected:
                try:
                    await asyncio.wait_for(self.wecom.disconnect(), timeout=10)
                except Exception as e:
                    logger.warning(f"清理残留企业微信连接失败: {e}")

            connected = await self.wecom.connect()
            if not connected:
                reason = self.wecom.last_error or "未知原因"
                logger.error(f"企业微信端连接失败: {reason}")
                self._wecom_running = False
                self._on_event(
                    "bot_start_failed",
                    {"platform": "wecom", "message": f"企业微信连接失败: {reason}"},
                    platform="wecom",
                )
                return

            loop = asyncio.get_event_loop()
            self.wecom.start_listening(loop)
            await self.wecom_handler.start()
            self._wecom_running = True
            self._start_wecom_watchdog()
            logger.info("企业微信端已启动")
            self._on_event("bot_running", {}, platform="wecom")
        except Exception as e:
            self._wecom_running = False
            logger.error(f"企业微信端启动失败: {e}")
            self._on_event(
                "bot_start_failed",
                {"platform": "wecom", "message": str(e)},
                platform="wecom",
            )

    async def _stop_wecom(self) -> None:
        """停止企业微信端。"""
        if not self._wecom_running:
            return
        try:
            if self.wecom_handler:
                await self.wecom_handler.stop()
        except Exception as e:
            logger.warning(f"停止企业微信处理管线失败: {e}")
        try:
            if self.wecom:
                await asyncio.wait_for(self.wecom.close(), timeout=10)
        except Exception as e:
            logger.warning(f"关闭企业微信连接失败: {e}")
        await self._stop_wecom_watchdog()
        self._wecom_running = False
        logger.info("企业微信端已停止")

    async def _start_wecom_watchdog(self) -> None:
        """启动企业微信端连接看门狗。"""
        if self._wecom_watchdog_task and not self._wecom_watchdog_task.done():
            return
        self._wecom_watchdog_task = asyncio.create_task(
            self._wecom_watchdog(), name="wecom-watchdog"
        )

    async def _stop_wecom_watchdog(self) -> None:
        task = self._wecom_watchdog_task
        self._wecom_watchdog_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _wecom_watchdog(self) -> None:
        """企业微信端连接看门狗：断线后自动重连。"""
        try:
            while self._wecom_running:
                await asyncio.sleep(30)
                if not self._wecom_running or not self.wecom:
                    break
                if not self.wecom.is_connected:
                    logger.warning("企业微信连接断开，尝试重连...")
                    await self._start_wecom()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"企业微信看门狗异常: {e}")

    def set_async_bridge(self, bridge: AsyncBridge):
        self._async_bridge = bridge

    async def _on_message(self, message) -> None:
        # 2026-10-16：机器人附着微信的瞬间，wechatauto 会把聊天窗口里原本就
        # 存在的历史消息整批回调进来（实测约 200 条）。这类消息按要求只写进
        # 消息库留档，不进入回复管线；只有「机器人打开之后」的新消息才回复。
        is_history = bool(getattr(message, "is_history", False))
        record = {
            "platform": "wechat",
            "direction": "in",
            "room": message.room_name,
            "sender": message.sender_name,
            "content": message.content,
        }
        if is_history:
            record["history"] = True
        self._add_recent_message(record)
        self._archive_message(record)
        if is_history:
            # 2026-10-20：只归档不回复，但把「睡着时收到的」交给补消息服务，
            # 回放窗口结束后由它每个会话回一句。
            service = getattr(self, "catchup", None)
            if service is not None:
                try:
                    service.note(message)
                except Exception as exc:
                    logger.debug(f"[开机补消息] 收录失败: {exc}")
            return
        await self.handler.enqueue(message)

    async def _on_qq_message(self, message) -> None:
        """QQ 端消息入口：与微信端完全并行，互不阻塞。"""
        if not self.qq_handler:
            return
        self._add_recent_message({
            "platform": "qq",
            "direction": "in",
            "room": message.room_name,
            "sender": message.sender_name,
            "content": message.content,
        })
        await self.qq_handler.enqueue(message)

    async def _on_wecom_message(self, message) -> None:
        """企业微信端消息入口。"""
        if not self.wecom_handler:
            return
        self._add_recent_message({
            "platform": "wecom",
            "direction": "in",
            "room": message.room_name,
            "sender": message.sender_name,
            "content": message.content,
        })
        await self.wecom_handler.enqueue(message)

    def _resolve_platforms(self, platform: str | None) -> list[str]:
        """把平台参数展开为需要操作的平台列表。"""
        name = str(platform or "").strip().lower()
        if name in {"wechat", "weixin", "wx"}:
            return ["wechat"]
        if name in {"qq", "qqbot"}:
            return ["qq"] if self._qq_enabled else []
        if name in {"wecom", "wework", "企业微信"}:
            return ["wecom"] if self._wecom_enabled else []
        targets = ["wechat"]
        if self._qq_enabled:
            targets.append("qq")
        if self._wecom_enabled:
            targets.append("wecom")
        return targets

    async def apply_target_groups(
        self,
        groups: list[str],
        platform: str = "wechat",
    ) -> dict:
        """通过主事件循环把 WebUI 的群聊变更同步到对应平台的监听器。"""
        if platform == "qq":
            if not self.qq:
                return {
                    "success": False,
                    "live": False,
                    "message": "QQ 桥接器未初始化",
                }
            result = await self.qq.set_target_groups(groups)
            qq_proactive = self.config.get("qq.proactive", {}) or {}
            if self.qq_handler and not (qq_proactive.get("groups") or []):
                self.qq_handler.set_proactive_groups(
                    groups,
                    start_if_needed=bool(self._qq_running),
                )
            return result

        if platform == "wecom":
            if not self.wecom:
                return {
                    "success": False,
                    "live": False,
                    "message": "企业微信桥接器未初始化",
                }
            result = await self.wecom.set_target_groups(groups)
            return result

        if not self.wechat:
            return {
                "success": False,
                "live": False,
                "message": "微信桥接器未初始化",
            }

        result = await self.wechat.set_target_groups(groups)
        proactive_cfg = self.config.get("wechat.proactive", {}) or {}
        if self.handler and not (proactive_cfg.get("groups") or []):
            self.handler.set_proactive_groups(
                groups,
                start_if_needed=bool(self._running),
            )
        return result

    def _on_ai_usage(self, info: dict) -> None:
        """token 用量变化 → 实时推送 Web（不进事件日志，避免每条 AI 调用刷屏）。"""
        payload = dict(info or {})
        payload.setdefault("platform", "wechat")
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(broadcast("token_usage", payload))
            )
        except RuntimeError:
            pass

    def _on_event(self, event: str, data: dict, platform: str = "wechat") -> None:
        """把管线事件广播给 Web / GUI，并打上平台标记避免双端串台。"""
        payload = dict(data or {})
        payload.setdefault("platform", platform)
        self._add_log("INFO", f"[{platform}] {event}: {payload}")
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(broadcast(event, payload))
            )
        except RuntimeError:
            pass

        # 通过信号安全地更新 GUI（跨线程）
        if self.window:
            self.window._gui_event.emit(event, payload)

    def _make_event_callback(self, platform: str):
        """生成带平台标记的事件回调，供各平台独立管线使用。"""

        def _callback(event: str, data: dict) -> None:
            self._on_event(event, data, platform=platform)

        return _callback

    def _add_recent_message(self, msg: dict) -> None:
        record = dict(msg or {})
        record.setdefault("platform", "wechat")
        self._recent_messages.append(record)
        if len(self._recent_messages) > self._max_recent:
            self._recent_messages = self._recent_messages[-self._max_recent // 2:]

    # 入站消息磁盘归档的单文件上限：超过后轮转成 .1，避免无限增长
    ARCHIVE_MAX_BYTES = 20 * 1024 * 1024

    def _archive_message(self, record: dict) -> None:
        """把入站消息追加写入 data/message_archive.jsonl。

        2026-10-16 新增：内存里的 _recent_messages 重启即失、且只保留最近若干条，
        撑不起「历史消息留作记录」的用途，因此单独落一份磁盘归档。启动时回放
        的历史消息同样会写进来（带 "history": true 标记），只是不参与回复。
        写入失败只告警，绝不影响消息处理主链路。
        """
        try:
            archive_dir = Path("data")
            archive_dir.mkdir(parents=True, exist_ok=True)
            path = archive_dir / "message_archive.jsonl"
            if path.exists() and path.stat().st_size > self.ARCHIVE_MAX_BYTES:
                rotated = archive_dir / "message_archive.jsonl.1"
                try:
                    if rotated.exists():
                        rotated.unlink()
                    path.replace(rotated)
                except OSError:
                    pass
            payload = dict(record)
            payload["time"] = datetime.now().isoformat(timespec="seconds")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"消息归档写入失败: {e}")

    def _add_log(self, level: str, message: str) -> None:
        from datetime import datetime
        self._logs.append({"level": level, "message": message, "time": datetime.now().isoformat()})
        if len(self._logs) > self._max_recent:
            self._logs = self._logs[-self._max_recent // 2:]
        # 通过信号安全地更新日志（跨线程）
        if self.window:
            self.window._log_event.emit(level, message)

    def get_recent_messages(self, limit: int = 50, platform: str = "") -> list:
        """按平台读取最近消息；platform 为空时返回全部。"""
        items = self._recent_messages
        if platform:
            items = [
                item for item in items
                if str(item.get("platform", "wechat")) == platform
            ]
        return items[-limit:]

    def get_logs(self, limit: int = 100) -> list:
        return self._logs[-limit:]

    async def start(self, platform: str | None = None) -> None:
        """启动机器人；platform 为空时启动所有已启用的平台。

        各平台独立启停：一端失败不会影响另一端。
        """
        if getattr(self, "asr", None) is not None:
            await self.asr.start()
        for name in self._resolve_platforms(platform):
            try:
                if name == "wechat":
                    await self._start_wechat()
                elif name == "qq":
                    await self._start_qq()
                else:
                    await self._start_wecom()
            except Exception as exc:
                logger.error(f"{name} 端启动异常: {exc}")
                log_exception(f"main.start.{name}", exc=exc)

        # 任一平台起来后就预热热门歌曲，等用户点歌时几乎瞬间可发
        self._start_song_prewarm()

    def _start_song_prewarm(self) -> None:
        song = getattr(self, "song", None)
        voice_cfg = getattr(self, "voice_cfg", {}) or {}
        if not song or not voice_cfg.get("prewarm", True):
            return
        if not (self._running or self._qq_running or self._wecom_running):
            return
        try:
            song.start_prewarm(int(voice_cfg.get("prewarm_count", 6)))
        except Exception as exc:
            logger.warning(f"点歌预热启动失败: {exc}")

    async def _start_wechat(self) -> None:
        handler_running = bool(self.handler and self.handler.is_processing)
        if self._running and handler_running:
            logger.info("微信端已在运行，忽略重复的启动请求")
            return
        if handler_running:
            logger.warning("检测到上次启动残留的处理管线，先停止再重新启动")
            try:
                await self.handler.stop()
            except Exception as e:
                logger.warning(f"停止残留处理管线失败: {e}")

        if self.window:
            self.window._gui_event.emit("bot_starting", {"platform": "wechat"})

        try:
            if self.wechat and self.wechat.is_connected:
                logger.warning("检测到残留的微信连接，先断开再重新连接")
                try:
                    await asyncio.wait_for(self.wechat.disconnect(), timeout=15)
                except Exception as e:
                    logger.warning(f"清理残留微信连接失败: {e}")

            loop = asyncio.get_event_loop()
            connected = await self.wechat.connect()
            if not connected:
                logger.error("微信窗口不可用")
                self._running = False
                if self.window:
                    self.window._gui_event.emit(
                        "bot_start_failed",
                        {
                            "platform": "wechat",
                            "message": (
                                "微信窗口不可用，请先手动登录微信，"
                                "再点击启动机器人"
                            ),
                        },
                    )
                return
            self.wechat.start_listening(loop)
            await self.handler.start()
            self._running = True
            # 2026-10-20：等历史回放窗口结束，把睡着时收到的消息补一句
            self._schedule_catchup()
            self._start_sticker_scheduler()
            self._start_connection_watchdog()
            logger.info("微信端已启动")
            if self.window:
                self.window._gui_event.emit("bot_running", {"platform": "wechat"})
        except Exception as e:
            self._running = False
            logger.error(f"微信端启动失败: {e}")
            if self.window:
                self.window._gui_event.emit(
                    "bot_start_failed",
                    {"platform": "wechat", "message": str(e)},
                )

    async def _start_qq(self) -> None:
        """启动 QQ 端（官方机器人 API）。"""
        if not self.qq or not self.qq_handler:
            logger.info("QQ 端未初始化，跳过启动")
            return

        handler_running = bool(self.qq_handler.is_processing)
        if self._qq_running and handler_running:
            logger.info("QQ 端已在运行，忽略重复的启动请求")
            return
        if handler_running:
            try:
                await self.qq_handler.stop()
            except Exception as e:
                logger.warning(f"停止残留的 QQ 处理管线失败: {e}")

        if self.window:
            self.window._gui_event.emit("bot_starting", {"platform": "qq"})

        try:
            if self.qq.is_connected:
                try:
                    await asyncio.wait_for(self.qq.disconnect(), timeout=15)
                except Exception as e:
                    logger.warning(f"清理残留 QQ 连接失败: {e}")

            loop = asyncio.get_event_loop()
            connected = await self.qq.connect()
            if not connected:
                reason = self.qq.last_error or self.qq._unavailable_reason() or "未知原因"
                logger.error(f"QQ 端连接失败: {reason}")
                self._qq_running = False
                self._on_event(
                    "bot_start_failed",
                    {"platform": "qq", "message": f"QQ 连接失败: {reason}"},
                    platform="qq",
                )
                return

            self.qq.start_listening(loop)
            await self.qq_handler.start()
            self._qq_running = True
            self._start_qq_watchdog()
            logger.info("QQ 端已启动")
            self._on_event("bot_running", {}, platform="qq")
            if self.window:
                self.window._gui_event.emit("bot_running", {"platform": "qq"})
        except Exception as e:
            self._qq_running = False
            logger.error(f"QQ 端启动失败: {e}")
            log_exception("main._start_qq", exc=e)
            self._on_event(
                "bot_start_failed",
                {"platform": "qq", "message": str(e)},
                platform="qq",
            )
            if self.window:
                self.window._gui_event.emit(
                    "bot_start_failed",
                    {"platform": "qq", "message": str(e)},
                )

    async def stop(self, platform: str | None = None) -> None:
        """停止机器人；platform 为空时停止所有平台。"""
        targets = self._resolve_platforms(platform)
        for name in targets:
            try:
                if name == "wechat":
                    await self._stop_wechat()
                elif name == "qq":
                    await self._stop_qq()
                else:
                    await self._stop_wecom()
            except Exception as exc:
                logger.error(f"{name} 端停止异常: {exc}")

        # 两端都停了就顺带结束点歌预热任务
        if not (self._running or self._qq_running or self._wecom_running):
            song = getattr(self, "song", None)
            if song is not None:
                try:
                    await song.stop()
                except Exception as exc:
                    logger.warning(f"停止点歌预热任务失败: {exc}")
            asr = getattr(self, "asr", None)
            if asr is not None:
                try:
                    await asr.close()
                except Exception as exc:
                    logger.warning(f"停止语音识别任务失败: {exc}")

    async def _stop_wechat(self) -> None:
        if not self._running and not (
            self.handler and self.handler.is_processing
        ):
            logger.info("微信端当前未运行，忽略停止请求")
            return
        self._running = False
        if self.window:
            self.window._gui_event.emit("bot_stopping", {"platform": "wechat"})
        try:
            await self._stop_connection_watchdog()
        except Exception as e:
            logger.warning(f"停止微信连接看门狗失败: {e}")
        try:
            await self._stop_sticker_scheduler()
        except Exception as e:
            logger.warning(f"停止收藏表情任务失败: {e}")
        try:
            await asyncio.wait_for(self.handler.stop(), timeout=20)
        except asyncio.TimeoutError:
            logger.error("停止消息处理管线超时")
        except Exception as e:
            logger.warning(f"停止消息处理管线失败: {e}")
        try:
            await asyncio.wait_for(self.wechat.disconnect(), timeout=20)
        except asyncio.TimeoutError:
            logger.error("断开微信连接超时")
        except Exception as e:
            logger.warning(f"断开微信连接失败: {e}")
        logger.info("微信端已停止")
        if self.window:
            self.window._gui_event.emit("bot_stopped", {"platform": "wechat"})

    async def _stop_qq(self) -> None:
        """停止 QQ 端，不影响微信端运行。"""
        if not self._qq_running and not (
            self.qq_handler and self.qq_handler.is_processing
        ):
            logger.info("QQ 端当前未运行，忽略停止请求")
            return
        self._qq_running = False
        if self.window:
            self.window._gui_event.emit("bot_stopping", {"platform": "qq"})
        try:
            await self._stop_qq_watchdog()
        except Exception as e:
            logger.warning(f"停止 QQ 看门狗失败: {e}")
        try:
            await asyncio.wait_for(self.qq_handler.stop(), timeout=20)
        except asyncio.TimeoutError:
            logger.error("停止 QQ 消息处理管线超时")
        except Exception as e:
            logger.warning(f"停止 QQ 消息处理管线失败: {e}")
        try:
            await asyncio.wait_for(self.qq.disconnect(), timeout=20)
        except asyncio.TimeoutError:
            logger.error("断开 QQ 连接超时")
        except Exception as e:
            logger.warning(f"断开 QQ 连接失败: {e}")
        logger.info("QQ 端已停止")
        self._on_event("bot_stopped", {}, platform="qq")
        if self.window:
            self.window._gui_event.emit("bot_stopped", {"platform": "qq"})

    def _get_reconnect_lock(self) -> asyncio.Lock:
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()
        return self._reconnect_lock

    def _start_connection_watchdog(self) -> None:
        if (
            self._connection_watchdog_task
            and not self._connection_watchdog_task.done()
        ):
            return
        self._connection_watchdog_task = asyncio.create_task(
            self._connection_watchdog(),
            name="wechat-connection-watchdog",
        )

    async def _stop_connection_watchdog(self) -> None:
        task = self._connection_watchdog_task
        self._connection_watchdog_task = None
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _connection_watchdog(self) -> None:
        """Recover when WeChat restarts or a UIA send stops returning."""
        while self._running:
            await asyncio.sleep(3)
            if not self._running or not self.wechat:
                break
            try:
                if self.handler and not self.handler.is_processing:
                    logger.warning("检测到消息处理管线已退出，正在自动恢复")
                    await self.handler.start()
                    logger.info("消息处理管线已自动恢复")
                    continue
                if self.wechat.window_process_changed():
                    recovered = await self._recover_wechat_connection(
                        "微信主窗口已重启"
                    )
                    if not recovered:
                        await asyncio.sleep(7)
                elif self.wechat.is_sender_stuck(40):
                    # 阈值与 WeChatBridge.SEND_TIMEOUT_SECONDS(25s) 对齐。
                    # 原先 5 秒就断连重连，但实测发送只是慢（UIA 优先 + OCR 兜底），
                    # 重连会把在途的那条回复变成迟到发出或直接漏发。
                    recovered = await self._recover_wechat_connection(
                        "微信发送线程超过 40 秒未返回"
                    )
                    if not recovered:
                        await asyncio.sleep(7)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"微信连接看门狗异常: {e}")
                await asyncio.sleep(3)

    async def _recover_wechat_connection(self, reason: str) -> bool:
        lock = self._get_reconnect_lock()
        if lock.locked():
            return False

        async with lock:
            if not self._running or not self.wechat:
                return False
            if (
                self.wechat.is_connected
                and not self.wechat.window_process_changed()
                # 与 WeChatBridge.SEND_TIMEOUT_SECONDS(25s) 对齐：
                # 原先 5 秒就判定「发送线程卡死」并断连重连，但实测发送只是慢，
                # 重连不但没救回来，反而让那条回复迟到发出/漏发。
                and not self.wechat.is_sender_stuck(40)
            ):
                return False

            logger.warning(f"{reason}，正在自动重连微信...")
            try:
                recovered = await asyncio.wait_for(
                    self.wechat.recover_connection(),
                    timeout=50,
                )
            except asyncio.TimeoutError:
                logger.error("微信自动重连超时，稍后继续重试")
                return False
            except Exception as e:
                logger.error(f"微信自动重连失败: {e}")
                return False

            if recovered:
                logger.info("微信连接已自动恢复")
                return True

            logger.error("微信自动恢复失败，保持重试")
            return False

    def _start_qq_watchdog(self) -> None:
        if self._qq_watchdog_task and not self._qq_watchdog_task.done():
            return
        self._qq_watchdog_task = asyncio.create_task(
            self._qq_watchdog(),
            name="qq-connection-watchdog",
        )

    async def _stop_qq_watchdog(self) -> None:
        task = self._qq_watchdog_task
        self._qq_watchdog_task = None
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _qq_watchdog(self) -> None:
        """QQ 端看门狗：管线退出自动重启，连接断开自动重连。"""
        while self._qq_running:
            await asyncio.sleep(5)
            if not self._qq_running or not self.qq:
                break
            try:
                if self.qq_handler and not self.qq_handler.is_processing:
                    logger.warning("检测到 QQ 处理管线已退出，正在自动恢复")
                    await self.qq_handler.start()
                    logger.info("QQ 处理管线已自动恢复")
                    continue
                if (
                    getattr(self.qq, "backend", "official") != "simulate"
                    and not self.qq.is_connected
                ):
                    logger.warning("检测到 QQ 连接已断开，正在自动重连")
                    recovered = await self.qq.recover_connection()
                    if not recovered:
                        await asyncio.sleep(8)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"QQ 连接看门狗异常: {e}")
                await asyncio.sleep(5)

    def platform_status(self) -> dict:
        """聚合两个平台的运行状态，供 Web / GUI 展示。"""
        wechat_running = bool(self._running)
        qq_running = bool(self._qq_running)
        return {
            "wechat": {
                "enabled": True,
                "running": wechat_running,
                "bridge": self.wechat.status_info if self.wechat else {},
                "handler": self.handler.status if self.handler else {},
                "security": self.security.status if self.security else {},
            },
            "qq": {
                "enabled": bool(getattr(self, "_qq_enabled", False)),
                "running": qq_running,
                "bridge": self.qq.status_info if self.qq else {},
                "handler": self.qq_handler.status if self.qq_handler else {},
                "security": self.qq_security.status if self.qq_security else {},
            },
            "voice": self.voice_status(),
        }

    async def configure_qq(
        self,
        app_id: str,
        client_secret: str = "",
        sandbox: bool = False,
        backend: str = "official",
        target_groups: list[str] | None = None,
        reply_delay: float | None = None,
        enabled: bool = True,
    ) -> dict:
        """保存并热更新 QQ 配置；无需重启进程。"""
        app_id = str(app_id or "").strip()
        backend = str(backend or "official").strip().lower()
        if backend not in {"official", "simulate"}:
            backend = "official"
        if backend == "official" and not app_id:
            return {"success": False, "message": "请填写机器人 AppID"}

        secret = str(client_secret or "").strip()
        if secret:
            self.config.set(
                "qq.client_secret_encrypted",
                self.config.encrypt_value(secret),
            )
            self.config.set("qq.client_secret", "")
        else:
            secret = self.config.get_decrypted("qq.client_secret_encrypted")
            if not secret:
                secret = str(self.config.get("qq.client_secret", "") or "")

        if backend == "official" and not secret:
            return {"success": False, "message": "请填写机器人 AppSecret"}

        self.config.set("qq.enabled", bool(enabled))
        self.config.set("qq.app_id", app_id)
        self.config.set("qq.sandbox", bool(sandbox))
        self.config.set("qq.backend", backend)
        if target_groups is not None:
            groups = [str(g).strip() for g in target_groups if str(g).strip()]
            self.config.set("qq.target_groups", groups)
        if reply_delay is not None:
            self.config.set("qq.reply_delay", float(reply_delay))
        self.config.save()

        # 若 QQ 端正在运行，先停再按新配置重建
        was_running = self._qq_running
        if was_running:
            await self._stop_qq()

        sec_cfg = self.config.get("security", {}) or {}
        ai_cfg = self.config.get("ai", {}) or {}
        wx_cfg = self.config.get("wechat", {}) or {}
        qq_cfg = self.config.get("qq", {}) or {}
        self._qq_enabled = bool(qq_cfg.get("enabled", True))
        if self._qq_enabled:
            self._setup_qq(qq_cfg, sec_cfg, ai_cfg, wx_cfg)

        message = "QQ 配置已保存"
        if was_running and self._qq_enabled:
            await self._start_qq()
            message += "，并已按新配置重启 QQ 端"
        elif self._qq_enabled:
            message += "，点击「启动 QQ」即可生效"
        else:
            message += "，QQ 端已停用"
        logger.info(message)
        return {
            "success": True,
            "message": message,
            "enabled": self._qq_enabled,
            "running": bool(self._qq_running),
        }

    def _setup_gui(self) -> None:
        if not self.window or not self._async_bridge:
            return

        bridge = self._async_bridge

        self.window.start_requested.connect(lambda: bridge.call(self.start))
        self.window.stop_requested.connect(lambda: bridge.call(self.stop))
        self.window.style_change_requested.connect(self._change_style)
        self.window.api_test_requested.connect(lambda: bridge.call(self._test_api))
        self.window.config_save_requested.connect(self._save_config)

        if self.style:
            self.window.style_tab.update_styles(self.style.list_styles())
            self.window.style_tab.set_locked(self.style.is_locked, self.style.locked_name)

        self.window.config_tab.api_url.setText(self.config.get("ai.api_url", ""))
        self.window.config_tab.model.setText(self.config.get("ai.model", ""))
        self.window.config_tab.max_tokens.setValue(self.config.get("ai.max_tokens", 60))
        self.window.config_tab.temperature.setValue(self.config.get("ai.temperature", 0.7))
        self.window.config_tab.target_groups.setText(
            ", ".join(self.config.get("wechat.target_groups", []))
        )
        self.window.config_tab.reply_delay.setValue(
            self.config.get("wechat.reply_delay", 0.0)
        )
        self.window.config_tab.auto_reply.setChecked(
            self.config.get("wechat.auto_reply", True)
        )

        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._update_gui_status)
        self._status_timer.start(2000)

    def _change_style(self, name: str) -> None:
        if not self.style:
            return

        if self.style.is_locked:
            logger.warning(f"人设已永久锁定为 {self.style.locked_name}，拒绝切换")
            self.window.style_tab.update_styles(self.style.list_styles())
            self.window.style_tab.set_locked(True, self.style.locked_name)
            QMessageBox.information(
                self.window,
                "人设已锁定",
                "傲娇鲸鱼娘人设已永久锁定，不能更改为其他风格。",
            )
            return

        self.style.set_active(name)
        self.window.style_tab.update_styles(self.style.list_styles())

    def _start_sticker_scheduler(self) -> None:
        """启动收藏表情随机发送任务。"""
        if self._sticker_task and not self._sticker_task.done():
            return

        sticker_cfg = self.config.get("wechat.sticker", {})
        if not sticker_cfg.get("enabled", False):
            logger.info("收藏表情随机发送已关闭")
            return
        if not sticker_cfg.get("random_schedule", False):
            logger.info("收藏表情已改为每三次回复发送，随机定时发送已关闭")
            return
        if not self.wechat or not hasattr(self.wechat, "send_random_favorite_sticker"):
            logger.info("当前微信后端不支持随机发送收藏表情")
            return

        self._sticker_task = asyncio.create_task(self._sticker_loop())
        logger.info("收藏表情随机发送任务已启动")

    async def _stop_sticker_scheduler(self) -> None:
        """停止收藏表情随机发送任务。"""
        task = self._sticker_task
        self._sticker_task = None
        if not task:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("收藏表情随机发送任务已停止")

    async def _sticker_loop(self) -> None:
        """按随机间隔从微信收藏表情中发送一个表情。"""
        sticker_cfg = self.config.get("wechat.sticker", {})
        try:
            min_interval = max(30.0, float(sticker_cfg.get("min_interval", 600)))
            max_interval = max(min_interval, float(sticker_cfg.get("max_interval", 1200)))
        except (TypeError, ValueError):
            logger.warning("收藏表情间隔配置无效，使用默认 600 到 1200 秒")
            min_interval, max_interval = 600.0, 1200.0

        try:
            while self._running:
                delay = random.uniform(min_interval, max_interval)
                logger.debug(f"下次收藏表情发送等待 {delay:.0f} 秒")
                await asyncio.sleep(delay)

                try:
                    if (
                        not self._running
                        or not self.wechat
                        or not self.wechat.is_connected
                    ):
                        continue

                    groups = list(getattr(self.wechat, "target_groups", []) or [])
                    if not groups:
                        continue

                    room = random.choice(groups)
                    success = await self.wechat.send_random_favorite_sticker(room)
                    if success:
                        self._add_recent_message({
                            "direction": "out",
                            "room": room,
                            "sender": "",
                            "content": "[收藏表情]",
                        })
                        self._on_event("sticker_sent", {"room": room})
                    else:
                        logger.warning(f"随机发送收藏表情失败: {room}")
                        self._on_event("sticker_failed", {"room": room})
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"单次收藏表情发送异常，继续下一轮: {e}")
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    async def _test_api(self) -> None:
        if self.ai:
            success, msg = await self.ai.test_connection()
            if self.window:
                self.window.config_tab.test_btn.setText(f"{'成功' if success else '失败'}: {msg[:50]}")

    def _save_config(self) -> None:
        if self.config and self.window:
            cfg = self.window.config_tab
            self.config.set("ai.api_url", cfg.api_url.text())
            api_key = cfg.api_key.text().strip()
            if api_key:
                self.config.set("ai.api_key_encrypted", self.config.encrypt_value(api_key))
                self.config.set("ai.api_key", "")
                if self.ai:
                    self.ai.api_key = api_key
            self.config.set("ai.model", cfg.model.text())
            self.config.set("ai.max_tokens", cfg.max_tokens.value())
            self.config.set("ai.temperature", cfg.temperature.value())
            self.config.set("wechat.auto_reply", cfg.auto_reply.isChecked())
            self.config.set("wechat.reply_delay", cfg.reply_delay.value())
            groups = [g.strip() for g in cfg.target_groups.text().split(",") if g.strip()]
            self.config.set("wechat.target_groups", groups)
            self.config.save()

            if self.ai:
                self.ai.api_url = cfg.api_url.text()
                self.ai.model = cfg.model.text()
                self.ai.max_tokens = cfg.max_tokens.value()
                self.ai.temperature = cfg.temperature.value()
                self.ai.api_style = self.ai._resolve_api_style(
                    self.config.get("ai.api_url", ""),
                    self.config.get("ai.api_style", "auto"),
                )
                self.ai.api_url = self.ai._normalize_api_url(
                    self.config.get("ai.api_url", ""),
                    self.ai.api_style,
                )
                self.ai._clients.clear()
            if self.wechat:
                self.wechat.target_groups = groups
                self.wechat.reply_delay = cfg.reply_delay.value()

            logger.info("配置已保存")

    def _update_gui_status(self) -> None:
        if not self.window:
            return
        wx_status = self.wechat.status.value if self.wechat else "unknown"
        wx_color = {"connected": "#4CAF50", "connecting": "#FF9800", "error": "#F44336"}.get(wx_status, "#888")
        self.window.update_status("wechat", wx_status, wx_color)

        if getattr(self, "_qq_enabled", False) and self.qq:
            qq_status = self.qq.status.value
            qq_color = {"connected": "#4CAF50", "connecting": "#FF9800", "error": "#F44336"}.get(qq_status, "#888")
            self.window.update_status("qq", qq_status, qq_color)
        else:
            self.window.update_status("qq", "未启用", "#888")

        ai_ok = self.ai.is_available if self.ai else False
        self.window.update_status("ai", "可用" if ai_ok else "不可用", "#4CAF50" if ai_ok else "#F44336")

        wx_queue = self.handler._queue.qsize() if self.handler else 0
        qq_queue = self.qq_handler._queue.qsize() if self.qq_handler else 0
        q_size = wx_queue + qq_queue
        self.window.update_status("queue", str(q_size), "#FF9800" if q_size > 10 else "#4CAF50")

        style_name = self.style.active_name if self.style else "--"
        self.window.update_status("style", style_name, "#2196F3")


def run_web_server(bot: ChatBot, host: str, port: int) -> None:
    """运行 Web 控制台；端口冲突时短暂重试并记录完整错误。"""
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            uvicorn.run(web_app, host=host, port=port, log_level="warning")
            return
        except KeyboardInterrupt:
            return
        except BaseException as exc:
            last_error = exc
            log_exception("main.run_web_server", exc=exc)
            logger.error(
                f"Web 控制台启动失败（第 {attempt}/3 次）: {exc}"
            )
            time.sleep(2 * attempt)
    if last_error is not None:
        logger.error(f"Web 控制台最终启动失败: {last_error}")


def main():
    mark_startup("main")
    _install_crash_logging()
    _install_qt_message_filter()
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _CLEAN_EXIT_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"清理正常退出标记失败: {exc}", file=sys.stderr)
    _write_heartbeat()

    if not acquire_single_instance():
        message = (
            "已经有一个微信机器人在运行了，请勿重复启动。"
            "如需重启，请先运行 restart-clean.bat 清理旧实例。"
        )
        log_error_message(
            context="main.acquire_single_instance",
            error_type="DuplicateInstance",
            error_message=message,
        )
        _report_duplicate_start(message)
        sys.exit(1)

    bot = ChatBot()

    if not bot.initialize():
        print("初始化失败，请检查配置文件")
        sys.exit(1)

    # 创建 Qt 应用
    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(True)

    # 创建 asyncio 事件循环（独立线程）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async_thread = threading.Thread(target=loop.run_forever, daemon=True)
    async_thread.start()

    # 创建 Qt-asyncio 桥梁
    bridge = AsyncBridge(loop)
    bot.set_async_bridge(bridge)

    # 创建 GUI
    window = MainWindow()
    bot.window = window
    bot._setup_gui()
    window.show()
    _restore_recent_monitor_text(window)

    heartbeat_timer = QTimer()
    heartbeat_timer.timeout.connect(_write_heartbeat)
    heartbeat_timer.start(5000)

    # 启动 Web 服务器
    web_cfg = bot.config.get("web", {})
    if web_cfg.get("enabled", True):
        host = web_cfg.get("host", "127.0.0.1")
        port = web_cfg.get("port", 8080)
        web_thread = threading.Thread(
            target=run_web_server,
            args=(bot, host, port),
            daemon=True,
        )
        web_thread.start()
        logger.info(f"Web 仪表盘已启动: http://{host}:{port}")

    if bot.config.get("wechat.auto_start", True):
        QTimer.singleShot(800, lambda: bridge.call(bot.start))
        logger.info("已启用启动后自动连接微信")

    # 运行 Qt 事件循环
    exit_code = qt_app.exec()
    heartbeat_timer.stop()
    if exit_code == 0:
        _mark_clean_exit()
    sys.exit(exit_code)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:
        log_exception("main.__main__", exc=exc, level="CRITICAL")
        raise
