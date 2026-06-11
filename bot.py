import os
import asyncio
import logging
import urllib.request
import urllib.parse
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import yt_dlp

# ==========================================
# БЛОК НАСТРОЕК И ТЕКСТОВЫХ ПЕРЕМЕННЫХ (Кастомизируй тут)
# ==========================================
BOT_USERNAME = "@zombie_dl_bot"  # Замени на юзернейм твоего бота (без @ в названии файла, код сам уберет)

TXT_START = "👋🔗 Просто отправь мне ссылку на **YouTube, TikTok или SoundCloud**."
TXT_PROCESSING = "⏳ Обработка..."
TXT_LIMIT_EXCEEDED = "⚠️ Ограничение на video 25 минут!"
TXT_CHOOSE_FORMAT = "🎬 Выберите формат для скачивания:"
TXT_RECORD_BUTTON = "📊 Рекорд (Статистика)"
TXT_SEARCH_BUTTON = "🔍 Поиск в SoundCloud"
TXT_ENTER_QUERY = "🎵 Введите имя артиста и название трека для поиска:"
TXT_SEARCH_ING = "🔎 Ищу треки..."
TXT_NOT_FOUND = "❌ Ничего не найдено."
TXT_ERROR = "❌ Произошла ошибка при обработке."

# Кнопки форматов
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

# Базовые опции скачивания
YTDL_COMMON_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
    'source_address': '0.0.0.0',
    'rm_cached_media': True,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
}

class BotStates(StatesGroup):
    waiting_for_search_query = State()
    waiting_for_format_selection = State()

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TXT_SEARCH_BUTTON)],
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
        await msg.edit_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception:
        await msg.answer(text, parse_mode="Markdown", disable_web_page_preview=True)

# Функция парсинга HTML YouTube без использования API yt-dlp
def scrape_youtube_search(query: str):
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://www.youtube.com/results?search_query={encoded_query}"
        
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        
        html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        # Ищем ID видео и их названия в JSON-структуре страницы YouTube
        video_ids = re.findall(r"\"videoId\":\"([^\"]+)\"", html)
        titles = re.findall(r"\"title\":\{\"runs\":\[\{\"text\":\"([^\"]+)\"\}", html)
        
        results = []
        seen_ids = set()
        
        for i in range(len(video_ids)):
            v_id = video_ids[i]
            if v_id not in seen_ids:
                seen_ids.add(v_id)
                # Берем соответствующее название, если оно есть
                v_title = titles[i] if i < len(titles) else "Ремикс/Трек"
                # Декодируем юникод-символы в названии, если они смазались
                v_title = v_title.encode().decode('unicode-escape', errors='ignore')
                results.append({"id": v_id, "title": v_title})
                if len(results) >= 5: # Нам нужно топ-5 результатов
                    break
        return results
    except Exception as e:
        logging.error(f"Scraping error: {e}")
        return []

async def download_media(url: str, mode: str, message: Message):
    status_msg = await message.answer(TXT_PROCESSING)
    clean_bot_name = BOT_USERNAME.replace("@", "")
    
    ydl_opts_info = {**YTDL_COMMON_OPTS, 'skip_download': True, 'ignoreerrors': True}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Не удалось извлечь информацию о файле.")
            
            duration = info.get('duration', 0)
            title = clean_filename(info.get('title', 'media'))
            
            if duration and duration > 1500:
                await safe_edit_text(status_msg, TXT_LIMIT_EXCEEDED)
                return

        out_template = f"downloads/{title}_{clean_bot_name}.%(ext)s"
        os.makedirs("downloads", exist_ok=True)

        if mode == "mp3":
            ydl_opts = {
                **YTDL_COMMON_OPTS,
                'format': 'ba/b',
                'outtmpl': out_template,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + ".mp3"
                
            await message.reply_audio(FSInputFile(filename))
            STATS["audio"] += 1
            if os.path.exists(filename):
                os.remove(filename)

        elif mode == "mp4":
            ydl_opts = {
                **YTDL_COMMON_OPTS,
                'format': 'b[ext=mp4]/bestvideo+bestaudio/b', 
                'outtmpl': out_template,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if not filename.endswith('.mp4'):
                    base_path = filename.rsplit('.', 1)[0]
                    if os.path.exists(base_path + ".mp4"):
                        filename = base_path + ".mp4"
                    elif os.path.exists(filename):
                        os.rename(filename, base_path + ".mp4")
                        filename = base_path + ".mp4"
                    
            await message.reply_video(FSInputFile(filename))
            STATS["video"] += 1
            if os.path.exists(filename):
                os.remove(filename)
            
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logging.error(f"Ошибка при скачивании: {e}")
        await safe_edit_text(status_msg, TXT_ERROR)


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

@dp.message(F.text == TXT_SEARCH_BUTTON)
async def search_sc_start(message: Message, state: FSMContext):
    await message.answer(TXT_ENTER_QUERY)
    await state.set_state(BotStates.waiting_for_search_query)

@dp.message(BotStates.waiting_for_search_query)
async def search_sc_process(message: Message, state: FSMContext):
    query = message.text.strip()
    
    if "http://" in query or "https://" in query:
        await state.clear()
        await handle_urls(message, state)
        return

    status_msg = await message.answer(TXT_SEARCH_ING)
    
    try:
        # Запускаем парсинг веб-страницы в отдельном потоке, чтобы бот не зависал
        loop = asyncio.get_event_loop()
        entries = await loop.run_in_executor(None, scrape_youtube_search, query)
        
        if not entries:
            await safe_edit_text(status_msg, TXT_NOT_FOUND)
            await state.clear()
            return
        
        response_text = "🎵 **Найденные треки:**\n\n"
        valid_tracks_count = 0
        
        for i, entry in enumerate(entries, 1):
            title = entry["title"]
            video_id = entry["id"]
            url = f"https://www.youtube.com/watch?v={video_id}"
            response_text += f"{i}. [{title}]({url})\n\n"
            valid_tracks_count += 1
        
        response_text += "👉 **Нажмите на нужную ссылку выше, отправьте её мне в чат, и я пришлю её в MP3!**"
        
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(response_text, parse_mode="Markdown", disable_web_page_preview=True)
        
    except Exception as e:
        logging.error(f"Ошибка поиска: {e}")
        await safe_edit_text(status_msg, TXT_ERROR)
    
    await state.clear()

@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_urls(message: Message, state: FSMContext):
    url = message.text.strip()
    await state.clear()
    
    if "soundcloud.com" in url:
        await download_media(url, "mp3", message)
    elif "youtube.com" in url or "youtu.be" in url or "tiktok.com" in url:
        await state.update_data(current_url=url)
        await state.set_state(BotStates.waiting_for_format_selection)
        await message.answer(TXT_CHOOSE_FORMAT, reply_markup=get_format_keyboard())
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
        await download_media(url, "mp3", callback.message)
        await download_media(url, "mp4", callback.message)
    else:
        await download_media(url, mode, callback.message)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
