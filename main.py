import os
import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv

from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Библиотеки Google
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import google.generativeai as genai
import json

import sqlite3
import time
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ДЛЯ СТАТИСТИКИ ---

DB_DIR = "/app/data" 
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "stats.db")

def init_db():
    # Заменяем "stats.db" на переменную DB_PATH
    conn = sqlite3.connect(DB_PATH) 
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monthly_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month_year TEXT,
            duration_sec INTEGER,
            total_chars INTEGER,
            had_bad_photo INTEGER,
            cost INTEGER
        )
    ''')
    
    try:
        cursor.execute("ALTER TABLE monthly_stats ADD COLUMN cost INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_FOLDER_ID = os.getenv("MAIN_FOLDER_ID")
CLIENT_SECRET_FILE = 'credentials.json' 
#SCOPES = ['https://www.googleapis.com/auth/drive']

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_ID = [int(admin_id.strip()) for admin_id in admin_ids_str.split(",") if admin_id.strip().isdigit()]

def get_done_keyboard(text="✅ Готово, идем дальше"):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_skip_keyboard(yes_text="📸 Загрузить фото", no_text="➡️ Нет / Далее"):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=no_text)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# --- GOOGLE DRIVE AUTH ---
CLIENT_SECRET_FILE = 'credentials.json'
# Добавили права на таблицы
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

def get_google_services():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    drive = build('drive', 'v3', credentials=creds)
    sheets = build('sheets', 'v4', credentials=creds)
    return drive, sheets

drive_service, sheets_service = get_google_services()


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

class OrderFlow(StatesGroup):
    waiting_for_bot_knowledge = State()
    waiting_for_name = State()
    waiting_for_contact = State()
    
    waiting_for_main_chars_count = State()
    waiting_for_total_chars_count = State()
    waiting_for_main_chars_age = State()
    waiting_for_style = State()
    waiting_for_eye_colors = State()

    waiting_for_relative_photo = State()
    waiting_for_relative_caption = State()

    waiting_for_pet_photo = State()
    waiting_for_pet_caption = State()

    waiting_for_toy_photo = State()
    waiting_for_toy_caption = State()
    waiting_for_agreement = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def create_drive_folder(folder_name, parent_id):
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    file = drive_service.files().create(body=file_metadata, fields='id, webViewLink').execute()
    return file.get('id'), file.get('webViewLink')

def get_onboarding_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, я умею пользоваться ботами")],
            [KeyboardButton(text="🤔 Нет, расскажите, как это работает")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_start_after_help_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 Всё понятно, давайте начинать!")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )



def upload_file_to_drive(file_path, file_name, parent_id):
    file_metadata = {'name': file_name, 'parents': [parent_id]}
    media = MediaFileUpload(file_path, resumable=True)
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file.get('id')



def clean_filename(text):
    return re.sub(r'[\\/*?:"<>|]', "", text)

def append_to_sheet(row_data):
    try:
        body = {'values': [row_data]}
        sheets_service.spreadsheets().values().append(
            spreadsheetId=os.getenv("SPREADSHEET_ID"),
            range="A:G", 
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
    except Exception as e:
        logging.error(f"Ошибка записи в Google Таблицу: {e}")
        
        
genai.configure(api_key=GEMINI_API_KEY)

# --- ФУНКИЯ АНАЛИЗА ФОТО (Gemini) ---
async def analyze_photo_quality(file_path: str) -> dict:
    """Анализирует фото через Gemini и возвращает JSON с результатами проверки."""
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        sample_file = genai.upload_file(path=file_path)
        
        prompt = """
        You are a strict photo moderator for an automated system.
        Analyze the attached photo and return the result STRICTLY as a JSON object without any markdown formatting.

        Strict verification rules:
        1. NUMBER OF PEOPLE: Carefully count all the people in the frame (including those in the background). 
        2. CLOTHING: Is the person wearing everyday clothing? If a child is wearing ONLY a diaper, underwear, a swimsuit, or if a person is naked/topless, this is a violation (set to true).
        3. COMPOSITION: Is the full face visible? If it's an extreme close-up (e.g., only one eye or nose taking up the whole frame) or the face is heavily cropped by the edge of the photo, this is a violation (set to false). We need a standard portrait.

        Return the JSON strictly following this template:
        {
        "face_count": <integer, number of people in the photo>,
        is_naked_or_diaper": <true if wearing only a diaper/underwear/swimsuit or naked, otherwise false>,
        "is_full_face_visible": <true if the face is normally visible, false if it's an extreme macro shot or heavily cropped>
        }
        """
        
        response = await asyncio.to_thread(model.generate_content, [sample_file, prompt])
        
        genai.delete_file(sample_file.name)
        
        clean_json = response.text.strip().removeprefix('```json').removesuffix('```').strip()
        result = json.loads(clean_json)
        return result

    except Exception as e:
        logging.error(f"Ошибка анализа Gemini: {e}")
        return {"is_naked_or_diaper": False, "eyes_visible": True, "face_count": 1}

# ================= ХЕНДЛЕРЫ =================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    # Засекаем время старта для будущей статистики
    await state.update_data(start_time=time.time(), had_bad_photo=False)
    
    await message.answer(
        "✨ <b>Добро пожаловать в мастерскую сказок!</b> ✨\n\n"
        "Я — ваш автоматический помощник. Моя задача — аккуратно собрать все фотографии для вашей будущей книги и передать их нашим художникам.\n\n"
        "Скажите, вы уже пользовались Telegram-ботами раньше?",
        reply_markup=get_onboarding_keyboard()
    )
    await state.set_state(OrderFlow.waiting_for_bot_knowledge)


@dp.message(Command("restart"))
async def cmd_restart(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    # Проверяем, дошел ли человек вообще до создания папки
    if 'current_folder_id' not in data:
        await message.answer(
            "⚠️ Вы еще не начали оформление заказа или папка не была создана.\n"
            "Пожалуйста, нажмите /start, чтобы начать с самого начала."
        )
        return

    # Если папка есть, просто откатываем состояние до приема первого фото родственников
    await state.set_state(OrderFlow.waiting_for_relative_photo)
    
    await message.answer(
        "🙌 <b>Ничего страшного, с каждым могло случиться! Все абсолютно под контролем.</b>\n\n"
        "Ваша персональная папка никуда не пропала, и все ответы на вопросы сохранены. Мы просто сбросили процесс загрузки.\n\n"
        "Начинайте загружать фото заново (строго по одному!). Если нужно освежить в памяти правила выбора фото — просто пролистайте чат немного вверх.\n\n"
        "👇 <b>Жду первое фото!</b>",
        reply_markup=ReplyKeyboardRemove() # На всякий случай убираем старые кнопки
    )
    
    # Снова показываем кнопку "Все люди загружены", чтобы она была под рукой
    await message.answer(
        "После каждого фото я спрошу имя. Когда загрузите всех, нажмите кнопку:", 
        reply_markup=get_done_keyboard("✅ Все люди загружены")
    )
    
    # ================= АВАРИЙНОЕ ЗАВЕРШЕНИЕ =================
@dp.message(Command("done"))
async def cmd_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    # Защита: проверяем, начал ли человек вообще заказ
    if 'current_folder_id' not in data:
        await message.answer(
            "⚠️ Вы еще не начали оформление заказа.\n"
            "Пожалуйста, нажмите /start, чтобы начать."
        )
        return

    # Если папка есть — бросаем всё и запускаем финальный расчет!
    await message.answer("✅ <b>Принудительно завершаем оформление заказа...</b>")
    await finish_all(message, state)


@dp.message(OrderFlow.waiting_for_bot_knowledge, F.text == "🤔 Нет, расскажите, как это работает")
async def explain_bot_logic(message: types.Message):
    # Если человек не знает, что такое бот, объясняем очень просто
    await message.answer(
        "💡 <b>Не волнуйтесь, это очень просто!</b>\n\n"
        "Представьте, что я — ваш друг, с которым вы просто переписываетесь в Telegram. Вы точно так же пишите мне в чат, отправляете фото и т.д.\n"
        "Я не умею поддерживать светскую беседу или отвечать на сложные вопросы (я не искусственный интеллект, как ChatGPT).\n\n"
        "<b>Моя работа строится строго по шагам:</b>\n"
        "1️⃣ Я задаю вам конкретный вопрос (например, «Как вас зовут?»).\n"
        "2️⃣ Вы пишете ответ в чат и отправляете его мне. Примерно так же, как вы бы писали любому другому человеку в Telegram\n"
        "3️⃣ Я сохраняю ответ и задаю следующий вопрос или прошу прислать фото.\n\n"
        "⚠️ <i>Самое главное: отвечайте строго на тот вопрос, который я задал, и присылайте фото только тогда, когда я об этом попрошу.</i>\n\n"
        "Готовы начинать? Жмите кнопку ниже!",
        reply_markup=get_start_after_help_keyboard()
    )
    


@dp.message(OrderFlow.waiting_for_bot_knowledge)
async def start_questionnaire(message: types.Message, state: FSMContext):
    await message.answer(
        "Отлично! Тогда давайте приступим к оформлению вашей персональной папки.\n\n"
        "📝 <b>Напишите одним сообщением:</b>\n"
        "<code>Ваше Имя собственное и Фамилию (Имя ребенка)</code>\n\n"
        "<i>Например: Иванов Антон (Миша)</i>",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(OrderFlow.waiting_for_name)


@dp.message(OrderFlow.waiting_for_name, F.text, ~F.text.startswith('/'))
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(client_name_part=message.text)
    await message.answer(
        "👍 <b>Имя принято!</b>\n\n"
        "Теперь укажи контакт для связи.\n"
        "Напиши свой <b>Ник в Telegram</b> (через @) или <b>ваш номер телефона</b>.\n\n"
        "<i>Не волнуйтесь, эти данные никуда не уйдут. Я добавлю это в название папки, чтобы понимать, чей это заказ.</i>"
    )
    await state.set_state(OrderFlow.waiting_for_contact)


@dp.message(OrderFlow.waiting_for_contact, F.text, ~F.text.startswith('/'))
async def process_contact(message: types.Message, state: FSMContext):
    contact = message.text
    data = await state.get_data()
    name_part = data['client_name_part']

    full_folder_name = f"{name_part} {contact}"

    msg = await message.answer(f"Создаю папку '<b>{full_folder_name}</b>'... ⏳")

    try:
        folder_id, folder_link = await asyncio.to_thread(create_drive_folder, full_folder_name, MAIN_FOLDER_ID)
        await state.update_data(current_folder_id=folder_id, folder_link=folder_link, full_name=full_folder_name)

        await msg.edit_text("📂 <b>Папка готова!</b>")

        await message.answer(
            "📝 <b>Перед тем как мы перейдем к фото, ответьте, пожалуйста, на 4 коротких вопроса.</b>\n"
            "Это очень поможет нам в создании вашей книги!\n\n"
            "1️⃣ <b>Сколько главных героев будет в книге?</b>\n"
            "Главным героем считается тот, для кого будет книга. Это может быть ваш ребёнок (дети) или родственники (Дедушка с бабушкой, Брат и т.д.)\n"
            "<i>(Пожалуйста, напишите в чат только цифру)</i>"
        )
        await state.set_state(OrderFlow.waiting_for_main_chars_count)

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка создания папки: {e}")

# --- НОВЫЕ ХЕНДЛЕРЫ ОПРОСА ---

@dp.message(OrderFlow.waiting_for_main_chars_count, F.text)
async def process_main_chars_count(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Пожалуйста, напишите **только цифру** (например: 1, 2 или 3).")
        return
    await state.update_data(main_chars_count=message.text)
    
    await message.answer(
        "Отлично! ✌️\n\n"
        "2️⃣ <b>А сколько всего героев будет в книге?</b>\n"
        "<i>(Включая главных героев. Напишите только цифру в чат)</i>"
    )
    await state.set_state(OrderFlow.waiting_for_total_chars_count)


@dp.message(OrderFlow.waiting_for_total_chars_count, F.text)
async def process_total_chars_count(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Пожалуйста, напишите **только цифру**.")
        return
    await state.update_data(total_chars_count=message.text)
    
    await message.answer(
        "Принято! Идём дальше ⏳\n\n"
        "3️⃣ <b>Какой возраст у главного героя (или героев)?</b>\n"
        "<i>Если главный герой 1 - напишите только цифру в чат</i>\n"
        "<i>Если их несколько - напишите их возраста через запятую (порядок записи не важен). Например: 3, 12</i>"
    )
    await state.set_state(OrderFlow.waiting_for_main_chars_age)


@dp.message(OrderFlow.waiting_for_main_chars_age, F.text)
async def process_main_chars_age(message: types.Message, state: FSMContext):
    if not re.match(r'^[\d,\s]+$', message.text):
        await message.answer("⚠️ Пожалуйста, напишите только цифры (и запятые, если героев несколько).")
        return
    await state.update_data(main_chars_age=message.text)
    
    await message.answer(
        "🎨 <b>И последний вопрос перед загрузкой фото!</b>\n\n"
        "Вы уже ознакомились с нашим PDF-файлом, где представлены стили иллюстраций.\n"
        "Напишите, пожалуйста, одним словом, какой стиль для вашей книги вы выбрали?\n"
        "<i>(Например: Дисней, Натурализм или Реализм)</i>"
    )
    await state.set_state(OrderFlow.waiting_for_style)


@dp.message(OrderFlow.waiting_for_style, F.text, ~F.text.startswith('/'))
async def process_book_style(message: types.Message, state: FSMContext):
    await state.update_data(book_style=message.text)
    data = await state.get_data()

    full_info = data.get('full_name', 'Не указано') 
    age_str = data.get('main_chars_age', '0')
    style = message.text

    # Черновик теперь указывает, что клиент остановился на этапе цвета глаз
    draft_row = [full_info, "ЧЕРНОВИК (на этапе цвета глаз)", "-", "-", "-", style, age_str]
    
    try:
        await asyncio.to_thread(append_to_sheet, draft_row)
    except Exception as e:
        logging.error(f"Ошибка при записи черновика: {e}")

    await message.answer(
        "👀 <b>Важный нюанс: Цвет глаз</b>\n\n"
        "На фотографиях не всегда четко виден цвет глаз, а для сказки это очень важно.\n"
        "Пожалуйста, напишите в одном сообщении прямо сюда мне в чат цвет глаз для всех героев вашей сказки.\n\n"
        "<i>Например: Мама, Бабушка Саша, Сестра Оля — зеленые.</i>\n"
        "<i>Папа, Дядя Сергей — карие.</i>\n"
        "<i>Дедушка Артем, Полина, Тётя Света, Дедушка Володя — серо-голубые.</i>\n"
        "Я сохраню это отдельной заметкой для наших художников 📝"
    )
    
    await state.set_state(OrderFlow.waiting_for_eye_colors)


@dp.message(OrderFlow.waiting_for_eye_colors, F.text, ~F.text.startswith('/'))
async def process_eye_colors(message: types.Message, state: FSMContext):
    eye_info = message.text
    data = await state.get_data()
    folder_id = data.get('current_folder_id')
    
    file_name = "цвет_глаз_героев.txt"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(f"Описание цвета глаз от клиента:\n\n{eye_info}")
    
    try:
        await asyncio.to_thread(upload_file_to_drive, file_name, file_name, folder_id)
        if os.path.exists(file_name): 
            os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при загрузке TXT с глазами: {e}")

    await message.answer(
        "Принято! Заметку для художников сохранил. ✨\n\n"
        "Теперь переходим к самому важному — загрузке фотографий...\n\n"
        "Перед началом еще один важный момент: пожалуйста, присылайте <b>по одному фото за раз</b>. Я не умею обрабатывать альбомы фотографий.\n"
        "Вы отправили мне фото -> Я его проверяю -> Если все OK, то прошу вас написать <b>Кто на фото</b> (например: Мама Оля, Дедушка Валера, Тетя Полина и т.д.)."
        "И в таком формате вы загружаете все остальные фото!\n\n"
        "📸 <b>ВАЖНО: Как выбрать фото?</b>\n\n"
        "Чтобы мы смогли описать внешность максимально точно, пожалуйста, следуйте этим советам:\n\n"
        "1️⃣ <b>Четкость и детали</b>\n"
        "На фото должны быть <b>хорошо видны цвет глаз и волос</b>. Избегайте размытых и темных кадров.\n\n"
        "2️⃣ <b>Крупный план</b>\n"
        "Идеально — портрет или фото по пояс. Ha фото, где человек стоит далеко (например, где-то за столом), сложно разобрать черты лица.\n\n"
        "3️⃣ <b>Количество</b>\n"
        "Можно присылать <b>1-2 фото</b> одного человека c разных ракурсов.\n<b>Однако очень важно понимать один момент:</b> герой будет одинаковым на всех иллюстрациях (прическа, одежда, цвет волос и тд). Поэтому только вам решать, в каком наряде вы бы хотели видеть вашего ребенка и родных на иллюстрациях. Присылать 1 или 2 фото одного человека более чем достаточно😊\nИ тем самым вы сами выбираете облик героя книги, который больше всего хотели бы именно <b>ВЫ</b>\n\n"
        "🔄 <b>АВАРИЙНАЯ КНОПКА:</b>\n"
        "Хоть я и стараюсь не давать вам совершить ошибку, ситуации бывают разные. Если вдруг что-то пошло не так, бот завис или вы запутались — просто отправьте мне команду /restart или выберите её в меню слева внизу.\n\n"
        "🆘 <b>Нужна помощь или есть вопросы?</b>\n"
        "Если бот завис, вы запутались или что-то пошло не так — пишите нашему иллюстратору: @bondtemaa. Он быстро во всем разберется и поможет!\n\n"
        "👇 <b>Теперь можно начинать! Жду первое фото.</b>"
    )

    await message.answer(
        "После каждого фото я спрошу у вас 'Кто это?'. Когда загрузите всех, нажмите кнопку:", 
        reply_markup=get_done_keyboard("✅ Все люди загружены")
    )
    
    await state.set_state(OrderFlow.waiting_for_relative_photo)
    
    # ================= УНИВЕРСАЛЬНЫЕ КНОПКИ ЗАВЕРШЕНИЯ =================

@dp.message(F.text == "✅ Все люди загружены")
async def finish_relatives(message: types.Message, state: FSMContext):
    await message.answer(
        "Отлично, с людьми закончили! 👨‍👩‍👧‍👦\n\n"
        "4️⃣ <b>Есть ли у вас домашние животные?</b> 🐶🐱\n"
        "Если <b>ЕСТЬ</b>: Просто пришлите фото питомца.\n"
        "Если <b>НЕТ</b>: Нажмити кнопку ниже.",
        reply_markup=get_skip_keyboard("📸 Фото питомца", "➡️ Нет животных / Готово")
    )
    await state.set_state(OrderFlow.waiting_for_pet_photo)

@dp.message(F.text.in_(["➡️ Нет животных / Готово", "➡️ Готово, идем к игрушкам"]))
async def finish_pets(message: types.Message, state: FSMContext):
    await message.answer(
        "Принято! 🐾\n\n"
        "5️⃣ <b>Последний вопрос: Любимая игрушка вашего малыша</b> 🧸\n"
        "Если <b>ЕСТЬ</b>: Пришлите её фото.\n"
        "Если <b>НЕТ</b>: Нажимайте кнопку ниже.",
        reply_markup=get_skip_keyboard(no_text="➡️ Нет игрушки / Далее") 
    )
    await state.set_state(OrderFlow.waiting_for_toy_photo)

@dp.message(F.text == "➡️ Нет игрушки / Далее") 
async def skip_toy_and_agreement(message: types.Message, state: FSMContext):
    await show_agreement(message, state)
# ===================================================================

# --- БЛОК РОДСТВЕННИКОВ ---

@dp.message(OrderFlow.waiting_for_relative_photo, F.photo)
async def relative_photo(message: types.Message, state: FSMContext):
    if message.media_group_id:
        data = await state.get_data()

        if data.get("last_error_group") == message.media_group_id:
            return 
    
        await state.update_data(last_error_group=message.media_group_id)
        await message.answer("❌ <b>Пожалуйста, присылайте строго по ОДНОМУ фото за раз!</b>\nЯ не умею обрабатывать альбомы. Выберите одно лучшее фото и отправьте его.")
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    temp_name = f"temp_{photo.file_id[:10]}.jpg"
    await bot.download_file(file.file_path, temp_name)
    
    msg_thinking = await message.reply("👀 <i>Внимательно смотрю на фотографию...</i>")
    
    check_result = await analyze_photo_quality(temp_name)
    
    await msg_thinking.delete()

    if check_result.get("face_count", 0) > 1:
        os.remove(temp_name) 
        await state.update_data(had_bad_photo=True) 
        await message.answer(
            "❌ <b>Ой, на фото больше одного человека!</b>\n\n"
            "Нейросеть запутается, кого именно из вас рисовать. Пожалуйста, пришлите фото, где ваш герой находится в кадре один."
        )
        return

    # 2. Проверка на памперсы и голые торсы
    if check_result.get("is_naked_or_diaper") is True:
        os.remove(temp_name)
        await state.update_data(had_bad_photo=True)
        await message.answer(
            "❌ <b>Фото не подходит из-за одежды (или её отсутствия).</b>\n\n"
            "Наши фильтры безопасности строго блокируют фото малышей в подгузниках/памперсах, купальниках или без одежды. Пожалуйста, пришлите фото в обычной повседневной одежде."
        )
        return

    # 3. Проверка на "только глаза" и обрезанные лица
    if check_result.get("is_full_face_visible") is False:
        os.remove(temp_name)
        await state.update_data(had_bad_photo=True)
        await message.answer(
            "❌ <b>Слишком крупный план или лицо обрезано!</b>\n\n"
            "Нам нужно видеть овал лица целиком, чтобы художник уловил все черты. Пожалуйста, не присылайте макро-фото глаз или обрезанные селфи. Идеально подойдет обычный портрет или фото по пояс."
        )
        return

    # Если все проверки пройдены:
    await state.update_data(temp_photo_path=temp_name)
    await message.reply(
        "📸 <b>Фото прошло проверку и принято!</b>\n"
        "Напишити пожалуйста: <b>Кто это?</b> (например: Мама Оля, Дедушка Валера, Тетя Полина и т.д.)", 
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(OrderFlow.waiting_for_relative_caption)



@dp.message(OrderFlow.waiting_for_relative_caption, F.text, ~F.text.startswith('/'))
async def relative_caption(message: types.Message, state: FSMContext):
    name = clean_filename(message.text)
    filename = f"{name}.jpg"

    data = await state.get_data()
    await message.answer(f"Загружаю '<b>{filename}</b>'... ⏳")

    try:
        await asyncio.to_thread(upload_file_to_drive, data['temp_photo_path'], filename, data['current_folder_id'])
        if os.path.exists(data['temp_photo_path']): os.remove(data['temp_photo_path'])  # noqa: E701

        await message.answer(
            f"✅ <b>{name}</b> сохранен!\n"
            "Жду от вас следующее фото или (если вы завершили с загрузкой людей) - нажимайте кнопку.",
            reply_markup=get_done_keyboard("✅ Все люди загружены")
        )
        await state.set_state(OrderFlow.waiting_for_relative_photo)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# --- БЛОК ПИТОМЦЕВ ---

@dp.message(OrderFlow.waiting_for_pet_photo, F.photo)
async def pet_photo(message: types.Message, state: FSMContext):
    if message.media_group_id:
        data = await state.get_data()
        # Если мы уже ругались на этот альбом, просто молча игнорируем остальные фотки
        if data.get("last_error_group") == message.media_group_id:
            return 
        
        # Запоминаем ID этого альбома и выдаем ошибку ОДИН раз
        await state.update_data(last_error_group=message.media_group_id)
        await message.answer("❌ <b>Пожалуйста, присылайте строго по ОДНОМУ фото за раз!</b>\nЯ не умею обрабатывать альбомы. Выберите одно лучшее фото и отправьте его.")
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    temp_name = f"temp_pet_{photo.file_id[:10]}.jpg"
    await bot.download_file(file.file_path, temp_name)
    await state.update_data(temp_photo_path=temp_name)

    await message.reply("🐾 <b>Милаха!</b> Как зовут? (и кто это?)", reply_markup=ReplyKeyboardRemove())
    await state.set_state(OrderFlow.waiting_for_pet_caption)


@dp.message(OrderFlow.waiting_for_pet_caption, F.text, ~F.text.startswith('/'))
async def pet_caption(message: types.Message, state: FSMContext):
    name = clean_filename(message.text)
    filename = f"Питомец {name}.jpg"

    data = await state.get_data()
    await message.answer("Сохраняю питомца... ⏳")

    try:
        await asyncio.to_thread(upload_file_to_drive, data['temp_photo_path'], filename, data['current_folder_id'])
        if os.path.exists(data['temp_photo_path']): os.remove(data['temp_photo_path'])  # noqa: E701

        await message.answer(
            f"✅ <b>{name}</b> в домике!\n"
            "Есть еще животные? Присылайте фото или нажимайте кнопку.",
            reply_markup=get_skip_keyboard(no_text="➡️ Готово, идем к игрушкам")
        )
        await state.set_state(OrderFlow.waiting_for_pet_photo)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# --- БЛОК ИГРУШКИ ---


@dp.message(OrderFlow.waiting_for_toy_photo, F.photo)
async def toy_photo(message: types.Message, state: FSMContext):
    if message.media_group_id:
        data = await state.get_data()
        if data.get("last_error_group") == message.media_group_id:
            return 
        
        await state.update_data(last_error_group=message.media_group_id)
        await message.answer("❌ <b>Пожалуйста, присылайте строго по ОДНОМУ фото за раз!</b>\nЯ не умею обрабатывать альбомы. Выберите одно лучшее фото и отправьте его.")
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    temp_name = f"temp_toy_{photo.file_id[:10]}.jpg"
    await bot.download_file(file.file_path, temp_name)
    await state.update_data(temp_photo_path=temp_name)

    await message.reply("🧸 <b>Вижу!</b> Как называется игрушка?", reply_markup=ReplyKeyboardRemove())
    await state.set_state(OrderFlow.waiting_for_toy_caption)


@dp.message(OrderFlow.waiting_for_toy_caption, F.text, ~F.text.startswith('/'))
async def toy_caption(message: types.Message, state: FSMContext):
    name = clean_filename(message.text)
    filename = f"Игрушка {name}.jpg"

    data = await state.get_data()
    await message.answer("Загружаю игрушку... ⏳")

    try:
        await asyncio.to_thread(upload_file_to_drive, data['temp_photo_path'], filename, data['current_folder_id'])
        if os.path.exists(data['temp_photo_path']): os.remove(data['temp_photo_path'])  # noqa: E701
        await show_agreement(message, state) 
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ================= КАПКАНЫ ДЛЯ ОШИБОК =================
# Эти хендлеры сработают ТОЛЬКО если пользователь прислал не тот тип данных

@dp.message(OrderFlow.waiting_for_name, ~F.text)
@dp.message(OrderFlow.waiting_for_contact, ~F.text)
@dp.message(OrderFlow.waiting_for_style, ~F.text)
@dp.message(OrderFlow.waiting_for_relative_caption, ~F.text)
@dp.message(OrderFlow.waiting_for_pet_caption, ~F.text)
@dp.message(OrderFlow.waiting_for_toy_caption, ~F.text)
async def catch_not_text(message: types.Message):
    await message.answer("⚠️ <b>Пожалуйста, напишите ответ текстом!</b>\nЯ сейчас жду от вас текстовое сообщение, а не картинку или стикер.")

ALL_DONE_TEXTS = [
    "✅ Все люди загружены", 
    "➡️ Нет животных / Готово", 
    "➡️ Готово, идем к игрушкам", 
    "➡️ Нет игрушки / Далее", 
    "✅ Я соглашаюсь / Завершить заказ"
]

@dp.message(OrderFlow.waiting_for_relative_photo, ~F.photo, ~F.text.in_(ALL_DONE_TEXTS))
@dp.message(OrderFlow.waiting_for_pet_photo, ~F.photo, ~F.text.in_(ALL_DONE_TEXTS))
@dp.message(OrderFlow.waiting_for_toy_photo, ~F.photo, ~F.text.in_(ALL_DONE_TEXTS))
async def catch_not_photo(message: types.Message):
    await message.answer(
        "⚠️ <b>Я жду от вас фотографию! 📸</b>\n"
        "Пожалуйста, прикрепите изображение как обычное фото. "
        "Если вы хотите пропустить этот шаг — нажмите соответствующую кнопку внизу экрана."
    )
    
async def show_agreement(message: types.Message, state: FSMContext):
    await message.answer(
        "❗️ <b>Финальный шаг: Пользовательское соглашение</b> ❗️\n\n"
        "Перед тем как мы передадим материалы иллюстратору, пожалуйста, подтвердите несколько важных моментов:\n\n"
        "✅ <b>Внешность и одежда:</b> Я понимаю, что образы героев переносятся с фотографий максимально точно. Одежда на иллюстрациях будет совпадать с той, что на присланных мной фото <i>(исключение: если герой на фото в теплой куртке/пальто, мы бережно адаптируем его наряд под сказочный сюжет)</i>.\n"
        "✅ <b>Цвет глаз:</b> Я проверил(а) и подтверждаю, что цвет глаз всех героев указан абсолютно верно.\n"
        "✅ <b>Качество:</b> Я тщательно отобрал(а) фотографии и уверен(а) в своем выборе.\n\n"
        "Если вы со всем согласны, нажмите кнопку ниже, чтобы завершить заказ и отправить материалы в работу!",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="✅ Я соглашаюсь / Завершить заказ")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(OrderFlow.waiting_for_agreement)
    
@dp.message(OrderFlow.waiting_for_agreement, F.text == "✅ Я соглашаюсь / Завершить заказ")
async def final_agreement_accept(message: types.Message, state: FSMContext):
    await finish_all(message, state)

# --- АДМИНСКИЕ КОМАНДЫ ---

async def finish_all(message: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get('folder_link', 'нет ссылки')
    full_name_str = data.get('full_name', 'Клиент')
    style = data.get('book_style', 'Не указан')
    
    age_str = data.get('main_chars_age', '0')
    try:
        ages = [int(x) for x in re.findall(r'\d+', age_str)]
        min_age = min(ages) if ages else 0 
        
        main_count = int(data.get('main_chars_count', 1))
        total_count = int(data.get('total_chars_count', 1))
    except ValueError:
        min_age, main_count, total_count = 0, 1, 1

    
    match = re.search(r"^(.*?)\s*\((.*?)\)\s*(.*)$", full_name_str)
    if match:
        customer_name = f"{match.group(1).strip()} {match.group(3).strip()}" # Иванов Иван @nik
        child_name = match.group(2).strip() # Миша
    else:
        customer_name = full_name_str
        child_name = "Не указано"

    # 3. МАТЕМАТИКА: Сложность заказа
    complexity = 0
    complexity += 2 if min_age <= 3 else 3
    complexity += 1 if main_count == 1 else 3
    
    if total_count <= 7:
        complexity += 2
    elif 8 <= total_count <= 12:
        complexity += 3
    else:
        complexity += 4
        
    complexity_str = f"{complexity} ⭐"

    # 4. МАТЕМАТИКА: Стоимость книги
    base_cost = 60 if total_count > 7 else 50
    
    if main_count > 1:
        cost = base_cost + ((main_count - 1) * (base_cost * 0.5))
    else:
        cost = base_cost
        
    if total_count > 12:
        cost += 20
        
    cost_str = f"{int(cost)} BYN"

    # 5. ОТПРАВЛЯЕМ ДАННЫЕ В ТАБЛИЦУ
    row_data = [customer_name, child_name, total_count, complexity_str, cost_str, style, age_str]
    await asyncio.to_thread(append_to_sheet, row_data)

    # 6. ОТПРАВЛЯЕМ СООБЩЕНИЕ КЛИЕНТУ
    await message.answer(
        "🎉 <b>Спасибо! Анкета принята.</b>\n\n"
        "Мы получили все фото и уже начинаем работу над вашей книгой! ✨\n",
        reply_markup=ReplyKeyboardRemove()
    )

    # 7. ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ АДМИНАМ
    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🔔 <b>НОВЫЙ ЗАКАЗ!</b>\n\n"
                    f"👤 Заказчик: <b>{customer_name}</b>\n"
                    f"👶 Ребенок: <b>{child_name}</b>\n"
                    f"🎂 Возраст: <b>{age_str}</b>\n"
                    f"👥 Всего героев: <b>{total_count}</b>\n"
                    f"📊 Сложность: <b>{complexity_str}</b>\n"
                    f"💰 Расчетная сумма: <b>{cost_str}</b>\n\n"
                    f"📂 Папка: <a href='{link}'>Открыть на Диске</a>"
                ),
                disable_web_page_preview=True
            )
        except Exception as e:
            logging.error(f"Не удалось отправить админу {admin_id}: {e}")
            
    # --- СБОР ДОЛГОСРОЧНОЙ СТАТИСТИКИ ---
    start_time = data.get('start_time', time.time())
    duration_sec = int(time.time() - start_time)
    had_bad_photo = 1 if data.get('had_bad_photo') else 0
    current_month_year = datetime.now().strftime("%m-%Y")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO monthly_stats (month_year, duration_sec, total_chars, had_bad_photo, cost) VALUES (?, ?, ?, ?, ?)",
            (current_month_year, duration_sec, total_count, had_bad_photo, int(cost))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка записи статистики в БД: {e}")

    await state.clear()


# --- АДМИНСКАЯ КОМАНДА: РУЧНОЕ СОЗДАНИЕ ЗАКАЗА (ДЛЯ WHATSAPP) ---
@dp.message(Command("manual"))
async def cmd_manual(message: types.Message, command: CommandObject, state: FSMContext):
    # 1. Проверка на админа
    if message.from_user.id not in (ADMIN_ID if isinstance(ADMIN_ID, list) else [ADMIN_ID]):
        return

    # 2. Проверка аргументов
    if not command.args:
        await message.answer(
            "⚠️ <b>Ошибка ввода.</b>\n"
            "Используй: <code>/manual Имя Фамилия (Инфо)</code>\n"
            "Пример: <code>/manual Иванов Иван (из WhatsApp)</code>"
        )
        return

    folder_name = command.args.strip()

    # 3. Сразу создаем папку
    msg = await message.answer(f"🛠 <b>Ручной режим:</b> Создаю папку '<b>{folder_name}</b>'... ⏳")

    try:
        # Создаем папку на Диске
        folder_id, folder_link = await asyncio.to_thread(create_drive_folder, folder_name, MAIN_FOLDER_ID)

        # Сохраняем данные в состояние, как будто клиент сам все прошел
        await state.update_data(
            current_folder_id=folder_id,
            folder_link=folder_link,
            full_name=folder_name
        )

        await msg.edit_text(
            f"📂 <b>Папка готова!</b>\n"
            f"🔗 <a href='{folder_link}'>Ссылка</a>\n\n"
            "👇 <b>Теперь пересылай фото от менеджера.</b>\n"
            "Пересылай ПО ОДНОМУ, подписывай как обычно."
        )

        # Сразу ставим состояние ожидания фото родственника
        await state.set_state(OrderFlow.waiting_for_relative_photo)

        # Показываем кнопку завершения
        await message.answer("Погнали!", reply_markup=get_done_keyboard("✅ Все люди загружены"))

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")
    
    
async def send_monthly_stats():
    now = datetime.now()
    if now.month == 1:
        prev_month = 12
        prev_year = now.year - 1
    else:
        prev_month = now.month - 1
        prev_year = now.year
        
    target_month_str = f"{prev_month:02d}-{prev_year}"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # ДОБАВИЛИ cost В ВЫБОРКУ:
    cursor.execute("SELECT duration_sec, total_chars, had_bad_photo, cost FROM monthly_stats WHERE month_year = ?", (target_month_str,))
    records = cursor.fetchall()
    conn.close()

    if not records:
        return 

    total_orders = len(records)
    sum_duration = sum(row[0] for row in records)
    sum_chars = sum(row[1] for row in records)
    sum_bad_photos = sum(row[2] for row in records)
    
    # СЧИТАЕМ ВЫРУЧКУ (row[3] - это наша колонка cost. Защита от старых записей без цены)
    total_revenue = sum((row[3] if row[3] is not None else 0) for row in records)

    avg_duration_sec = sum_duration / total_orders
    avg_mins = int(avg_duration_sec // 60)
    avg_chars = int(sum_chars / total_orders + 0.5) 
    bad_photo_percent = int((sum_bad_photos / total_orders) * 100)

    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"📈 <b>СТАТИСТИКА ЗА ПРОШЛЫЙ МЕСЯЦ</b> ({target_month_str})\n\n"
                    f"📦 Всего заказов: <b>{total_orders}</b>\n"
                    f"💵 Выручка: <b>{total_revenue} BYN</b>\n"
                    f"⏱ Среднее время заполнения: <b>~{avg_mins} мин.</b>\n"
                    f"👥 Среднее кол-во героев: <b>{avg_chars}</b>\n"
                    f"⚠️ Доля клиентов с плохими фото: <b>{bad_photo_percent}%</b>\n\n"
                    f"<i>Продолжаем работать! 🚀</i>"
                )
            )
        except Exception as e:
            logging.error(f"Ошибка отправки статистики: {e}")

async def main():
    # Настраиваем планировщик
    scheduler = AsyncIOScheduler()
    # Запускаем 2-го числа каждого месяца в 12:00 дня
    scheduler.add_job(send_monthly_stats, 'cron', day=2, hour=12, minute=0)
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())