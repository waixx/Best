# ===================================================================
#  BROWAIX BOT — ИСПРАВЛЕННАЯ ВЕРСИЯ
#  Исправлен синтаксис, всё работает
# ===================================================================

import logging
import os
import json
import sys
import re
import asyncio
import aiohttp
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

load_dotenv()

# ==================== ЛОГГЕР ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")

ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(x.strip()) for x in ALLOWED_USERS_RAW.split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

MODEL_DEFAULT = os.getenv("MODEL_DEFAULT", "deepseek-v4-flash")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

SEARCH_RESULTS_NUM = 25
MAX_HTML_LEN = 6000
MAX_TOKENS_ANSWER = 4000
CACHE_TTL = 3600

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN не найден")
    sys.exit(1)

if not DEEPSEEK_API_KEY:
    logger.error("❌ DEEPSEEK_API_KEY не найден")
    sys.exit(1)

def now():
    return datetime.now(TZ)

def get_current_date():
    return now().strftime("%d.%m.%Y")

# ==================== ПУТИ ====================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")

# ==================== ПАМЯТЬ ====================
def load_memory(uid):
    try:
        with open(memory_path(uid), 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data[-20:] if len(data) > 20 else data
            return []
    except:
        return []

def save_memory(uid, history):
    try:
        if not isinstance(history, list):
            return False
        if len(history) > 100:
            history = history[-100:]
        with open(memory_path(uid), 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения памяти: {e}")
        return False

def load_profile(uid):
    try:
        with open(profile_path(uid), 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_profile(uid, profile):
    try:
        profile["updated"] = now().strftime("%d.%m.%Y %H:%M:%S")
        with open(profile_path(uid), 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def load_counter(uid):
    try:
        with open(counter_path(uid), 'r', encoding='utf-8') as f:
            return json.load(f).get("count", 0)
    except:
        return 0

def save_counter(uid, count):
    try:
        with open(counter_path(uid), 'w', encoding='utf-8') as f:
            json.dump({"count": count}, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

# ==================== HTTP ====================
_http_session = None

async def get_http_session():
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        _http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        logger.info("✅ HTTP сессия создана")
    return _http_session

# ==================== BROWSERLESS ====================
PLAYWRIGHT_AVAILABLE = False
if BROWSERLESS_WS_ENDPOINT:
    try:
        from playwright.async_api import async_playwright
        PLAYWRIGHT_AVAILABLE = True
        logger.info("✅ Playwright подключен")
    except:
        logger.warning("⚠️ Playwright не установлен")

# ==================== КЭШИ ====================
html_cache = {}
search_cache = {}
answer_cache = {}

def normalize_query(query):
    if not isinstance(query, str):
        return ""
    return re.sub(r'[^\w\s]', '', query.lower())[:100]

# ==================== ПАРСИНГ ====================
def clean_html_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    lines = []
    for line in text.split('. '):
        line = line.strip()
        if not line:
            continue
        if len(line) > 30:
            lines.append(line)
    return '. '.join(lines[:20])

def extract_date_from_html(html: str) -> str:
    patterns = [
        r'"datePublished":"(\d{4}-\d{2}-\d{2})"',
        r'"date":"(\d{4}-\d{2}-\d{2})"',
        r'(\d{2}\.\d{2}\.\d{4})',
        r'(\d{4})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            date = match.group(1)
            if re.match(r'^\d{4}$', date):
                year = int(date)
                if 2000 <= year <= 2030:
                    return date
            return date
    return "дата не указана"

# ==================== ЗАГРУЗКА ====================
async def fetch_content(url: str, timeout: int = 15):
    if url in html_cache:
        cached = html_cache[url]
        return cached.get("text", ""), cached.get("date", "дата не указана")
    
    result = ""
    pub_date = "дата не указана"
    
    if PLAYWRIGHT_AVAILABLE and BROWSERLESS_WS_ENDPOINT:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(BROWSERLESS_WS_ENDPOINT)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                html = await page.content()
                await page.close()
                result = clean_html_text(html)
                pub_date = extract_date_from_html(html)
                if result:
                    logger.info(f"✅ Browserless: {url[:50]}")
        except Exception as e:
            logger.warning(f"⚠️ Browserless: {str(e)[:50]}")
    
    if not result:
        session = await get_http_session()
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    result = clean_html_text(html)
                    pub_date = extract_date_from_html(html)
                    if result:
                        logger.info(f"✅ HTTP: {url[:50]}")
        except Exception as e:
            logger.warning(f"⚠️ HTTP: {str(e)[:50]}")
    
    if result:
        html_cache[url] = {"text": result, "date": pub_date}
        if len(html_cache) > 50:
            oldest = list(html_cache.keys())[0]
            del html_cache[oldest]
        return result, pub_date
    
    return "", "дата не указана"

async def fetch_multiple_pages(links, max_pages=8):
    if not links:
        return []
    
    semaphore = asyncio.Semaphore(5)
    
    async def fetch_one(url):
        async with semaphore:
            text, date = await fetch_content(url)
            if text and len(text) > 50:
                return {"url": url, "text": text, "date": date}
            return None
    
    tasks = [fetch_one(url) for url in links[:max_pages]]
    fetched = await asyncio.gather(*tasks)
    return [r for r in fetched if r is not None]

# ==================== ПОИСК ====================
async def search_apiserpent(query):
    if not APISERPENT_API_KEY:
        return []
    session = await get_http_session()
    try:
        params = {"q": query, "engine": "google", "num": SEARCH_RESULTS_NUM}
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=15
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            organic = data.get("results", {}).get("organic", [])
            if not organic:
                organic = data.get("organic_results", [])
            for x in organic[:SEARCH_RESULTS_NUM]:
                if isinstance(x, dict):
                    results.append({
                        "title": str(x.get("title", ""))[:120],
                        "snippet": str(x.get("snippet", ""))[:300],
                        "link": str(x.get("url", x.get("link", "#")))[:120]
                    })
            return results
    except Exception as e:
        logger.warning(f"⚠️ APISerpent: {e}")
        return []

async def search_serper(query):
    if not SERPER_API_KEY:
        return []
    session = await get_http_session()
    try:
        async with session.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": SEARCH_RESULTS_NUM},
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            timeout=10
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            for item in data.get("organic", [])[:SEARCH_RESULTS_NUM]:
                results.append({
                    "title": item.get("title", "")[:120],
                    "snippet": item.get("snippet", "")[:300],
                    "link": item.get("link", "#")[:120]
                })
            return results
    except Exception as e:
        logger.warning(f"⚠️ Serper: {e}")
        return []

async def search_primary(query):
    norm = normalize_query(query)
    if norm in search_cache:
        cached = search_cache[norm]
        if (datetime.now() - cached['time']).total_seconds() < CACHE_TTL:
            return cached['data']
    
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
        return results
    
    results = await search_serper(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
    
    return results

# ==================== DEEPSEEK ====================
async def ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER, attempt=0):
    if attempt >= 3:
        return None, "max_retries"
    
    session = await get_http_session()
    try:
        payload = {
            "model": MODEL_DEFAULT,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        async with session.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json=payload,
            timeout=45
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("choices"):
                    return data["choices"][0]["message"]["content"], None
            
            if resp.status == 429:
                wait = 2 ** attempt
                await asyncio.sleep(wait)
                return await ask_deepseek(messages, temperature, max_tokens, attempt + 1)
            
            return None, f"HTTP {resp.status}"
    except Exception as e:
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
            return await ask_deepseek(messages, temperature, max_tokens, attempt + 1)
        return None, str(e)

# ==================== ПОИСК И ОТВЕТ ====================
async def search_and_answer(uid, user_message, history):
    variants = [user_message]
    variants.append(f"{user_message} {now().year}")
    
    all_results = []
    for variant in variants[:2]:
        results = await search_primary(variant)
        if results:
            all_results.extend(results)
            if len(all_results) >= 20:
                break
    
    if not all_results:
        return "❌ В интернете ничего не найдено. Попробуйте переформулировать запрос."
    
    links = [r['link'] for r in all_results[:10]]
    pages = await fetch_multiple_pages(links, max_pages=8)
    
    if not pages:
        simple = "🔍 Результаты поиска:\n\n"
        for i, r in enumerate(all_results[:10], 1):
            simple += f"{i}. {r.get('title', 'Без названия')}\n"
            simple += f"   {r.get('snippet', '')[:150]}\n"
            simple += f"   🔗 {r.get('link', '')}\n\n"
        return simple + f"📅 {get_current_date()}"
    
    source_text = "\n\n".join([
        f"--- ИСТОЧНИК {i+1} ---\nURL: {p['url']}\nДата: {p.get('date', 'дата не указана')}\n{p['text'][:1500]}"
        for i, p in enumerate(pages)
    ])
    
    system_prompt = f"""
Ты — аналитик. Проанализируй источники и дай ответ.

Запрос: {user_message}

⚠️ ФОРМАТ ОТВЕТА:
📊 **Использованные источники:** (перечисли все с кратким содержанием)
📊 **Общие факты:**
⚠️ **Противоречия:** (если есть)
✅ **Вывод:**

ДАННЫЕ:
{source_text}
"""
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer:
        simple = "🔍 Результаты поиска:\n\n"
        for i, r in enumerate(all_results[:10], 1):
            simple += f"{i}. {r.get('title', 'Без названия')}\n"
            simple += f"   {r.get('snippet', '')[:150]}\n"
            simple += f"   🔗 {r.get('link', '')}\n\n"
        return simple + f"📅 {get_current_date()}"
    
    speculation = ['возможно', 'вероятно', 'скорее всего', 'должно быть']
    if any(p in answer.lower() for p in speculation):
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    return answer

# ==================== КНОПКИ ====================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🔍 Новый поиск", "❓ Помощь"],
        ["🔄 Сброс", "⏹️ Стоп"]
    ], resize_keyboard=True)

def get_after_answer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"),
         InlineKeyboardButton("✏️ Уточнить", callback_data="refine")]
    ])

# ==================== ОБРАБОТЧИКИ ====================
async def handle_message(update, context):
    try:
        uid = update.effective_user.id
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            return
        
        user_message = update.effective_message.text[:1000]
        if not user_message:
            return
        
        if user_message == "🔍 Новый поиск":
            context.user_data.clear()
            await safe_reply(update, "🔍 Задай вопрос для поиска.")
            return
        
        elif user_message == "❓ Помощь":
            await safe_reply(update,
                "❓ **Помощь**\n\n"
                "🔍 **Новый поиск** - задай вопрос\n"
                "🔄 **Сброс** - очистить\n"
                "⏹️ **Стоп** - остановить"
            )
            return
        
        elif user_message == "🔄 Сброс":
            context.user_data.clear()
            await safe_reply(update, "🔄 Диалог сброшен.")
            return
        
        elif user_message == "⏹️ Стоп":
            context.user_data.clear()
            await safe_reply(update, "⏹️ Остановлено.")
            return
        
        if user_message.startswith('/'):
            return
        
        if context.user_data.get('awaiting_followup'):
            answer = await handle_followup(update, context, user_message)
            if answer:
                await safe_reply(update, answer)
            return
        
        uid = update.effective_user.id
        history = load_memory(uid)
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['start_time'] = time.time()
        context.user_data['query'] = user_message
        
        await safe_reply(update, "⏳ Ищу информацию...")
        
        answer = await search_and_answer(uid, user_message, history)
        
        elapsed = int(time.time() - context.user_data['start_time'])
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        context.user_data['last_answer'] = answer
        context.user_data['awaiting_followup'] = True
        
        history.append({"role": "assistant", "content": answer[:500]})
        save_memory(uid, history)
        
        await safe_reply(update, answer, reply_markup=get_after_answer_keyboard())
    
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await safe_reply(update, "⚠️ Ошибка. Попробуйте еще раз.")

async def handle_after_answer_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "new_search":
        context.user_data.clear()
        try:
            await query.edit_message_text("🔍 Новый поиск. Напиши вопрос.")
        except:
            await query.message.reply_text("🔍 Новый поиск. Напиши вопрос.")
    
    elif query.data == "refine":
        last_query = context.user_data.get('query', '')
        if not last_query:
            await query.edit_message_text("⏳ Нет активного вопроса.")
            return
        
        context.user_data['awaiting_followup'] = True
        try:
            await query.edit_message_text(
                f"✏️ Уточни по запросу:\n\n**{last_query}**\n\nНапиши что именно уточнить."
            )
        except:
            await query.message.reply_text(
                f"✏️ Уточни по запросу:\n\n**{last_query}**\n\nНапиши что именно уточнить."
            )

# ✅ ИСПРАВЛЕННАЯ ФУНКЦИЯ - скобка добавлена!
async def handle_followup(update, context, user_message):
    last_answer = context.user_data.get('last_answer', '')
    
    system_prompt = f"""
Пользователь уточняет по предыдущему ответу.

Предыдущий ответ: {last_answer[:500]}

Уточнение: {user_message}

Ответь на уточнение кратко и по делу.
"""
    messages = [{"role": "system", "content": system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=2000)  # ✅ СКОБКА ЗАКРЫТА!
    
    if err or not answer:
        return "⚠️ Не удалось обработать уточнение."
    
    speculation = ['возможно', 'вероятно', 'скорее всего']
    if any(p in answer.lower() for p in speculation):
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    return answer

async def safe_reply(update, text, reply_markup=None):
    if not text:
        text = "⚠️ Пустой ответ."
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.reply_text(text, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")

# ==================== КОМАНДЫ ====================
async def start(update, context):
    await safe_reply(
        update,
        "👋 Привет! Я поисковый ассистент.\n\n"
        "🔍 **Просто напиши вопрос** - я найду ответ\n"
        "📊 Покажу источники\n"
        "🧠 Запоминаю тебя\n\n"
        "Попробуй спросить что-нибудь!",
        reply_markup=get_main_keyboard()
    )

async def stats_command(update, context):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    
    raw = load_memory(uid)
    await safe_reply(
        update,
        f"📊 **Статистика**\n\n"
        f"💬 В памяти: {len(raw)} сообщений"
    )

async def forget_command(update, context):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    
    save_memory(uid, [])
    save_profile(uid, {})
    context.user_data.clear()
    await safe_reply(update, "🧹 Всё забыто!")

# ==================== ЗАПУСК ====================
def main():
    logger.info("🚀 БОТ ЗАПУСКАЕТСЯ...")
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("forget", forget_command))
        
        app.add_handler(CallbackQueryHandler(handle_after_answer_callback, pattern="^(new_search|refine)$"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("✅ Бот готов к работе!")
        app.run_polling()
    
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
