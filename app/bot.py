from __future__ import annotations

import asyncio
import io
import json
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from dotenv import load_dotenv

from .ai_client import AIAPIError, AIClient
from .memory_store import MemoryStore, QuotaExceeded, format_relevant_memories
from .personality import build_system_prompt, load_personality
from .text_utils import calls_bot, split_discord_message

PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "Disturpe AI Chatbot"
COMPONENT_TEXT_LIMIT = 4000
MAX_AI_OUTPUT_CHARS = 12_000
INCOMING_LOG_COLOR = 0x5865F2
OUTGOING_LOG_COLOR = 0x57F287
GUILD_REMOVE_LOG_COLOR = 0xED4245
load_dotenv(PROJECT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    discord_token: str
    authorized_user_id: int
    message_log_channel_id: int
    bot_log_channel_id: int
    data_dir: Path
    personality_file: Path
    message_quota: int
    ai_api_url: str
    ai_model: str
    ai_api_key: str
    ai_api_key_header: str
    ai_api_key_prefix: str
    ai_extra_headers: dict[str, str]
    ai_allow_insecure_http: bool
    ai_connect_timeout: float
    ai_read_timeout: float
    ai_max_retries: int
    ai_max_response_bytes: int
    ai_requests_per_minute: int
    ai_send_image_urls: bool
    ai_send_file_urls: bool
    log_message_content: bool
    memory_retention_days: int
    bot_trigger_names: tuple[str, ...]
    timezone: ZoneInfo

    @classmethod
    def from_env(cls) -> Settings:
        configured_data_dir = Path(os.getenv("DATA_DIR", "data"))
        if not configured_data_dir.is_absolute():
            configured_data_dir = PROJECT_DIR / configured_data_dir
        configured_personality_file = Path(
            os.getenv("PERSONALITY_FILE", "config/personality.md")
        )
        if not configured_personality_file.is_absolute():
            configured_personality_file = PROJECT_DIR / configured_personality_file
        trigger_names = tuple(
            name.strip()
            for name in os.getenv(
                "BOT_TRIGGER_NAMES", "Disturpe,Disturpe AI,Disturpe AI Chatbot"
            ).split(",")
            if name.strip()
        )
        return cls(
            discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
            authorized_user_id=_env_int("AUTHORIZED_USER_ID"),
            message_log_channel_id=_env_int("MESSAGE_LOG_CHANNEL_ID"),
            bot_log_channel_id=_env_int("BOT_LOG_CHANNEL_ID"),
            data_dir=configured_data_dir,
            personality_file=configured_personality_file,
            message_quota=max(_env_int("MESSAGE_QUOTA", 1000), 1),
            ai_api_url=os.getenv("AI_API_URL", "").strip(),
            ai_model=os.getenv("AI_MODEL", "").strip(),
            ai_api_key=os.getenv("AI_API_KEY", "").strip(),
            ai_api_key_header=os.getenv("AI_API_KEY_HEADER", "Authorization").strip(),
            ai_api_key_prefix=os.getenv("AI_API_KEY_PREFIX", "Bearer").strip(),
            ai_extra_headers=_env_json_headers("AI_EXTRA_HEADERS_JSON"),
            ai_allow_insecure_http=_env_bool("AI_ALLOW_INSECURE_HTTP", False),
            ai_connect_timeout=max(_env_float("AI_CONNECT_TIMEOUT", 10.0), 0.1),
            ai_read_timeout=max(_env_float("AI_READ_TIMEOUT", 60.0), 0.1),
            ai_max_retries=max(_env_int("AI_MAX_RETRIES", 3), 1),
            ai_max_response_bytes=max(
                _env_int("AI_MAX_RESPONSE_BYTES", 2_000_000), 1_024
            ),
            ai_requests_per_minute=max(_env_int("AI_REQUESTS_PER_MINUTE", 60), 0),
            ai_send_image_urls=_env_bool("AI_SEND_IMAGE_URLS", True),
            ai_send_file_urls=_env_bool("AI_SEND_FILE_URLS", False),
            log_message_content=_env_bool("LOG_MESSAGE_CONTENT", False),
            memory_retention_days=max(_env_int("MEMORY_RETENTION_DAYS", 30), 0),
            bot_trigger_names=trigger_names,
            timezone=_env_timezone("BOT_TIMEZONE", "UTC"),
        )

    def validate(self) -> None:
        missing = []
        if not self.discord_token:
            missing.append("DISCORD_TOKEN")
        if not self.ai_api_url:
            missing.append("AI_API_URL")
        if not self.ai_model:
            missing.append("AI_MODEL")
        if not self.bot_trigger_names:
            missing.append("BOT_TRIGGER_NAMES")
        if missing:
            raise RuntimeError(
                "Missing required settings in .env: " + ", ".join(missing)
            )


@dataclass(frozen=True)
class ChatLogEntry:
    timestamp: datetime
    user_id: int
    display_name: str
    username: str
    user_content: str
    response_content: str
    guild_name: str
    channel_name: str
    jump_url: str
    user_avatar_url: str | None = None
    bot_avatar_url: str | None = None
    attachment_count: int = 0
    duration_seconds: float | None = None
    history_count: int | None = None
    related_count: int | None = None
    remaining: int | None = None


@dataclass(frozen=True)
class GuildLogEntry:
    action: Literal["joined", "removed"]
    timestamp: datetime
    guild_id: int
    guild_name: str
    owner_id: int | None
    member_count: int | None
    guild_count: int
    created_at: datetime
    icon_url: str | None = None


def _env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _env_json_headers(name: str) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a valid JSON object.") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"{name} must contain string keys and values only.")
    return value


def _env_timezone(name: str, default: str) -> ZoneInfo:
    configured = os.getenv(name, default).strip() or default
    try:
        return ZoneInfo(configured)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"{name} is not a valid IANA time zone: {configured}") from exc


class AsyncRateLimiter:
    def __init__(self, limit: int, period: float) -> None:
        self.limit = limit
        self.period = period
        self.timestamps: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                while self.timestamps and self.timestamps[0] <= now - self.period:
                    self.timestamps.popleft()
                if len(self.timestamps) < self.limit:
                    self.timestamps.append(now)
                    return
                delay = self.period - (now - self.timestamps[0]) + 0.05
            await asyncio.sleep(delay)


class DisturpeBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.dm_messages = True
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.store = MemoryStore(settings.data_dir / "memory.db")
        self.personality = load_personality(settings.personality_file)
        self.ai = AIClient(
            settings.ai_api_url,
            settings.ai_model,
            api_key=settings.ai_api_key,
            api_key_header=settings.ai_api_key_header,
            api_key_prefix=settings.ai_api_key_prefix,
            extra_headers=settings.ai_extra_headers,
            allow_insecure_http=settings.ai_allow_insecure_http,
            timeout=(settings.ai_connect_timeout, settings.ai_read_timeout),
            max_retries=settings.ai_max_retries,
            max_response_bytes=settings.ai_max_response_bytes,
        )
        self.message_log_queue: asyncio.Queue[ChatLogEntry] = asyncio.Queue(maxsize=300)
        self.bot_log_queue: asyncio.Queue[str | GuildLogEntry] = asyncio.Queue(
            maxsize=300
        )
        self.message_log_task: asyncio.Task | None = None
        self.bot_log_task: asyncio.Task | None = None
        self.quota_reset_task: asyncio.Task | None = None
        self.user_locks: dict[int, asyncio.Lock] = {}
        self.api_limiter = (
            AsyncRateLimiter(limit=settings.ai_requests_per_minute, period=60.0)
            if settings.ai_requests_per_minute
            else None
        )

    def log(self, message: str) -> None:
        output = f"[{datetime.now(self.settings.timezone):%H:%M:%S}] {message}"
        print(output)
        if self.settings.bot_log_channel_id and not self.bot_log_queue.full():
            self.bot_log_queue.put_nowait(output)

    def _log_chat_exchange(
        self,
        message: discord.Message,
        user_content: str,
        response_content: str,
        *,
        attachment_count: int,
        duration_seconds: float,
        history_count: int | None,
        related_count: int | None,
        remaining: int | None,
    ) -> None:
        guild_name = (
            message.guild.name if message.guild is not None else "Direct Message"
        )
        raw_channel_name = getattr(message.channel, "name", None)
        channel_name = f"#{raw_channel_name}" if raw_channel_name else "Direct Message"
        bot_avatar_url = (
            str(self.user.display_avatar.url) if self.user is not None else None
        )
        if not self.settings.log_message_content:
            user_content = "*(Message content logging is disabled.)*"
            response_content = "*(Response content logging is disabled.)*"
        entry = ChatLogEntry(
            timestamp=message.created_at.astimezone(self.settings.timezone),
            user_id=message.author.id,
            display_name=message.author.display_name,
            username=message.author.name,
            user_content=user_content,
            response_content=response_content,
            guild_name=guild_name,
            channel_name=channel_name,
            jump_url=message.jump_url,
            user_avatar_url=str(message.author.display_avatar.url),
            bot_avatar_url=bot_avatar_url,
            attachment_count=attachment_count,
            duration_seconds=duration_seconds,
            history_count=history_count,
            related_count=related_count,
            remaining=remaining,
        )
        print(
            f"[{datetime.now(self.settings.timezone):%H:%M:%S}] 🗨️ "
            f"[{message.author.id}] Response {duration_seconds:.2f}s | "
            f"history={history_count or 0}, related={related_count or 0}, "
            f"remaining={remaining}"
        )
        if self.settings.message_log_channel_id and not self.message_log_queue.full():
            self.message_log_queue.put_nowait(entry)

    def _log_guild_event(
        self,
        guild: discord.Guild,
        action: Literal["joined", "removed"],
    ) -> None:
        entry = GuildLogEntry(
            action=action,
            timestamp=datetime.now(self.settings.timezone),
            guild_id=guild.id,
            guild_name=guild.name,
            owner_id=guild.owner_id,
            member_count=guild.member_count,
            guild_count=len(self.guilds),
            created_at=guild.created_at,
            icon_url=str(guild.icon.url) if guild.icon else None,
        )
        action_text = "joined" if action == "joined" else "left"
        print(
            f"[{entry.timestamp:%H:%M:%S}] "
            f"🏠 [GUILD] {action_text} {guild.name} ({guild.id})."
        )
        if self.settings.bot_log_channel_id and not self.bot_log_queue.full():
            self.bot_log_queue.put_nowait(entry)

    async def on_ready(self) -> None:
        if self.settings.message_log_channel_id and self.message_log_task is None:
            self.message_log_task = asyncio.create_task(self._message_log_worker())
        if self.settings.bot_log_channel_id and self.bot_log_task is None:
            self.bot_log_task = asyncio.create_task(self._bot_log_worker())
        if self.quota_reset_task is None:
            self.quota_reset_task = asyncio.create_task(self._quota_reset_worker())
        await self._ensure_daily_quota()
        await self._purge_expired_memory()
        message_count, user_count = await asyncio.to_thread(self.store.stats)
        self.log(
            f"✅ [SYSTEM] {self.user} online | {message_count} messages, "
            f"{user_count} users | "
            f"FTS5={'enabled' if self.store.fts_available else 'fallback search'}"
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self._log_guild_event(guild, "joined")

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._log_guild_event(guild, "removed")

    async def _message_log_worker(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            entry = await self.message_log_queue.get()
            channel = self.get_channel(self.settings.message_log_channel_id)
            if channel is None:
                print("⚠️ Discord message log channel was not found.")
                continue
            try:
                await channel.send(
                    view=self._chat_log_view(entry),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.DiscordException as exc:
                print(f"Could not send the Discord message log: {exc}")

    async def _bot_log_worker(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            first = await self.bot_log_queue.get()
            batch = [first]
            await asyncio.sleep(1.5)
            while not self.bot_log_queue.empty():
                batch.append(self.bot_log_queue.get_nowait())
            channel = self.get_channel(self.settings.bot_log_channel_id)
            if channel is None:
                print("⚠️ Discord bot log channel was not found.")
                continue
            text_batch: list[str] = []
            for entry in batch:
                if isinstance(entry, str):
                    text_batch.append(entry)
                    continue
                await self._send_text_log_batch(channel, text_batch)
                text_batch.clear()
                try:
                    await channel.send(
                        view=self._guild_log_view(entry),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.DiscordException as exc:
                    print(f"Could not send the Discord bot log: {exc}")
            await self._send_text_log_batch(channel, text_batch)

    @staticmethod
    async def _send_text_log_batch(
        channel: discord.abc.Messageable,
        entries: list[str],
    ) -> None:
        if not entries:
            return
        for chunk in split_discord_message("\n".join(entries)):
            try:
                await channel.send(f"```\n{chunk}\n```")
            except discord.DiscordException as exc:
                print(f"Could not send the Discord log: {exc}")

    @staticmethod
    def _chat_log_view(entry: ChatLogEntry) -> discord.ui.LayoutView:
        display_name = discord.utils.escape_markdown(entry.display_name)
        username = discord.utils.escape_markdown(entry.username)
        guild_name = discord.utils.escape_markdown(entry.guild_name)
        channel_name = discord.utils.escape_markdown(entry.channel_name)
        timestamp = int(entry.timestamp.timestamp())

        user_header = (
            f"### 📨 {display_name}\n`@{username}` · User ID: `{entry.user_id}`"
        )
        response_header = f"### 🗨️ {APP_NAME} response\n**{APP_NAME} → {display_name}**"
        attachment_info = (
            f" • 📎 {entry.attachment_count} attachments"
            if entry.attachment_count
            else ""
        )
        footer = (
            f"-# 🕒 <t:{timestamp}:T> • 📍 {guild_name} / {channel_name}"
            f"{attachment_info}\n"
            f"-# ⏱️ {entry.duration_seconds or 0:.2f}s • "
            f"🧠 History {entry.history_count if entry.history_count is not None else '-'} • "
            f"🔎 Related {entry.related_count if entry.related_count is not None else '-'} • "
            f"🎫 Remaining {entry.remaining if entry.remaining is not None else '-'} • "
            f"[View message]({entry.jump_url})"
        )
        user_body = _quote_component_text(entry.user_content)
        response_body = _quote_component_text(entry.response_content)
        body_limit = (
            COMPONENT_TEXT_LIMIT - len(user_header) - len(response_header) - len(footer)
        )
        user_body, response_body = _fit_conversation_texts(
            user_body,
            response_body,
            body_limit,
        )

        user_heading: discord.ui.Item
        if entry.user_avatar_url:
            user_heading = discord.ui.Section(
                discord.ui.TextDisplay(user_header),
                accessory=discord.ui.Thumbnail(
                    entry.user_avatar_url,
                    description=f"{entry.display_name} avatar",
                ),
            )
        else:
            user_heading = discord.ui.TextDisplay(user_header)

        response_heading: discord.ui.Item
        if entry.bot_avatar_url:
            response_heading = discord.ui.Section(
                discord.ui.TextDisplay(response_header),
                accessory=discord.ui.Thumbnail(
                    entry.bot_avatar_url,
                    description=f"{APP_NAME} avatar",
                ),
            )
        else:
            response_heading = discord.ui.TextDisplay(response_header)

        container = discord.ui.Container(
            user_heading,
            discord.ui.TextDisplay(user_body),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            response_heading,
            discord.ui.TextDisplay(response_body),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(footer),
            accent_color=INCOMING_LOG_COLOR,
        )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view

    @staticmethod
    def _guild_log_view(entry: GuildLogEntry) -> discord.ui.LayoutView:
        guild_name = discord.utils.escape_markdown(entry.guild_name)
        timestamp = int(entry.timestamp.timestamp())
        created_at = int(entry.created_at.timestamp())
        if entry.action == "joined":
            title = "### ✅ Bot added to a guild"
            status = f"{APP_NAME} joined a new guild."
            accent_color = OUTGOING_LOG_COLOR
        else:
            title = "### 🚪 Bot removed from a guild"
            status = f"{APP_NAME} is no longer in this guild."
            accent_color = GUILD_REMOVE_LOG_COLOR

        heading = f"{title}\n**{guild_name}**\n-# Guild ID: `{entry.guild_id}`"
        details = (
            f"{status}\n\n"
            f"👥 **Member count:** {entry.member_count if entry.member_count is not None else '-'}\n"
            f"👑 **Owner ID:** `{entry.owner_id if entry.owner_id is not None else '-'}`\n"
            f"🌐 **Guilds containing the bot:** {entry.guild_count}"
        )
        footer = (
            f"-# Event time: <t:{timestamp}:F>\n-# Guild created: <t:{created_at}:D>"
        )

        heading_text = discord.ui.TextDisplay(heading)
        if entry.icon_url:
            heading_item: discord.ui.Item = discord.ui.Section(
                heading_text,
                accessory=discord.ui.Thumbnail(
                    entry.icon_url,
                    description=f"{entry.guild_name} icon",
                ),
            )
        else:
            heading_item = heading_text
        container = discord.ui.Container(
            heading_item,
            discord.ui.TextDisplay(details),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(footer),
            accent_color=accent_color,
        )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view

    async def _ensure_daily_quota(self) -> None:
        current_date = datetime.now(self.settings.timezone).date().isoformat()
        reset, affected = await asyncio.to_thread(
            self.store.ensure_daily_quota, current_date
        )
        if reset:
            self.log(
                f"🌙 [QUOTA] Daily quotas reset: {affected} users now have "
                f"{self.settings.message_quota} messages available."
            )

    async def _quota_reset_worker(self) -> None:
        while not self.is_closed():
            now = datetime.now(self.settings.timezone)
            next_midnight = datetime.combine(
                now.date() + timedelta(days=1),
                wall_time.min,
                tzinfo=self.settings.timezone,
            )
            await asyncio.sleep(max((next_midnight - now).total_seconds(), 1.0))
            await self._ensure_daily_quota()
            await self._purge_expired_memory()

    async def _purge_expired_memory(self) -> None:
        if not self.settings.memory_retention_days:
            return
        cutoff = time.time() - self.settings.memory_retention_days * 86_400
        removed = await asyncio.to_thread(self.store.purge_older_than, cutoff)
        if removed:
            self.log(f"🧹 [PRIVACY] Deleted {removed} messages past retention.")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        content_lower = message.content.casefold().strip()
        command = content_lower.split(maxsplit=1)[0] if content_lower else ""
        if command == "!analyze":
            await self._handle_analysis(message)
            return
        if command == "!export":
            await self._handle_export(message)
            return
        if command == "!clear-user":
            await self._handle_clear(message)
            return
        if command == "!reset-quota":
            await self._handle_quota_reset(message)
            return
        if command == "!quota":
            await self._handle_my_quota(message)
            return
        if command == "!memory":
            await self._handle_stats(message)
            return

        is_dm = message.guild is None
        mentioned = self.user is not None and self.user.mentioned_in(message)
        called_by_name = calls_bot(message.content, self.settings.bot_trigger_names)
        replied = (
            message.reference is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author == self.user
        )
        if not (is_dm or mentioned or called_by_name or replied):
            return

        prompt = self._clean_prompt(message)
        image_urls, file_urls, attachment_meta = self._attachments(message)
        if not prompt and not attachment_meta:
            return
        log_content = prompt or "No text — attachment only."
        if not prompt:
            prompt = "Please examine this attachment and describe what you notice."

        lock = self.user_locks.setdefault(message.author.id, asyncio.Lock())
        async with lock:
            await self._chat(
                message,
                prompt=prompt[:12_000],
                log_content=log_content[:12_000],
                image_urls=image_urls,
                file_urls=file_urls,
                attachment_meta=attachment_meta,
            )

    async def _chat(
        self,
        message: discord.Message,
        *,
        prompt: str,
        log_content: str,
        image_urls: list[str],
        file_urls: list[str],
        attachment_meta: list[dict[str, str]],
    ) -> None:
        user_id = message.author.id
        session_id = f"discord:channel:{message.channel.id}"
        started = time.monotonic()
        print(
            f"[{datetime.now(self.settings.timezone):%H:%M:%S}] 📨 [{user_id}] "
            f"Message received ({len(log_content)} characters)"
        )
        await self._ensure_daily_quota()
        remaining_before = await asyncio.to_thread(
            self.store.remaining_quota, user_id, self.settings.message_quota
        )
        if remaining_before <= 0:
            quota_reply = (
                f"You have used today's quota of {self.settings.message_quota} messages. "
                f"Your quota resets at midnight ({self.settings.timezone.key})."
            )
            await message.reply(
                quota_reply,
                mention_author=False,
                view=self._quota_view(0),
            )
            self._log_chat_exchange(
                message,
                log_content,
                quota_reply,
                attachment_count=len(attachment_meta),
                duration_seconds=time.monotonic() - started,
                history_count=None,
                related_count=None,
                remaining=0,
            )
            return

        try:
            async with message.channel.typing():
                recent = await asyncio.to_thread(
                    self.store.recent, user_id, session_id, limit=24
                )
                related = await asyncio.to_thread(
                    self.store.relevant,
                    user_id,
                    prompt,
                    exclude_ids={item.id for item in recent},
                    limit=6,
                )
                system = build_system_prompt(
                    self.personality,
                    format_relevant_memories(related),
                    timezone=self.settings.timezone,
                )
                if self.api_limiter is not None:
                    await self.api_limiter.acquire()
                reply = await asyncio.to_thread(
                    self.ai.generate,
                    prompt,
                    system=system,
                    history=[item.as_history() for item in recent],
                    images=image_urls,
                    files=file_urls,
                )
                reply = _limit_ai_output(reply)
                remaining = await asyncio.to_thread(
                    self.store.add_exchange,
                    user_id=user_id,
                    session_id=session_id,
                    channel_id=message.channel.id,
                    user_content=prompt,
                    assistant_content=reply,
                    attachments=attachment_meta,
                    quota_limit=self.settings.message_quota,
                )
        except QuotaExceeded:
            quota_reply = (
                f"You have used today's quota of {self.settings.message_quota} messages. "
                f"Your quota resets at midnight ({self.settings.timezone.key})."
            )
            await message.reply(
                quota_reply,
                mention_author=False,
                view=self._quota_view(0),
            )
            self._log_chat_exchange(
                message,
                log_content,
                quota_reply,
                attachment_count=len(attachment_meta),
                duration_seconds=time.monotonic() - started,
                history_count=None,
                related_count=None,
                remaining=0,
            )
            return
        except AIAPIError as exc:
            self.log(f"❌ [AI_API] [{user_id}] HTTP={exc.status_code or '-'}")
            error_reply = (
                "The AI service is temporarily unavailable. Please try again shortly."
            )
            await message.reply(
                error_reply,
                mention_author=False,
            )
            self._log_chat_exchange(
                message,
                log_content,
                error_reply,
                attachment_count=len(attachment_meta),
                duration_seconds=time.monotonic() - started,
                history_count=None,
                related_count=None,
                remaining=remaining_before,
            )
            return
        except Exception as exc:  #noqa: BLE001 - ArviİS(arviis.)
            self.log(f"❌ [ERROR] [{user_id}] {type(exc).__name__}")
            error_reply = (
                "Something went wrong while generating a response. Please try again."
            )
            await message.reply(
                error_reply,
                mention_author=False,
            )
            self._log_chat_exchange(
                message,
                log_content,
                error_reply,
                attachment_count=len(attachment_meta),
                duration_seconds=time.monotonic() - started,
                history_count=None,
                related_count=None,
                remaining=remaining_before,
            )
            return

        chunks = split_discord_message(reply)
        quota_view = (
            self._quota_view(remaining)
            if remaining is not None
            and remaining < self.settings.message_quota
            and remaining % 100 == 0
            else None
        )
        if len(chunks) == 1:
            await message.reply(chunks[0], mention_author=False, view=quota_view)
        else:
            await message.reply(chunks[0], mention_author=False)
            for index, chunk in enumerate(chunks[1:], start=1):
                is_last = index == len(chunks) - 1
                await message.channel.send(chunk, view=quota_view if is_last else None)
        self._log_chat_exchange(
            message,
            log_content,
            reply,
            attachment_count=len(attachment_meta),
            duration_seconds=time.monotonic() - started,
            history_count=len(recent),
            related_count=len(related),
            remaining=remaining,
        )

    @staticmethod
    def _quota_view(remaining: int) -> discord.ui.View:
        view = discord.ui.View(timeout=180)
        view.add_item(
            discord.ui.Button(
                label=f"Remaining quota: {remaining}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )
        return view

    def _clean_prompt(self, message: discord.Message) -> str:
        text = message.content
        if self.user is not None:
            text = text.replace(f"<@!{self.user.id}>", "")
            text = text.replace(f"<@{self.user.id}>", "")
        return text.strip()

    def _attachments(
        self,
        message: discord.Message,
    ) -> tuple[list[str], list[str], list[dict[str, str]]]:
        images: list[str] = []
        files: list[str] = []
        metadata: list[dict[str, str]] = []
        for attachment in message.attachments[:10]:
            content_type = attachment.content_type or "application/octet-stream"
            item = {
                "filename": attachment.filename,
                "content_type": content_type,
            }
            metadata.append(item)
            if content_type.startswith("image/") and self.settings.ai_send_image_urls:
                images.append(attachment.url)
            elif self.settings.ai_send_file_urls:
                files.append(attachment.url)
        return images, files, metadata

    def _authorized(self, message: discord.Message) -> bool:
        return bool(
            self.settings.authorized_user_id
            and message.author.id == self.settings.authorized_user_id
        )

    async def _handle_analysis(self, message: discord.Message) -> None:
        if not self._authorized(message):
            await message.channel.send(
                "⛔ **You are not authorized to use this command.**"
            )
            return
        await message.channel.send("🧠 Reviewing conversation memory...")
        async with message.channel.typing():
            conversation = await asyncio.to_thread(self.store.analysis_text)
            if not conversation:
                await message.channel.send("No stored conversations were found.")
                return
            try:
                if self.api_limiter is not None:
                    await self.api_limiter.acquire()
                result = await asyncio.to_thread(
                    self.ai.generate,
                    conversation,
                    system=(
                        "Analyze the Discord conversation history below. Provide a concise "
                        "summary for each user, common topics, and useful patterns. Write in "
                        "clear English and avoid unnecessarily repeating sensitive data."
                    ),
                    history=[],
                    temperature=0.3,
                    max_tokens=1200,
                )
                result = _limit_ai_output(result)
            except AIAPIError:
                await message.channel.send(
                    "The analysis service is currently unavailable."
                )
                return
        try:
            for chunk in split_discord_message("🧠 Analysis:\n" + result):
                await message.author.send(chunk)
        except discord.DiscordException:
            await message.channel.send(
                "The analysis was generated, but the DM could not be delivered. "
                "It was not posted here to protect user privacy."
            )
            return
        await message.channel.send("✅ The analysis was sent by DM.")

    async def _handle_export(self, message: discord.Message) -> None:
        if not self._authorized(message):
            await message.channel.send(
                "⛔ **You are not authorized to use this command.**"
            )
            return
        target_id = self._command_user_id(message.content)
        if target_id is None:
            await message.channel.send("Usage: `!export <user_id>`")
            return
        payload = await asyncio.to_thread(self.store.export_user, target_id)
        export_file = io.BytesIO(payload)
        try:
            await message.author.send(
                f"Conversation-memory export for user `{target_id}`:",
                file=discord.File(
                    export_file, filename=f"user_{target_id}_export.json"
                ),
            )
        except discord.DiscordException:
            await message.channel.send(
                "The export was generated, but the DM could not be delivered. "
                "It was not uploaded here to protect user privacy."
            )
            return
        await message.channel.send("✅ The export was sent by DM.")

    async def _handle_clear(self, message: discord.Message) -> None:
        if not self._authorized(message):
            await message.channel.send(
                "⛔ **You are not authorized to use this command.**"
            )
            return
        target_id = self._command_user_id(message.content)
        if target_id is None:
            await message.channel.send("Usage: `!clear-user <user_id>`")
            return
        count = await asyncio.to_thread(self.store.clear_user, target_id)
        await message.channel.send(
            f"✅ Conversation memory cleared ({count} messages). Quota was unchanged."
        )

    async def _handle_quota_reset(self, message: discord.Message) -> None:
        if not self._authorized(message):
            await message.channel.send("⛔ You are not authorized to use this command.")
            return
        target_id = self._command_user_id(message.content)
        if target_id is None:
            await message.channel.send("Usage: `!reset-quota <user_id or @user>`")
            return
        used_count = await asyncio.to_thread(self.store.reset_user_quota, target_id)
        self.log(
            f"🔄 [QUOTA] Administrator reset quota for user {target_id} "
            f"(previously used: {used_count})."
        )
        await message.channel.send(
            f"✅ Quota reset for <@{target_id}>: "
            f"**{self.settings.message_quota}/{self.settings.message_quota}**"
        )

    async def _handle_my_quota(self, message: discord.Message) -> None:
        await self._ensure_daily_quota()
        remaining = await asyncio.to_thread(
            self.store.remaining_quota,
            message.author.id,
            self.settings.message_quota,
        )
        await message.reply(
            "Your usage for today:",
            mention_author=False,
            view=self._quota_view(remaining),
        )

    async def _handle_stats(self, message: discord.Message) -> None:
        if not self._authorized(message):
            await message.channel.send(
                "⛔ **You are not authorized to use this command.**"
            )
            return
        message_count, user_count = await asyncio.to_thread(self.store.stats)
        search = "FTS5/BM25" if self.store.fts_available else "SQLite text search"
        await message.channel.send(
            f"🧠 `{message_count}` messages, `{user_count}` users. Search: **{search}**."
        )

    @staticmethod
    def _command_user_id(content: str) -> int | None:
        parts = content.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1].strip("<@!>"))
        except ValueError:
            return None


def _truncate_component_text(text: str, limit: int) -> str:
    marker = "\n> … *(truncated to fit the card limit)*"
    if len(text) <= limit:
        return text
    if limit <= len(marker):
        return text[:limit]
    return text[: limit - len(marker)].rstrip() + marker


def _limit_ai_output(text: str) -> str:
    marker = "\n\n… *(truncated at the safe response-length limit)*"
    if len(text) <= MAX_AI_OUTPUT_CHARS:
        return text
    return text[: MAX_AI_OUTPUT_CHARS - len(marker)].rstrip() + marker


def _quote_component_text(text: str) -> str:
    escaped = discord.utils.escape_markdown(text)
    return (
        "\n".join(f"> {line}" if line else ">" for line in escaped.splitlines())
        or "> *(No text)*"
    )


def _fit_conversation_texts(
    user_text: str,
    response_text: str,
    limit: int,
) -> tuple[str, str]:
    if len(user_text) + len(response_text) <= limit:
        return user_text, response_text

    user_limit = min(len(user_text), max(int(limit * 0.4), 1))
    response_limit = min(len(response_text), max(limit - user_limit, 1))
    remaining = max(limit - user_limit - response_limit, 0)

    response_extra = min(remaining, len(response_text) - response_limit)
    response_limit += response_extra
    remaining -= response_extra
    user_limit += min(remaining, len(user_text) - user_limit)

    return (
        _truncate_component_text(user_text, user_limit),
        _truncate_component_text(response_text, response_limit),
    )


def main() -> None:
    try:
        settings = Settings.from_env()
        settings.validate()
        bot = DisturpeBot(settings)
    except (RuntimeError, ValueError) as exc:
        print(f"❌ {exc}")
        return
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
