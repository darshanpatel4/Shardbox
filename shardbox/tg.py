"""Everything that talks to Telegram.

The app logs in as the user's own account (MTProto, through Telethon) rather than as a
bot, because the Bot API only allows 50 MB uploads while user accounts get 2 GB
(4 GB with Premium) per file.
"""

from __future__ import annotations

import asyncio
import logging
import math
import mimetypes
import platform
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine, Iterable

from telethon import TelegramClient, errors, functions, helpers, types
from telethon.tl.custom import Message

from . import APP_NAME, __version__
from .captions import INDEX_TAG, is_index_caption, parse_part_caption
from .chunking import PART_SIZE, max_split_bytes, read_at, write_at

log = logging.getLogger(__name__)

# Disk reads/writes run here so the UI never freezes on a slow drive.
IO_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="io")

SMALL_FILE_LIMIT = 10 * 1024 * 1024  # Telegram's limit for the "small file" upload method
RETRIES = 6

Progress = Callable[[int], None]


class MissingPartError(RuntimeError):
    pass


async def run_all(coros: Iterable[Coroutine[Any, Any, Any]]) -> None:
    """Run coroutines concurrently; if one fails, cancel the rest and raise its error."""
    try:
        async with asyncio.TaskGroup() as group:
            for coro in coros:
                group.create_task(coro)
    except BaseExceptionGroup as eg:
        raise eg.exceptions[0] from None


def display_name(user: types.User | None) -> str:
    if user is None:
        return ""
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    if user.username:
        name = f"{name} (@{user.username})" if name else f"@{user.username}"
    return name or str(user.id)


class TelegramService:
    def __init__(self, api_id: int, api_hash: str, session: Path):
        self.client = TelegramClient(
            str(session),
            api_id,
            api_hash,
            device_model=APP_NAME,
            system_version=platform.platform(terse=True),
            app_version=__version__,
            flood_sleep_threshold=90,
            connection_retries=10,
            retry_delay=2,
            receive_updates=False,
        )
        self.client.parse_mode = None  # captions are sent as plain text
        self.me: types.User | None = None
        self.channel: types.Channel | None = None
        self._input_channel: types.TypeInputPeer | None = None

    # ------------------------------------------------------------ connection

    @property
    def premium(self) -> bool:
        return bool(self.me and getattr(self.me, "premium", False))

    @property
    def max_file_bytes(self) -> int:
        return max_split_bytes(self.premium)

    async def connect(self) -> None:
        await self.client.connect()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def is_authorized(self) -> bool:
        if not await self.client.is_user_authorized():
            return False
        self.me = await self.client.get_me()
        return True

    async def send_code(self, phone: str) -> None:
        await self.client.send_code_request(phone)

    async def sign_in_code(self, phone: str, code: str) -> None:
        """Raises SessionPasswordNeededError when the account has two-step verification."""
        await self.client.sign_in(phone=phone, code=code)
        self.me = await self.client.get_me()

    async def sign_in_password(self, password: str) -> None:
        await self.client.sign_in(password=password)
        self.me = await self.client.get_me()

    async def log_out(self) -> None:
        await self.client.log_out()

    # ------------------------------------------------------------ channel

    async def list_channels(self) -> list[tuple[int, str]]:
        """Channels and groups where this account can post files."""
        found = []
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, types.Channel):
                continue
            rights = entity.admin_rights
            if entity.creator or (rights and (entity.megagroup or rights.post_messages)):
                found.append((entity.id, dialog.name))
        return found

    async def create_channel(self, title: str) -> tuple[int, str]:
        result = await self.client(
            functions.channels.CreateChannelRequest(
                title=title,
                about=f"Private storage for {APP_NAME}. Don't delete messages here by hand.",
                broadcast=True,
            )
        )
        channel = result.chats[0]
        return channel.id, channel.title

    async def open_channel(self, channel_id: int) -> str:
        try:
            entity = await self.client.get_entity(types.PeerChannel(channel_id))
        except ValueError:
            # A fresh session doesn't know the channel's access hash yet; loading dialogs fixes that.
            await self.client.get_dialogs()
            entity = await self.client.get_entity(types.PeerChannel(channel_id))
        self.channel = entity
        self._input_channel = await self.client.get_input_entity(entity)
        return entity.title

    # ------------------------------------------------------------ retries

    async def _retry(self, make_call: Callable[[], Awaitable[Any]], what: str = "request") -> Any:
        delay = 2.0
        for attempt in range(1, RETRIES + 1):
            try:
                return await make_call()
            except errors.FloodError as exc:
                wait = getattr(exc, "seconds", None) or 5
                log.warning("Telegram asked us to wait %ss (%s)", wait, what)
                if attempt == RETRIES:
                    raise
                await asyncio.sleep(wait + 1)
            except (errors.ServerError, OSError) as exc:
                log.warning("%s failed (attempt %d/%d): %r", what, attempt, RETRIES, exc)
                if attempt == RETRIES:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        raise RuntimeError(f"Telegram {what} failed")

    async def _invoke(self, request: Any, what: str) -> Any:
        async def call() -> Any:
            result = await self.client(request)
            if result is False:
                raise errors.ServerError(request, f"{what} was not accepted")
            return result

        return await self._retry(call, what)

    # ------------------------------------------------------------ upload

    async def upload_range(
        self,
        path: str,
        offset: int,
        size: int,
        file_name: str,
        caption: str,
        workers: int,
        progress: Progress,
    ) -> int:
        """Upload bytes [offset, offset+size) of a local file as one document. Returns the message id."""
        if self._input_channel is None:
            raise RuntimeError("No storage channel is open")
        if size <= 0:
            raise ValueError("Telegram cannot store empty files")
        if size > self.max_file_bytes:
            raise ValueError("This piece is bigger than Telegram allows for this account")

        file_id = helpers.generate_random_long()
        total = math.ceil(size / PART_SIZE)
        big = size > SMALL_FILE_LIMIT
        loop = asyncio.get_running_loop()
        pending = iter(range(total))  # shared by all workers; each takes the next part number

        async def worker() -> None:
            with open(path, "rb") as fh:
                for i in pending:
                    length = min(PART_SIZE, size - i * PART_SIZE)
                    data = await loop.run_in_executor(IO_POOL, read_at, fh, offset + i * PART_SIZE, length)
                    if big:
                        request = functions.upload.SaveBigFilePartRequest(file_id, i, total, data)
                    else:
                        request = functions.upload.SaveFilePartRequest(file_id, i, data)
                    await self._invoke(request, f"upload of part {i + 1}/{total}")
                    progress(length)

        await run_all(worker() for _ in range(max(1, min(workers, total))))

        if big:
            input_file = types.InputFileBig(file_id, total, file_name)
        else:
            input_file = types.InputFile(file_id, total, file_name, "")
        media = types.InputMediaUploadedDocument(
            file=input_file,
            mime_type=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
            attributes=[types.DocumentAttributeFilename(file_name)],
            force_file=True,
        )
        # The same random_id is reused on retries, so Telegram won't post the file twice.
        request = functions.messages.SendMediaRequest(
            peer=self._input_channel,
            media=media,
            message=caption,
            random_id=helpers.generate_random_long(),
            silent=True,
        )
        result = await self._invoke(request, "send document")
        return self._message_id(result)

    @staticmethod
    def _message_id(result: Any) -> int:
        updates = getattr(result, "updates", None) or []
        for update in updates:
            if isinstance(update, (types.UpdateNewChannelMessage, types.UpdateNewMessage)):
                if isinstance(update.message, types.Message):
                    return update.message.id
        for update in updates:
            if isinstance(update, types.UpdateMessageID):
                return update.id
        raise RuntimeError("Telegram did not return the new message id")

    # ------------------------------------------------------------ download

    async def get_messages(self, ids: list[int]) -> dict[int, Message]:
        found: dict[int, Message] = {}
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            msgs = await self._retry(
                lambda c=chunk: self.client.get_messages(self.channel, ids=c), "fetch messages"
            )
            for msg in msgs:
                if msg is not None:
                    found[msg.id] = msg
        return found

    async def _document(self, message_id: int) -> types.Document:
        msg = (await self.get_messages([message_id])).get(message_id)
        if msg is None or msg.document is None:
            raise MissingPartError(
                f"Message {message_id} is missing from the channel (was it deleted in Telegram?)"
            )
        return msg.document

    async def download_range(
        self,
        message_id: int,
        dest_path: str,
        dest_offset: int,
        size: int,
        workers: int,
        progress: Progress,
    ) -> None:
        """Download one document into dest_path at dest_offset, using several connections."""
        document = await self._document(message_id)
        if document.size != size:
            raise MissingPartError(
                f"Message {message_id} holds {document.size} bytes but the index expects {size}"
            )
        loop = asyncio.get_running_loop()
        chunks = math.ceil(size / PART_SIZE)
        segments = max(1, min(workers, chunks))
        per = math.ceil(chunks / segments)

        async def segment(first: int, last: int) -> None:
            nonlocal document
            pos = first
            failures = 0
            with open(dest_path, "r+b") as fh:
                while pos < last:
                    try:
                        async for data in self.client.iter_download(
                            document,
                            offset=pos * PART_SIZE,
                            limit=last - pos,
                            request_size=PART_SIZE,
                            file_size=size,
                        ):
                            await loop.run_in_executor(IO_POOL, write_at, fh, dest_offset + pos * PART_SIZE, data)
                            pos += 1
                            progress(len(data))
                    except errors.FileReferenceExpiredError:
                        document = await self._document(message_id)
                    except errors.FloodError as exc:
                        await asyncio.sleep((getattr(exc, "seconds", None) or 5) + 1)
                    except (errors.ServerError, OSError) as exc:
                        failures += 1
                        log.warning("download of message %s failed (%d): %r", message_id, failures, exc)
                        if failures >= RETRIES:
                            raise
                        await asyncio.sleep(min(2**failures, 30))

        await run_all(
            segment(k * per, min(chunks, (k + 1) * per)) for k in range(segments) if k * per < chunks
        )

    async def download_message_to(self, message: Message, dest: Path) -> None:
        await self.client.download_media(message, file=str(dest))

    # ------------------------------------------------------------ housekeeping

    async def delete_messages(self, ids: list[int]) -> None:
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            await self._retry(lambda c=chunk: self.client.delete_messages(self.channel, c), "delete messages")

    async def find_index_backups(self, full_scan: bool = False) -> list[Message]:
        """Index backups in the channel, newest first."""
        found = [
            m
            async for m in self.client.iter_messages(self.channel, search=INDEX_TAG, limit=50)
            if m.document and is_index_caption(m.message)
        ]
        if not found and full_scan:
            found = [
                m
                async for m in self.client.iter_messages(self.channel, filter=types.InputMessagesFilterDocument)
                if is_index_caption(m.message)
            ]
        return sorted(found, key=lambda m: m.id, reverse=True)

    async def scan_part_captions(self, progress: Callable[[int], None]) -> list[tuple[int, dict]]:
        """Read the metadata caption of every stored piece in the channel."""
        found = []
        seen = 0
        async for msg in self.client.iter_messages(self.channel, filter=types.InputMessagesFilterDocument):
            seen += 1
            meta = parse_part_caption(msg.message)
            if meta is not None and msg.document is not None and msg.document.size == meta["ps"]:
                found.append((msg.id, meta))
            if seen % 100 == 0:
                progress(seen)
        progress(seen)
        return found
