import os
import asyncio
import logging
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
TXT_LIMIT_EXCEEDED = "⚠️ Ограничение на видео 25 минут!"
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

# Инициализация логов и бота
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN env variable is missing!")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Хранилище статистики (в памяти, сбросится при перезапуске. Для надежности нужен Redis/DB)
STATS = {"audio": 0, "video": 0}

class SearchStates(StatesGroup):
    waiting_for_query = State()

# Главное меню (Ремоут кнопки внизу)
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TXT_SEARCH_BUTTON)],
            [KeyboardButton(text=TXT_RECORD_BUTTON)]
        ],
        resize_keyboard=True
    )

# Инлайн кнопки выбора формата для YT/TikTok
def get_format_keyboard(url: str):
    # Кодируем URL в callback_data аккуратно (длина callback_data ограничена 64 байтами)
    # Используем хэш или просто режем, но лучше передавать тип и ID, для простоты запишем в стейт или передадим часть.
    # Чтобы не упасть по лимиту длины, сохраняем урл, но здесь для простоты передадим через разделитель, если URL короткий.
    # Безопаснее: использовать inline-кнопки с префиксами.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_MP3, callback_data=f"down:mp3:{url}")],
        [InlineKeyboardButton(text=BTN_MP4, callback_data=f"down:mp4:{url}")],
        [InlineKeyboardButton(text=BTN_BOTH, callback_data=f"down:both:{url}")]
    ])

def clean_filename(title: str) -> str:
    # Удаляем запрещенные символы для имени файла
    for c in ['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>']:
        title = title.replace(c, '')
    return title.strip()

async def download_media(url: str, mode: str, message: Message):
    status_msg = await message.answer(TXT_PROCESSING)
    clean_bot_name = BOT_USERNAME.replace("@", "")
    
    # Опции для извлечения инфо
    ydl_opts_info = {'skip_download': True}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get('duration', 0)
            title = clean_filename(info.get('title', 'media'))
            
            # Проверка лимита 25 минут (1500 секунд)
            if duration > 1500:
                await status_msg.edit_text(TXT_LIMIT_EXCEEDED)
                return

        out_template = f"downloads/{title}_{clean_bot_name}.%(ext)s"
        os.makedirs("downloads", exist_ok=True)

        # Скачивание аудио
        if mode == "mp3":
            ydl_opts = {
                'format': 'bestaudio/best',
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
            os.remove(filename)

        # Скачивание видео
        elif mode == "mp4":
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': out_template,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if not filename.endswith('.mp4'):
                    filename = filename.rsplit('.', 1)[0] + ".mp4"
                    
            await message.reply_video(FSInputFile(filename))
            STATS["video"] += 1
            os.remove(filename)
            
        await status_msg.delete()

    except Exception as e:
        logging.error(e)
        await status_msg.edit_text(TXT_ERROR)


# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(TXT_START, reply_markup=get_main_keyboard())

@dp.message(F.text == TXT_RECORD_BUTTON)
async def show_record(message: Message):
    total = STATS["audio"] + STATS["video"]
    txt = f"🏆 **Статистика скачиваний:**\n\n🎵 Аудио: {STATS['audio']}\n🎥 Видео: {STATS['video']}\n\nВсего скачано: {total} файлов."
    await message.answer(txt)

@dp.message(F.text == TXT_SEARCH_BUTTON)
async def search_sc_start(message: Message, state: FSMContext):
    await message.answer(TXT_ENTER_QUERY)
    await state.set_state(SearchStates.waiting_for_query)

@dp.message(SearchStates.waiting_for_query)
async def search_sc_process(message: Message, state: FSMContext):
    query = message.text
    status_msg = await message.answer(TXT_SEARCH_ING)
    
    ydl_opts = {
        'default_search': 'ytsearch',
        'extract_flat': True,
        'skip_download': True,
    }
    # Для SoundCloud используем scsearch, если yt-dlp настроен. По дефолту надежнее искать через ytsearch (ютуб) или scsearch.
    # Заменим на поиск в soundcloud: `scsearch5:query`
    ydl_opts['default_search'] = 'scsearch'
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Ищем первые 5 результатов
            search_result = ydl.extract_info(f"scsearch5:{query}", download=False)
            entries = search_result.get('entries', [])
            
            if not entries:
                await status_msg.edit_text(TXT_NOT_FOUND)
                await state.clear()
                return
            
            buttons = []
            for entry in entries:
                title = entry.get('title', 'Track')
                url = entry.get('url') or entry.get('webpage_url')
                if url:
                    # Сокращаем название для инлайн кнопки (лимит 64 символа на callback_data целиком!)
                    # Чтобы не выйти за лимиты callback_data, запишем в callback только префикс скачивания mp3
                    # Но url может быть длинным. Безопаснее вставить прямую ссылку в кнопку:
                    buttons.append([InlineKeyboardButton(text=f"🎵 {title[:40]}", callback_data=f"down:mp3:{url}")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await status_msg.delete()
            await message.answer("🎵 Выберите найденный трек:", reply_markup=keyboard)
            
    except Exception as e:
        logging.error(e)
        await status_msg.edit_text(TXT_ERROR)
    
    await state.clear()

@dp.message(F.text.contains("http://") | F.text.contains("https://"))
async def handle_urls(message: Message):
    url = message.text.strip()
    
    if "soundcloud.com" in url:
        # Для SoundCloud сразу качаем mp3
        await download_media(url, "mp3", message)
    elif "youtube.com" in url or "youtu.be" in url or "tiktok.com" in url:
        # Для YT и ТТ предлагаем выбор
        # Внимание: Длина URL в callback_data ограничена. Если URL > 45 символов, это вызовет ошибку Telegram.
        # Поэтому сделаем "умный" фолбек: если ссылка слишком длинная, просто качаем mp4 по дефолту, либо просим выбрать текстом.
        if len(url) < 45:
            await message.answer(TXT_CHOOSE_FORMAT, reply_markup=get_format_keyboard(url))
        else:
            # Если ссылка длинная, качаем MP4 по умолчанию
            await download_media(url, "mp4", message)
    else:
        # Любые другие ссылки пробуем как видео
        await download_media(url, "mp4", message)

@dp.callback_query(F.data.startswith("down:"))
async def process_download_callback(callback: CallbackQuery):
    await callback.answer()
    _, mode, url = callback.data.split(":", 2)
    
    # Удаляем сообщение с кнопками выбора, чтобы пользователь не тыкал повторно
    await callback.message.delete()
    
    if mode == "both":
        await download_media(url, "mp3", callback.message)
        await download_media(url, "mp4", callback.message)
    else:
        await download_media(url, mode, callback.message)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())