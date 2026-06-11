import os
import json
import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import yt_dlp


# ==========================================
# НАСТРОЙКИ
# ==========================================

BOT_USERNAME = "@zombie_dl_bot"
BOT_TAG = "_tg@zombie_dl_bot"

TXT_START = (
    "👋🔗 Просто отправь мне ссылку на "
    "**YouTube или SoundCloud**."
)

TXT_PROCESSING = "⏳ Скачиваю и обрабатываю файл..."
TXT_CHOOSE_FORMAT = "🎬 Выберите формат для скачивания:"
TXT_RECORD_BUTTON = "📊 Рекорд (Статистика)"

TXT_ERROR = (
    "❌ Произошла ошибка при обработке файла.\n\n"
    "Попробуйте ещё раз позже."
)

BTN_MP3 = "🎵 MP3 (Аудио)"
BTN_MP4 = "🎥 MP4 (Видео)"
BTN_BOTH = "🔄 MP3 & MP4"


# ==========================================
# ЛОГИ
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ==========================================
# TELEGRAM
# ==========================================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN env variable is missing")

bot = Bot(token=TOKEN)

dp = Dispatcher(
    storage=MemoryStorage()
)


# ==========================================
# ПАПКИ
# ==========================================

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

STATS_FILE = Path("stats.json")


# ==========================================
# СТАТИСТИКА
# ==========================================

def load_stats():
    if not STATS_FILE.exists():
        return {
            "audio": 0,
            "video": 0
        }

    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "audio": 0,
            "video": 0
        }


def save_stats():
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            STATS,
            f,
            ensure_ascii=False,
            indent=2
        )


STATS = load_stats()


# ==========================================
# СОСТОЯНИЯ
# ==========================================

class BotStates(StatesGroup):
    waiting_for_format_selection = State()


# ==========================================
# YT-DLP
# ==========================================

YTDL_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 60,
    "retries": 10,
    "fragment_retries": 10,
    "extractor_retries": 10,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "source_address": "0.0.0.0",
}


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def clean_filename(name: str) -> str:
    bad_chars = [
        "/", "\\", "?", "%", "*",
        ":", "|", '"', "<", ">",
        ".", ",", "(", ")", "[",
        "]", "{", "}"
    ]

    for char in bad_chars:
        name = name.replace(char, "")

    return name.strip()[:180] or "media"


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=TXT_RECORD_BUTTON
                )
            ]
        ],
        resize_keyboard=True
    )


def get_format_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_MP3,
                    callback_data="fmt_mp3"
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_MP4,
                    callback_data="fmt_mp4"
                )
            ],
            [
                InlineKeyboardButton(
                    text=BTN_BOTH,
                    callback_data="fmt_both"
                )
            ]
        ]
    )

# ==========================================
# СКАЧИВАНИЕ И ОТПРАВКА
# ==========================================

async def safe_edit_text(message_obj, text: str):
    try:
        await message_obj.edit_text(text)
    except Exception:
        try:
            await message_obj.answer(text)
        except Exception:
            pass


def get_media_info(url: str):
    with yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True
    }) as ydl:
        return ydl.extract_info(
            url,
            download=False
        )


def download_youtube_mp3(url: str):
    info = get_media_info(url)

    title = clean_filename(
        info.get("title", "audio")
    )

    filename = f"{title} {BOT_TAG}"

    outtmpl = str(
        DOWNLOAD_DIR / f"{filename}.%(ext)s"
    )

    opts = {
        **YTDL_BASE_OPTS,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    final_file = DOWNLOAD_DIR / f"{filename}.mp3"

    return {
        "title": title,
        "file": str(final_file)
    }


def download_youtube_mp4(url: str):
    info = get_media_info(url)

    title = clean_filename(
        info.get("title", "video")
    )

    filename = f"{title} {BOT_TAG}"

    outtmpl = str(
        DOWNLOAD_DIR / f"{filename}.%(ext)s"
    )

    opts = {
        **YTDL_BASE_OPTS,
        "format": "best[height<=720]",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    final_file = DOWNLOAD_DIR / f"{filename}.mp4"

    return {
        "title": title,
        "file": str(final_file)
    }


def download_soundcloud_mp3(url: str):
    info = get_media_info(url)

    title = clean_filename(
        info.get("title", "soundcloud")
    )

    filename = f"{title} {BOT_TAG}"

    outtmpl = str(
        DOWNLOAD_DIR / f"{filename}.%(ext)s"
    )

    opts = {
        **YTDL_BASE_OPTS,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    final_file = DOWNLOAD_DIR / f"{filename}.mp3"

    return {
        "title": title,
        "file": str(final_file)
    }


async def run_in_thread(func, *args):
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        None,
        lambda: func(*args)
    )


async def send_audio_file(
    message: Message,
    file_path: str,
    title: str,
    performer: str
):
    await message.answer_audio(
        audio=FSInputFile(file_path),
        title=f"{title} {BOT_TAG}",
        performer=performer
    )

    STATS["audio"] += 1
    save_stats()


async def send_video_file(
    message: Message,
    file_path: str,
    title: str
):
    await message.answer_video(
        video=FSInputFile(file_path),
        caption=f"🎬 {title}\n\n{BOT_TAG}"
    )

    STATS["video"] += 1
    save_stats()


async def remove_file(file_path: str):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass


async def process_soundcloud(
    url: str,
    message: Message
):
    status_msg = await message.answer(
        TXT_PROCESSING
    )

    try:
        result = await run_in_thread(
            download_soundcloud_mp3,
            url
        )

        await send_audio_file(
            message,
            result["file"],
            result["title"],
            "SoundCloud"
        )

        await remove_file(
            result["file"]
        )

        await status_msg.delete()

    except Exception as e:
        logger.exception(e)

        await safe_edit_text(
            status_msg,
            TXT_ERROR
        )


async def process_youtube(
    url: str,
    mode: str,
    message: Message
):
    status_msg = await message.answer(
        TXT_PROCESSING
    )

    try:
        if mode == "mp3":
            result = await run_in_thread(
                download_youtube_mp3,
                url
            )

            await send_audio_file(
                message,
                result["file"],
                result["title"],
                "Zombie Loader"
            )

        else:
            result = await run_in_thread(
                download_youtube_mp4,
                url
            )

            await send_video_file(
                message,
                result["file"],
                result["title"]
            )

        await remove_file(
            result["file"]
        )

        await status_msg.delete()

    except Exception as e:
        logger.exception(e)

        await safe_edit_text(
            status_msg,
            TXT_ERROR
        )

# ==========================================
# ОБРАБОТЧИКИ
# ==========================================

@dp.message(Command("start"))
async def cmd_start(
    message: Message,
    state: FSMContext
):
    await state.clear()

    await message.answer(
        TXT_START,
        reply_markup=get_main_keyboard()
    )


@dp.message(F.text == TXT_RECORD_BUTTON)
async def show_record(
    message: Message
):
    total = (
        STATS["audio"]
        + STATS["video"]
    )

    text = (
        "🏆 Статистика скачиваний\n\n"
        f"🎵 Аудио: {STATS['audio']}\n"
        f"🎥 Видео: {STATS['video']}\n\n"
        f"Всего скачано: {total}"
    )

    await message.answer(text)


@dp.message(
    F.text.contains("http://")
    | F.text.contains("https://")
)
async def handle_url(
    message: Message,
    state: FSMContext
):
    url = message.text.strip()

    await state.clear()

    if "soundcloud.com" in url.lower():
        await process_soundcloud(
            url,
            message
        )
        return

    youtube_domains = [
        "youtube.com",
        "youtu.be",
        "music.youtube.com"
    ]

    if any(
        domain in url.lower()
        for domain in youtube_domains
    ):
        await state.update_data(
            current_url=url
        )

        await state.set_state(
            BotStates.waiting_for_format_selection
        )

        await message.answer(
            TXT_CHOOSE_FORMAT,
            reply_markup=get_format_keyboard()
        )

        return

    await message.answer(
        "❌ Поддерживаются только YouTube и SoundCloud."
    )


@dp.callback_query(
    BotStates.waiting_for_format_selection,
    F.data.startswith("fmt_")
)
async def process_callback(
    callback: CallbackQuery,
    state: FSMContext
):
    await callback.answer()

    data = await state.get_data()

    url = data.get(
        "current_url"
    )

    if not url:
        await state.clear()

        await callback.message.answer(
            TXT_ERROR
        )
        return

    mode = callback.data.replace(
        "fmt_",
        ""
    )

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await state.clear()

    if mode == "both":
        await process_youtube(
            url,
            "mp3",
            callback.message
        )

        await process_youtube(
            url,
            "mp4",
            callback.message
        )

    elif mode == "mp3":
        await process_youtube(
            url,
            "mp3",
            callback.message
        )

    elif mode == "mp4":
        await process_youtube(
            url,
            "mp4",
            callback.message
        )


# ==========================================
# ЗАПУСК
# ==========================================

async def on_startup():
    logger.info(
        "Bot started"
    )


async def main():
    await on_startup()

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    asyncio.run(main())
