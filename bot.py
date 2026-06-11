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
TXT_PROCESSING = "⏳ Скачиваю и обрабатываю файл через удаленный сервер..."
TXT_CHOOSE_FORMAT = "🎬 Выберите формат для скачивания:"
TXT_RECORD_BUTTON = "📊 Рекорд (Статистика)"
TXT_ERROR = "❌ Произошла ошибка при обработке файла сторонним сервисом."

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

# Публичный инстанс Cobalt API для обхода блокировок YT/TikTok
COBALT_API_URL = "https://api.cobalt.tools/api/json"

# Оставляем чистый yt-dlp только для SoundCloud
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
    # Кнопка поиска временно удалена, осталась только статистика
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TXT_RECORD_BUTTON)]
        ],
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

# Функция скачивания файлов по прямой ссылке в локальную папку
async def download_file_by_url(url: str, destination: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                with open(destination, 'wb') as f:
                    f.write(await response.read())
                return True
    return False

# Метод А: Скачивание SoundCloud через локальный yt-dlp
async def download_soundcloud(url: str, message: Message):
    status_msg = await message.answer(TXT_PROCESSING)
    clean_bot_name = BOT_USERNAME.replace("@", "")
    os.makedirs("downloads", exist_ok=True)
    
    out_template = f"downloads/soundcloud_{clean_bot_name}.%(ext)s"
    opts = {**YTDL_SOUNDCLOUD_OPTS, 'outtmpl': out_template}
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + ".mp3"
            
        await message.reply_audio(FSInputFile(filename))
        STATS["audio"] += 1
        if os.path.exists(filename):
            os.remove(filename)
        await status_msg.delete()
    except Exception as e:
        logging.error(f"SoundCloud error: {e}")
        await safe_edit_text(status_msg, TXT_ERROR)

# Метод Б: Хитрое скачивание YT/TikTok через сторонний API (Cobalt)
async def download_via_service(url: str, mode: str, message: Message):
    status_msg = await message.answer(TXT_PROCESSING)
    clean_bot_name = BOT_USERNAME.replace("@", "")
    os.makedirs("downloads", exist_ok=True)
    
    # Настраиваем параметры запроса к стороннему сайту
    payload = {
        "url": url,
        "vQuality": "720",          # Оптимальное качество для быстрой передачи в Телеграм
        "filenamePattern": "classic"
    }
    
    # Если пользователю нужен только звук (MP3)
    if mode == "mp3":
        payload["isAudioOnly"] = True
        payload["aFormat"] = "mp3"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(COBALT_API_URL, json=payload, headers=headers) as response:
                if response.status != 200:
                    raise ValueError(f"Сервер вернул код {response.status}")
                
                res_json = await response.json()
                
                # Если это TikTok с кучей картинок (слайдшоу), Cobalt вернет список 'picker'
                if "picker" in res_json:
                    await safe_edit_text(status_msg, "📸 Это слайдшоу из картинок. Скачиваю первое фото/аудио...")
                    direct_url = res_json["picker"][0]["url"]
                else:
                    direct_url = res_json.get("url")
                
                if not direct_url:
                    raise ValueError("Сторонний сервер не выдал прямую ссылку.")
                
                # Качаем файл к себе на сервер, чтобы красиво переслать пользователю
                ext = "mp3" if mode == "mp3" else "mp4"
                local_filename = f"downloads/media_{clean_bot_name}.{ext}"
                
                success = await download_file_by_url(direct_url, local_filename)
                if not success:
                    raise ValueError("Не удалось сохранить файл со стороннего сервера.")
                
                # Отправляем в Telegram
                if mode == "mp3":
                    await message.reply_audio(FSInputFile(local_filename))
                    STATS["audio"] += 1
                else:
                    await message.reply_video(FSInputFile(local_filename))
                    STATS["video"] += 1
                
                if os.path.exists(local_filename):
                    os.remove(local_filename)
                    
                await status_msg.delete()

    except Exception as e:
        logging.error(f"Cobalt API error: {e}")
        await safe_edit_text(status_msg, f"{TXT_ERROR}\n\n*Инфо:* Сервер перегружен или ссылка не поддерживается.")


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
        # Прямой SoundCloud обрабатываем как обычно нашими силами
        await download_soundcloud(url, message)
    else:
        # Для YT и TikTok вызываем меню выбора формата, а скачивать будем через Кобальт
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
