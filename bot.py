import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import yt_dlp

# ==========================================
# БЛОК НАСТРОЕК И ТЕКСТОВЫХ ПЕРЕМЕННЫХ
# ==========================================
BOT_USERNAME = "@zombie_dl_bot"  # Замени на юзернейм твоего бота

TXT_START = "👋🔗 Просто отправь мне ссылку на **YouTube, TikTok или SoundCloud**."
TXT_PROCESSING = "⏳ Скачиваю и обрабатываю файл..."
TXT_CHOOSE_FORMAT = "🎬 Выберите формат для скачивания:"
TXT_RECORD_BUTTON = "📊 Рекорд (Статистика)"
TXT_ERROR = "❌ Произошла ошибка при обработке файла."

BTN_MP3 = "🎵 MP3 (Аудио)"
BTN_MP4 = "🎥 MP4 (Видео)"
BTN_BOTH = "🔄 MP3 & MP4"
# ==========================================

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN env variable is missing!")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Хранилище статистики
STATS = {"audio": 0, "video": 0}

# Актуальный адрес Cobalt API
COBALT_API_URL = "https://api.cobalt.tools/api/json"

# Настройки для SoundCloud
YTDL_SOUNDCLOUD_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
    'source_address': '0.0.0.0',
    'rm_cached_media': True,
    'format': 'ba/b',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

class BotStates(StatesGroup):
    waiting_for_format_selection = State()

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=TXT_RECORD_BUTTON)]],
        resize_keyboard=True
    )

def get_format_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_MP3, callback_data="fmt_mp3")],
        [InlineKeyboardButton(text=BTN_MP4, callback_data="fmt_mp4")],
        [InlineKeyboardButton(text=BTN_BOTH, callback_data="fmt_both")]
    ])

def clean_filename(title: str) -> str:
    for c in ['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>', '.', ',', '(', ')', '[', ']', '{', '}']:
        title = title.replace(c, '')
    return title.strip() or "media_file"

async def safe_edit_text(msg: Message, text: str):
    try:
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception:
        await msg.answer(text, parse_mode="Markdown")

async def download_file_by_url(url: str, destination: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                with open(destination, 'wb') as f:
                    f.write(await response.read())
                return True
    return False

# --- МЕТОД А: СКАЧИВАНИЕ SOUNDCLOUD (С сохранением оригинального названия) ---
async def download_soundcloud(url: str, message: Message):
    status_msg = await message.answer(TXT_PROCESSING)
    os.makedirs("downloads", exist_ok=True)
    
    # Сначала просто извлекаем инфо, чтобы узнать имя трека
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = clean_filename(info.get('title', 'SoundCloud_Track'))
    except Exception:
        title = "SoundCloud_Track"

    out_template = f"downloads/{title}.%(ext)s"
    opts = {**YTDL_SOUNDCLOUD_OPTS, 'outtmpl': out_template}
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + ".mp3"
            
        await message.reply_audio(
            FSInputFile(filename), 
            title=title, 
            performer="SoundCloud"
        )
        STATS["audio"] += 1
        if os.path.exists(filename):
            os.remove(filename)
        await status_msg.delete()
    except Exception as e:
        logging.error(f"SoundCloud error: {e}")
        await safe_edit_text(status_msg, TXT_ERROR)

# --- МЕТОД Б: СКАЧИВАНИЕ YT / TIKTOK С ИСПРАВЛЕННЫМ COBALT API ---
async def download_via_service(url: str, mode: str, message: Message):
    status_msg = await message.answer(TXT_PROCESSING)
    os.makedirs("downloads", exist_ok=True)
    
    # Новые обязательные параметры для Cobalt API для защиты от ошибки 400
    payload = {
        "url": url,
        "videoQuality": "720",
        "audioFormat": "mp3",
        "filenamePattern": "classic",
        "isAudioOnly": True if mode == "mp3" else False,
        "isNoTTWatermark": True
    }
    
    # Браузерные заголовки, чтобы Cobalt не выдавал Bad Request
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://cobalt.tools",
        "Referer": "https://cobalt.tools/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(COBALT_API_URL, json=payload, headers=headers) as response:
                if response.status != 200:
                    err_text = await response.text()
                    logging.error(f"Cobalt error body: {err_text}")
                    raise ValueError(f"Сервер вернул код {response.status}")
                
                res_json = await response.json()
                
                if "picker" in res_json:
                    direct_url = res_json["picker"][0]["url"]
                else:
                    direct_url = res_json.get("url")
                
                if not direct_url:
                    raise ValueError("Прямая ссылка отсутствует в ответе сервера.")
                
                # Извлекаем название трека, которое нам заботливо прислал Cobalt
                display_title = res_json.get("text", "Media File")
                safe_title = clean_filename(display_title)
                
                ext = "mp3" if mode == "mp3" else "mp4"
                local_filename = f"downloads/{safe_title}.{ext}"
                
                success = await download_file_by_url(direct_url, local_filename)
                if not success:
                    raise ValueError("Не удалось сохранить файл.")
                
                # Отправляем файл с оригинальным названием
                if mode == "mp3":
                    await message.reply_audio(
                        FSInputFile(local_filename), 
                        title=display_title, 
                        performer="Zombie Loader"
                    )
                    STATS["audio"] += 1
                else:
                    # Для видео выводим название в описание под ним
                    await message.reply_video(
                        FSInputFile(local_filename), 
                        caption=f"🎬 **{display_title}**"
                    )
                    STATS["video"] += 1
                
                if os.path.exists(local_filename):
                    os.remove(local_filename)
                    
                await status_msg.delete()

    except Exception as e:
        logging.error(f"Cobalt API error: {e}")
        await safe_edit_text(status_msg, f"{TXT_ERROR}\n\n*Инфо:* Не удалось обработать ссылку.")


# --- ОБРАБОТЧИКИ СОБЫТИЙ ---

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(TXT_START, reply_markup=get_main_keyboard())

@dp.message(F.text == TXT_RECORD_BUTTON)
async def show_record(message: Message):
    total = STATS["audio"] + STATS["video"]
    txt = f"🏆 **Статистика скачиваний:**\n\n🎵 Аудио: {STATS['audio']}\n🎥 Видео: {STATS['video']}\n\nВсего скачано: {total} файлов."
    await message.answer(txt)

@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_urls(message: Message, state: FSMContext):
    url = message.text.strip()
    await state.clear()
    
    if "soundcloud.com" in url:
        await download_soundcloud(url, message)
    else:
        await state.update_data(current_url=url)
        await state.set_state(BotStates.waiting_for_format_selection)
        await message.answer(TXT_CHOOSE_FORMAT, reply_markup=get_format_keyboard())

@dp.callback_query(BotStates.waiting_for_format_selection, F.data.startswith("fmt_"))
async def process_download_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_data = await state.get_data()
    url = user_data.get("current_url")
    
    if not url:
        await callback.message.answer(TXT_ERROR)
        await state.clear()
        return
        
    mode = callback.data.replace("fmt_", "")
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await state.clear() 
    
    if mode == "both":
        await download_via_service(url, "mp3", callback.message)
        await download_via_service(url, "mp4", callback.message)
    else:
        await download_via_service(url, mode, callback.message)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
