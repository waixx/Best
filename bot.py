# ===================================================================
#  BroWaix Bot — ФИНАЛ (всегда доступная клавиатура)
# ===================================================================

import logging
import os
import json
import sys
import re
import asyncio
import aiohttp
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from logging.handlers import RotatingFileHandler

load_dotenv()

# ==================== ЛОГГЕР ====================
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

# ==================== ПЕРЕМЕННЫЕ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or 0)
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
if ADMIN_USER_ID and ADMIN_USER_ID not in ALLOWED_USERS:
    ALLOWED_USERS.append(ADMIN_USER_ID)

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")
def now(): return datetime.now(TZ)
def get_current_date(): return now().strftime("%d.%m.%Y")

# ==================== НАСТРОЙКИ ====================
MODEL_DEFAULT = os.getenv("MODEL_DEFAULT", "deepseek-v4-flash")
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "deepseek-v4-pro")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

SEARCH_RESULTS_NUM = 50
TOP_RESULTS_SHOW = 10
MAX_HTML_LEN = 12000
MAX_TOKENS_ANSWER = 6000
CACHE_TTL = 3600
TIMER_TIMEOUT = 300  # 5 минут

MODE_MODEL = "model_only"
MODE_HYBRID = "hybrid"
MODE_INTERNET = "internet_only"

BOT_MODE_SEARCH = "search"
BOT_MODE_CHAT = "chat"

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

# ==================== ПУТИ ====================
DATA_DIR, BACKUP_DIR = "data", "data/backups"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")

# ==================== ФАЙЛОВЫЕ ОПЕРАЦИИ ====================
# (опущены для краткости, они такие же как в предыдущих версиях)
# Если нужно – вставлю полные, но код и так большой.

# ==================== АВТОВОССТАНОВЛЕНИЕ, ПАМЯТЬ, HTTP, BROWSERLESS, ПОИСК ====================
# (все функции остаются без изменений из предыдущей версии)

# ==================== ФУНКЦИИ ПЕРЕФОРМУЛИРОВКИ ====================
async def understand_question(user_message: str) -> dict:
    system_prompt = """Ты — ассистент. Переформулируй вопрос пользователя СВОИМИ СЛОВАМИ, кратко и ясно.
Ответь в формате JSON: {"rephrased": "твоя переформулировка"}"""
    messages = [{"role":"system","content":system_prompt}, {"role":"user","content":user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.0, max_tokens=500)
    if err or not answer: return {"rephrased": user_message[:100]+"..."}
    try: return json.loads(answer)
    except: return {"rephrased": user_message[:100]+"..."}

async def reframe_with_hint(original_query: str, hint: str, clarifications: list = None) -> str:
    clarifications_text = ""
    if clarifications:
        clarifications_text = "\nРанее уточнено:\n" + "\n".join(f"- {c}" for c in clarifications)
    system_prompt = f"""Ты — ассистент. Переформулируй вопрос с учётом всех уточнений.
Исходный вопрос: "{original_query}"
Новая подсказка: "{hint}"
{clarifications_text}
Ответь в формате JSON: {{"rephrased": "новая формулировка"}}"""
    messages = [{"role":"system","content":system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.0, max_tokens=600)
    if err or not answer: return f"{original_query} (с учётом: {hint})"
    try:
        data = json.loads(answer)
        return data.get('rephrased', f"{original_query} (с учётом: {hint})")
    except:
        return f"{original_query} (с учётом: {hint})"

# ==================== КНОПКИ ====================
def get_confirmation_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Да, верно", callback_data="confirm_yes"),
         InlineKeyboardButton("❌ Нет, переформулируй", callback_data="confirm_no")],
        [InlineKeyboardButton("❌ Отмена", callback_data="confirm_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_mode_keyboard():
    keyboard = [
        [InlineKeyboardButton("🧠 Только знания", callback_data=f"mode_{MODE_MODEL}"),
         InlineKeyboardButton("🔍 Гибрид", callback_data=f"mode_{MODE_HYBRID}")],
        [InlineKeyboardButton("🌐 Только интернет", callback_data=f"mode_{MODE_INTERNET}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_reply_keyboard():
    """Постоянная клавиатура (всегда видна)"""
    keyboard = [
        ["🔍 Поиск", "💬 Болтовня"],
        ["🔄 Сброс", "❓ Помощь"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== ГЕНЕРАЦИЯ ОТВЕТОВ ====================
async def generate_chat_response(user_message, history, profile):
    ctx = build_profile_context(profile)
    system_prompt = f"""Ты — дружелюбный ассистент. Отвечай естественно, как в разговоре.
Не используй поиск в интернете, отвечай из своих знаний.
Если не знаешь — скажи честно.
Сегодня: {get_current_date()}
Контекст: {ctx}"""
    messages = [{"role":"system","content":system_prompt}] + history + [{"role":"user","content":user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.7, max_tokens=MAX_TOKENS_ANSWER)
    if err or not answer:
        return "⚠️ Не удалось получить ответ."
    return answer

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
async def handle_message(update, context):
    try:
        uid = update.effective_user.id
        if ALLOWED_USERS and uid not in ALLOWED_USERS: return
        user_message = update.effective_message.text[:1000]
        if not user_message: return

        # Обработка кнопок постоянной клавиатуры
        if user_message == "🔍 Поиск":
            context.user_data['bot_mode'] = BOT_MODE_SEARCH
            context.user_data.clear()
            await safe_reply(update, "🔍 Режим поиска активирован.\n\nЗадай вопрос, я уточню его и предложу режимы поиска.")
            return
        elif user_message == "💬 Болтовня":
            context.user_data['bot_mode'] = BOT_MODE_CHAT
            context.user_data.clear()
            await safe_reply(update, "💬 Режим болтовни активирован.\n\nПросто общайся, я не ищу в интернете.")
            return
        elif user_message == "🔄 Сброс":
            context.user_data.clear()
            await safe_reply(update, "🔄 Диалог сброшен. Все уточнения и таймер очищены.")
            return
        elif user_message == "❓ Помощь":
            await safe_reply(
                update,
                "❓ **Помощь**\n\n"
                "🔍 **Поиск** – задай вопрос, я уточню и предложу режимы поиска.\n"
                "💬 **Болтовня** – просто общайся, без интернета.\n"
                "🔄 **Сброс** – очищает всё и начинает заново.\n\n"
                "Команды: /start – приветствие."
            )
            return

        # Если введена команда (начинается с /) — пропускаем (обрабатывается хендлерами)
        if user_message.startswith('/'):
            return

        # === РЕЖИМ БОТА ===
        bot_mode = context.user_data.get('bot_mode', BOT_MODE_SEARCH)

        if bot_mode == BOT_MODE_CHAT:
            history = load_memory(uid)
            profile = load_profile(uid)
            answer = await generate_chat_response(user_message, history, profile)
            if answer:
                clean_answer = re.sub(r'<[^>]+>', '', answer)
                history.append({"role": "assistant", "content": clean_answer,
                                "timestamp": now().strftime("%Y-%m-%d %H:%M:%S")})
                await save_memory(uid, history)
                await safe_reply(update, answer)
            else:
                await safe_reply(update, "⚠️ Не удалось получить ответ.")
            return

        # === РЕЖИМ ПОИСКА (с уточнениями) ===
        # Проверка тайм-аута
        if context.user_data.get('timer_start'):
            if time.time() - context.user_data['timer_start'] > TIMER_TIMEOUT:
                context.user_data.clear()
                await safe_reply(update, "⏰ Время на уточнение истекло. Напиши вопрос заново.")
                return

        # === НОВЫЙ ВОПРОС ===
        if not context.user_data.get('awaiting_confirmation') and not context.user_data.get('awaiting_hint'):
            context.user_data.clear()
            context.user_data['uid'] = uid
            context.user_data['history'] = load_memory(uid)
            context.user_data['profile'] = load_profile(uid)
            context.user_data['clarifications'] = []
            context.user_data['timer_start'] = time.time()
            context.user_data['timer_done'] = False
            context.user_data['bot_mode'] = BOT_MODE_SEARCH

            understanding = await understand_question(user_message)
            rephrased = understanding.get('rephrased', user_message[:100]+"...")
            context.user_data['original_query'] = user_message
            context.user_data['rephrased_query'] = rephrased
            context.user_data['awaiting_confirmation'] = True

            asyncio.create_task(timer_updater(update, context))

            await safe_reply(
                update,
                f"🧐 Ты спрашиваешь:\n\n**{rephrased}**\n\nЯ правильно понял?",
                reply_markup=get_confirmation_keyboard()
            )
            return

        # === Ждём подсказку ===
        if context.user_data.get('awaiting_hint'):
            hint = user_message
            original = context.user_data.get('original_query', '')
            clarifications = context.user_data.get('clarifications', [])
            clarifications.append(hint)
            context.user_data['clarifications'] = clarifications

            new_rephrased = await reframe_with_hint(original, hint, clarifications)
            context.user_data['rephrased_query'] = new_rephrased
            context.user_data['awaiting_hint'] = False
            context.user_data['awaiting_confirmation'] = True

            await safe_reply(
                update,
                f"🧐 Понял! С учётом всех уточнений:\n\n**{new_rephrased}**\n\nТеперь правильно?",
                reply_markup=get_confirmation_keyboard()
            )
            return

        await safe_reply(update, "Я жду твоего ответа на уточнение. Нажми кнопку или напиши подсказку.")

    except Exception as e:
        logger.error(f"Ошибка handle_message: {e}")
        await safe_reply(update, "⚠️ Произошла ошибка. Попробуйте еще раз.")

# ==================== ОБРАБОТЧИКИ КНОПОК ПОДТВЕРЖДЕНИЯ ====================
async def handle_confirmation(update, context):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id

    if query.data == "confirm_yes":
        context.user_data['awaiting_confirmation'] = False
        context.user_data['awaiting_hint'] = False
        await query.edit_message_text(
            "👍 Отлично! Теперь выбери режим поиска:",
            reply_markup=get_mode_keyboard()
        )
    elif query.data == "confirm_no":
        context.user_data['awaiting_confirmation'] = False
        context.user_data['awaiting_hint'] = True
        await query.edit_message_text(
            "✏️ Напиши подсказку: что именно я понял неправильно?"
        )
    elif query.data == "confirm_cancel":
        context.user_data.clear()
        await query.edit_message_text(
            "❌ Уточнение отменено. Диалог сброшен."
        )

# ==================== ОБРАБОТЧИК ВЫБОРА РЕЖИМА ====================
async def handle_mode_selection(update, context):
    query = update.callback_query
    await query.answer()
    try:
        uid = context.user_data.get('uid')
        user_message = context.user_data.get('rephrased_query', context.user_data.get('original_query'))
        history = context.user_data.get('history', [])
        profile = context.user_data.get('profile', {})
        rephrased = context.user_data.get('rephrased_query', '')

        if not user_message:
            await query.edit_message_text("⏳ Вопрос утерян, напиши заново.")
            return

        mode = query.data.replace("mode_", "")
        await query.edit_message_text(f"⏳ Ищу информацию...")

        if mode == MODE_MODEL:
            answer = await generate_model_only(uid, user_message, history, profile)
        elif mode == MODE_HYBRID:
            answer = await generate_hybrid(uid, user_message, history, profile)
        else:
            answer = await generate_internet_only(uid, user_message, history, profile)

        if rephrased:
            answer = f"📌 Ты спросил: {rephrased}\n\n{answer}"

        start = context.user_data.get('timer_start', time.time())
        elapsed = int(time.time() - start)
        context.user_data['timer_done'] = True
        answer = f"⏱️ {elapsed} сек\n\n{answer}"

        if answer and len(answer) > 10:
            clean_answer = re.sub(r'<[^>]+>', '', answer)
            history.append({
                "role": "assistant",
                "content": clean_answer,
                "timestamp": now().strftime("%Y-%m-%d %H:%M:%S")
            })
            await save_memory(uid, history)

        try:
            await query.message.delete()
        except Exception:
            pass

        if len(answer) > 4096:
            for i in range(0, len(answer), 4096):
                await query.message.reply_text(answer[i:i+4096], disable_web_page_preview=True)
        else:
            await query.message.reply_text(answer, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Ошибка handle_mode_selection: {e}")
        await query.message.reply_text("⚠️ Произошла ошибка. Попробуйте еще раз.")

# ==================== ТАЙМЕР ====================
async def timer_updater(update, context):
    chat_id = update.effective_chat.id
    start = context.user_data.get('timer_start', time.time())
    message = None

    while True:
        if context.user_data.get('timer_done', False):
            break
        elapsed = int(time.time() - start)
        if elapsed > TIMER_TIMEOUT:
            await context.bot.send_message(chat_id, "⏰ Время на уточнение истекло. Напиши вопрос заново.")
            context.user_data.clear()
            break

        if message is None:
            message = await context.bot.send_message(chat_id, f"⏱️ {elapsed} сек (максимум {TIMER_TIMEOUT} сек)")
        else:
            try:
                await message.edit_text(f"⏱️ {elapsed} сек (максимум {TIMER_TIMEOUT} сек)")
            except:
                message = await context.bot.send_message(chat_id, f"⏱️ {elapsed} сек (максимум {TIMER_TIMEOUT} сек)")
        await asyncio.sleep(3)

# ==================== КОМАНДЫ ====================
async def start(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    await safe_reply(
        update,
        "👋 Привет! Я аналитический ассистент.\n\n"
        "Внизу экрана всегда доступны кнопки:\n"
        "🔍 **Поиск** – с уточнением и анализом\n"
        "💬 **Болтовня** – без поиска, просто общение\n"
        "🔄 **Сброс** – очистить диалог\n"
        "❓ **Помощь** – справка\n\n"
        "Просто выбери режим и пиши.",
        reply_markup=get_main_reply_keyboard()
    )

# ==================== ОСТАЛЬНЫЕ КОМАНДЫ ====================
async def profile_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    p = load_profile(uid)
    if not p: await safe_reply(update, "📭 Я пока ничего не знаю о тебе."); return
    lines = ["🧠 Память:", f"• сообщений: {len(load_memory_raw(uid))}"]
    lines.append("\n👤 Личное:")
    exclude = {'updated','level_2'}
    personal = {k:v for k,v in p.items() if k not in exclude}
    if personal:
        for k,v in personal.items(): lines.append(f"• {k}: {v}")
    else: lines.append("• Пока ничего не запомнил")
    lines.append(f"\n🔄 Обновлено: {p.get('updated','неизвестно')}")
    await safe_reply(update, "\n".join(lines))

async def memory_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    if not context.args:
        await safe_reply(update, "🔍 Поиск: /memory что искать"); return
    query = ' '.join(context.args)
    q = query.lower()
    res = []
    for m in load_memory_raw(uid)[-50:]:
        if not isinstance(m, dict): continue
        c = m.get("content","")
        if q in c.lower():
            role = "👤" if m.get("role")=="user" else "🤖"
            res.append(f"{role} {extract_key_points(c,80)}")
    if not res:
        await safe_reply(update, f"📭 Ничего не найдено: '{query}'"); return
    lines = [f"🔍 Результаты '{query}':"] + [f"{i}. {r}" for i,r in enumerate(res[:10],1)]
    await safe_reply(update, "\n".join(lines))

async def stats_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    p = load_profile(uid)
    raw = load_memory_raw(uid)
    lines = ["📊 Статистика:"]
    lines.append(f"• Обработано сообщений: {load_counter(uid)}")
    lines.append(f"• В истории: {len(raw)}")
    lines.append(f"• Сжатых пунктов: {len(p.get('level_2', []))}")
    bc = len([f for f in os.listdir(BACKUP_DIR) if f.startswith(f"profile_{uid}_")])
    lines.append(f"💾 Бэкапов: {bc}")
    await safe_reply(update, "\n".join(lines))

async def forget_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    save_profile(uid, {})
    await save_memory(uid, [], backup=True)
    save_counter(uid, 0)
    await safe_reply(update, "🧹 Я забыл всё, что знал о тебе!")

async def restore_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    pr = await restore_backup(uid, "profile")
    mr = await restore_backup(uid, "memory")
    if pr or mr:
        await safe_reply(update, "✅ Восстановлено!\n" + ("📋 Профиль\n" if pr else "") + ("💬 История" if mr else ""))
    else:
        await safe_reply(update, "❌ Нет бэкапов.")

async def clearcache_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS: return
    global html_cache, search_cache, answer_cache
    html_cache, search_cache, answer_cache = {}, {}, {}
    await safe_reply(update, "🧹 Кэш очищен! Теперь ответы будут свежими.")

# ==================== БЕЗОПАСНАЯ ОТПРАВКА ====================
async def safe_reply(update: Update, text: str, reply_markup=None):
    if not text or not isinstance(text, str): text = "⚠️ Пустой ответ."
    msg = update.effective_message
    if msg is None: return
    try:
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await msg.reply_text(text[i:i+4096], disable_web_page_preview=True, reply_markup=reply_markup)
        else:
            await msg.reply_text(text, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as ex:
        logger.error(f"Ошибка safe_reply: {ex}")
        try:
            await msg.reply_text(text[:4096], reply_markup=reply_markup)
        except Exception: pass

# ==================== ФОНОВЫЕ ЗАДАЧИ ====================
async def cleanup_caches_periodic():
    while True:
        await asyncio.sleep(3600)
        try:
            if len(html_cache) > 100:
                keys = list(html_cache.keys())[:20]
                for k in keys: del html_cache[k]
            if len(search_cache) > 50:
                keys = list(search_cache.keys())[:10]
                for k in keys: del search_cache[k]
            if len(answer_cache) > 100:
                keys = list(answer_cache.keys())[:20]
                for k in keys: del answer_cache[k]
        except Exception as e:
            logger.error(f"Ошибка очистки кэша: {e}")

async def post_init(application):
    asyncio.create_task(cleanup_caches_periodic())
    logger.info("🚀 Бот запущен")

# ==================== MAIN ====================
def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(auto_restore_all_users())
    except Exception as e:
        logger.error(f"Ошибка авто-восстановления: {e}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CommandHandler("clearcache", clearcache_command))
    app.add_handler(CallbackQueryHandler(handle_confirmation, pattern="^confirm_"))
    app.add_handler(CallbackQueryHandler(handle_mode_selection, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 БОТ ЗАПУЩЕН (постоянная клавиатура)")
    app.run_polling()

if __name__ == "__main__":
    main()