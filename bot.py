# ===================================================================
#  BROWAIX BOT — ФИНАЛЬНАЯ ВЕРСИЯ
#  Двойной запрет "нет доступа" + Исправленный таймер + Гибридный режим
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
from typing import List, Dict, Any, Optional, Tuple
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
MAX_TOKENS_ANSWER = 6000
CACHE_TTL = 3600

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN не найден")
    sys.exit(1)

if not DEEPSEEK_API_KEY:
    logger.error("❌ DEEPSEEK_API_KEY не найден")
    sys.exit(1)

logger.info(f"🔑 APISERPENT: {'✅' if APISERPENT_API_KEY else '❌'}")
logger.info(f"🔑 SERPER: {'✅' if SERPER_API_KEY else '❌'}")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")

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

# ==================== ДИНАМИЧЕСКИЙ ТАЙМЕР (ИСПРАВЛЕННЫЙ) ====================
async def send_progress_updates(chat_id, context, start_time):
    """Отправляет обновления таймера каждые 3 секунды"""
    message = None
    try:
        message = await context.bot.send_message(
            chat_id,
            "🌐 Ищу информацию в интернете...\n\n⏱️ 0 сек"
        )
        
        elapsed = 0
        dots = 0
        while elapsed < 120:  # Максимум 120 секунд
            await asyncio.sleep(3)
            
            # ✅ ПРОВЕРЯЕМ ФЛАГ ПОСЛЕ КАЖДОГО SLEEP!
            if context.user_data.get('found_answer'):
                try:
                    await message.edit_text("✅ Информация найдена! Формирую ответ...")
                except Exception:
                    pass
                break
            
            elapsed = int(time.time() - start_time)
            dots = (dots + 1) % 4
            dots_text = "." * dots + " " * (3 - dots)
            progress = min(elapsed, 30)
            bar = "█" * (progress // 3) + "░" * (10 - (progress // 3))
            
            status_text = f"🌐 Ищу информацию в интернете{dots_text}\n\n⏱️ {elapsed} сек\n{bar}"
            
            try:
                await message.edit_text(status_text)
            except Exception:
                message = await context.bot.send_message(chat_id, status_text)
    
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")
    return message

def has_sources_in_answer(text: str) -> bool:
    """Проверяет, есть ли в ответе перечисление источников"""
    patterns = [r'Источник \d+', r'источник \d+', r'📊 .*источник', r'http', r'www\.', r'🔗']
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================
async def search_and_answer_safe(uid, user_message, history):
    logger.info(f"🛡️ ШАГ 1: ЗАПРОС — {user_message[:50]}")
    
    # ================================================================
    # ШАГ 2: ПОИСК В ИНТЕРНЕТЕ
    # ================================================================
    
    if not APISERPENT_API_KEY and not SERPER_API_KEY:
        return await generate_knowledge_safe(user_message, history, "Нет API ключей для поиска")
    
    variants = [user_message, f"{user_message} {now().year}"]
    all_results = []
    
    for variant in variants:
        results = await search_primary(variant)
        if results:
            all_results.extend(results)
            if len(all_results) >= 20:
                break
    
    if not all_results:
        return await generate_knowledge_safe(user_message, history, "В интернете ничего не найдено")
    
    logger.info(f"✅ ШАГ 2: Найдено {len(all_results)} результатов")
    
    # ================================================================
    # ШАГ 3: ЗАГРУЗКА СТРАНИЦ
    # ================================================================
    
    links = [r['link'] for r in all_results[:12]]
    pages = await fetch_multiple_pages(links, max_pages=8)
    
    good_sources = [p for p in pages if len(p.get('text', '')) > 200]
    source_count = len(good_sources)
    
    logger.info(f"✅ ШАГ 3: Загружено {len(pages)} страниц, качественных {source_count}")
    
    # ================================================================
    # ШАГ 4: ВЫБОР РЕЖИМА
    # ================================================================
    
    if source_count == 0:
        logger.warning("⚠️ ШАГ 4: Нет качественных источников → фоллбэк")
        return await fallback_safe(all_results, "Нет качественных источников для анализа")
    
    if source_count >= 3:
        mode = "internet_only"
        logger.info("✅ ШАГ 4: Режим ТОЛЬКО ИНТЕРНЕТ")
    else:
        mode = "hybrid"
        logger.info("✅ ШАГ 4: Режим ГИБРИД")
    
    # ================================================================
    # ШАГ 5: ФОРМИРОВАНИЕ ПРОМПТА С ДВОЙНЫМ ЗАПРЕТОМ
    # ================================================================
    
    # 5.1. ИСТОЧНИКИ (САМОЕ ВАЖНОЕ — ПЕРВЫМИ!)
    source_text = "⚠️ **ВОТ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА:**\n\n"
    for i, p in enumerate(good_sources[:8], 1):
        source_text += f"""
--- ИСТОЧНИК {i} ---
URL: {p['url']}
Дата: {p.get('date', 'дата не указана')}
Содержание: {p['text'][:1500]}

"""
    
    # 5.2. ЗАПРЕТ В НАЧАЛЕ
    if mode == "internet_only":
        rules = f"""
⚠️ **ТЫ ПОЛУЧИЛ ВЫШЕ {len(good_sources)} РЕАЛЬНЫХ ИСТОЧНИКОВ ИЗ ИНТЕРНЕТА!**

⚠️ **СТРОГО ЗАПРЕЩЕНО (НАРУШЕНИЕ = ОШИБКА):**
1. **НЕЛЬЗЯ** говорить "нет доступа к интернету" — ТЫ ТОЛЬКО ЧТО ЕГО ПОЛУЧИЛ!
2. **НЕЛЬЗЯ** говорить "инструменты поиска не активированы" — ОНИ АКТИВИРОВАНЫ!
3. **НЕЛЬЗЯ** говорить "не могу проверить" — ТЫ МОЖЕШЬ, ИСТОЧНИКИ ПЕРЕД ТОБОЙ!
4. **НЕЛЬЗЯ** использовать "возможно", "вероятно" — БЕЗ ⚠️ [НЕ 100%]
5. **НЕЛЬЗЯ** игнорировать источники — ОНИ ПЕРЕД ТОБОЙ!

⚠️ **ТВОЯ ЗАДАЧА:**
1. Проанализируй источники выше
2. Дай ответ на основе ТОЛЬКО ИХ
3. Если в них нет информации — скажи "В источниках нет"

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **Использованные источники:** (перечисли ВСЕ {len(good_sources)})
📊 **Общие факты:**
⚠️ **Противоречия:**
✅ **Вывод:**

Запрос пользователя: {user_message}
Сегодня: {get_current_date()}
"""
    else:
        rules = f"""
⚠️ **ТЫ ПОЛУЧИЛ ВЫШЕ {len(good_sources)} РЕАЛЬНЫХ ИСТОЧНИКОВ ИЗ ИНТЕРНЕТА!**

⚠️ **СТРОГО ЗАПРЕЩЕНО (НАРУШЕНИЕ = ОШИБКА):**
1. **НЕЛЬЗЯ** говорить "нет доступа к интернету" — ТЫ ТОЛЬКО ЧТО ЕГО ПОЛУЧИЛ!
2. **НЕЛЬЗЯ** говорить "инструменты поиска не активированы" — ОНИ АКТИВИРОВАНЫ!
3. **НЕЛЬЗЯ** говорить "не могу проверить" — ТЫ МОЖЕШЬ, ИСТОЧНИКИ ПЕРЕД ТОБОЙ!
4. **НЕЛЬЗЯ** использовать "возможно", "вероятно" — БЕЗ ⚠️ [НЕ 100%]

⚠️ **ТВОЯ ЗАДАЧА:**
1. Сначала используй информацию из источников выше
2. Если данных мало — дополни знаниями
3. ОТМЕЧАЙ, что взято из знаний

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **Из интернета:** (что взято из источников)
🧠 **Дополнено из знаний:** (что добавил)
✅ **Вывод:**

Запрос: {user_message}
"""
    
    # 5.3. ПОВТОРНЫЙ ЗАПРЕТ В КОНЦЕ (ФИНАЛЬНОЕ НАПОМИНАНИЕ!)
    final_warning = """

⚠️ **ЕЩЁ РАЗ, ПЕРЕД ОТВЕТОМ:**

У ТЕБЯ ЕСТЬ ДОСТУП К ИНТЕРНЕТУ! ИСТОЧНИКИ ПЕРЕД ТОБОЙ!

**НЕЛЬЗЯ** говорить:
- "нет доступа к интернету"
- "инструменты поиска не активированы"
- "не могу проверить"

**ОТВЕЧАЙ ЧЕСТНО НА ОСНОВЕ ИСТОЧНИКОВ!**
"""
    
    system_prompt = source_text + rules + final_warning
    
    logger.info("✅ ШАГ 5: Промпт сформирован (источники ПЕРВЫМИ + ДВОЙНОЙ ЗАПРЕТ)")
    
    # ================================================================
    # ШАГ 6: ГЕНЕРАЦИЯ ОТВЕТА
    # ================================================================
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer:
        logger.warning("⚠️ ШАГ 6: DeepSeek не ответил → фоллбэк")
        return await fallback_safe(all_results, "DeepSeek не ответил")
    
    logger.info("✅ ШАГ 6: Ответ получен")
    
    # ================================================================
    # ШАГ 7: МЯГКАЯ ПРОВЕРКА (НЕ БЛОКИРУЕТ!)
    # ================================================================
    
    warnings = []
    answer_lower = answer.lower()
    
    # Проверяем запрещённые фразы
    forbidden = [
        "нет доступа к интернету",
        "инструменты поиска не активированы",
        "не могу проверить",
        "у меня нет информации",
    ]
    
    for phrase in forbidden:
        if phrase in answer_lower:
            warnings.append(f"⚠️ В ответе есть фраза '{phrase}' — это НЕПРАВДА!")
    
    # Проверка на предположения без предупреждения
    speculation = ['возможно', 'вероятно', 'скорее всего']
    if any(p in answer_lower for p in speculation) and "⚠️ [НЕ 100%]" not in answer:
        warnings.append("⚠️ Использует предположения без предупреждения")
    
    # Проверка на наличие источников в ответе
    if good_sources and not has_sources_in_answer(answer):
        warnings.append("⚠️ Есть источники, но они не перечислены в ответе")
    
    # Добавляем предупреждения в ответ (НЕ БЛОКИРУЕМ!)
    if warnings:
        logger.info(f"⚠️ ШАГ 7: Предупреждения: {warnings}")
        answer = answer + "\n\n⚠️ **Примечание:** " + "; ".join(warnings)
    else:
        logger.info("✅ ШАГ 7: Проверка пройдена")
    
    # ================================================================
    # ШАГ 8: ДОБАВЛЯЕМ МАРКЕР
    # ================================================================
    
    mode_labels = {
        "internet_only": "🌐 [ИНТЕРНЕТ]",
        "hybrid": "🔍 [ГИБРИД: ИНТЕРНЕТ + ЗНАНИЯ]",
    }
    
    if not any(label in answer for label in mode_labels.values()):
        answer = f"{mode_labels.get(mode, '📌 [ИСТОЧНИК НЕИЗВЕСТЕН]')}\n\n{answer}"
    
    logger.info("✅ ШАГ 8: Ответ готов!")
    
    return answer

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def generate_knowledge_safe(user_message, history, reason):
    """Ответ из знаний модели"""
    system_prompt = f"""
Ты — честный ассистент. Отвечай из своих знаний.

⚠️ **ПРАВИЛА:**
1. Отвечай ТОЛЬКО тем, что знаешь на 100%
2. Если не знаешь - скажи "Я не знаю"
3. Не используй слова: "возможно", "вероятно"

⚠️ **ПРИЧИНА:** {reason}

Запрос: {user_message}
Сегодня: {get_current_date()}
"""
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.0, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer:
        return "⚠️ Не удалось получить ответ."
    
    speculation = ['возможно', 'вероятно', 'скорее всего', 'должно быть']
    if any(p in answer.lower() for p in speculation):
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    return f"🧠 [ЗНАНИЯ МОДЕЛИ] — {reason}\n\n{answer}"

async def fallback_safe(all_results, reason):
    """Безопасный фоллбэк — показывает сырые результаты"""
    simple = f"⚠️ **ФОЛЛБЭК** ({reason})\n\n"
    simple += "🔍 **Результаты поиска:**\n\n"
    
    for i, r in enumerate(all_results[:15], 1):
        title = r.get('title', 'Без названия')
        snippet = r.get('snippet', '')[:200]
        link = r.get('link', '')
        
        simple += f"{i}. **{title}**\n"
        if snippet:
            simple += f"   {snippet}\n"
        if link and link != '#':
            simple += f"   🔗 {link}\n"
        simple += "\n"
    
    simple += f"📅 {get_current_date()}\n\n"
    simple += "⚠️ **Примечание:** Я не смог сформировать аналитический ответ, но вот сырые результаты поиска. Используй их для самостоятельного анализа."
    
    return simple

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
        
        # Кнопки меню
        if user_message == "🔍 Новый поиск":
            context.user_data.clear()
            await safe_reply(update, "🔍 Задай вопрос для поиска в интернете.")
            return
        
        elif user_message == "❓ Помощь":
            await safe_reply(update,
                "❓ **Помощь**\n\n"
                "🔍 **Новый поиск** - задай вопрос, я найду в интернете\n"
                "🔄 **Сброс** - очистить диалог\n"
                "⏹️ **Стоп** - остановить\n\n"
                "⚠️ **Я всегда ищу в интернете и честно говорю, откуда информация!**"
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
        
        # Уточнение
        if context.user_data.get('awaiting_followup'):
            answer = await handle_followup(update, context, user_message)
            if answer:
                await safe_reply(update, answer)
            return
        
        # НОВЫЙ ЗАПРОС
        uid = update.effective_user.id
        chat_id = update.effective_chat.id
        history = load_memory(uid)
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['start_time'] = time.time()
        context.user_data['query'] = user_message
        context.user_data['chat_id'] = chat_id
        context.user_data['found_answer'] = False
        
        # Запускаем таймер
        timer_task = asyncio.create_task(
            send_progress_updates(chat_id, context, context.user_data['start_time'])
        )
        
        # Выполняем поиск
        answer = await search_and_answer_safe(uid, user_message, history)
        
        # Сигналим таймеру
        context.user_data['found_answer'] = True
        await timer_task
        
        # Добавляем время
        elapsed = int(time.time() - context.user_data['start_time'])
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        context.user_data['last_answer'] = answer
        context.user_data['awaiting_followup'] = True
        
        # Сохраняем в память
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

async def handle_followup(update, context, user_message):
    last_answer = context.user_data.get('last_answer', '')
    
    system_prompt = f"""
Пользователь уточняет по предыдущему ответу.

Предыдущий ответ: {last_answer[:500]}

Уточнение: {user_message}

Ответь на уточнение кратко и по делу.

⚠️ **ПРАВИЛА:**
1. Не используй слова "возможно", "вероятно" без ⚠️ [НЕ 100%]
2. Если не знаешь - скажи "Я не знаю"
3. Будь честным
"""
    messages = [{"role": "system", "content": system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=2000)
    
    if err or not answer:
        return "⚠️ Не удалось обработать уточнение."
    
    speculation = ['возможно', 'вероятно', 'скорее всего']
    if any(p in answer.lower() for p in speculation) and "⚠️ [НЕ 100%]" not in answer:
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    return answer

async def safe_reply(update, text, reply_markup=None):
    """Безопасная отправка с разбивкой длинных сообщений"""
    if not text:
        text = "⚠️ Пустой ответ."
    msg = update.effective_message
    if not msg:
        return
    
    try:
        # Разбиваем длинные сообщения (Telegram лимит 4096 символов)
        if len(text) > 4096:
            parts = []
            current = ""
            for line in text.split('\n'):
                if len(current) + len(line) + 1 > 4000:
                    parts.append(current)
                    current = line
                else:
                    current += "\n" + line if current else line
            if current:
                parts.append(current)
            
            # Отправляем части
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    await msg.reply_text(part, disable_web_page_preview=True, reply_markup=reply_markup)
                else:
                    await msg.reply_text(part, disable_web_page_preview=True)
        else:
            await msg.reply_text(text, disable_web_page_preview=True, reply_markup=reply_markup)
    
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        # Пытаемся отправить хотя бы первую часть
        try:
            await msg.reply_text(text[:4000], disable_web_page_preview=True, reply_markup=reply_markup)
        except Exception:
            pass

# ==================== КОМАНДЫ ====================
async def start(update, context):
    await safe_reply(
        update,
        "👋 **Привет! Я поисковый ассистент.**\n\n"
        "🔍 **Просто напиши вопрос** - я найду ответ в интернете\n"
        "📊 **Покажу источники** - каждый ответ подтвержден\n"
        "⚠️ **НИКОГДА НЕ ВРУ** - если не знаю, скажу честно\n"
        "🧠 **Запоминаю тебя** - становлюсь умнее с каждым вопросом\n"
        "🕐 **Показываю время** - обновляется каждые 3 секунды\n\n"
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
    logger.info(f"🤖 Токен: {TELEGRAM_TOKEN[:10]}...")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("⚠️ РЕЖИМ: ДВОЙНОЙ ЗАПРЕТ 'НЕТ ДОСТУПА' + ДИНАМИЧЕСКИЙ ТАЙМЕР")
    
    if not APISERPENT_API_KEY and not SERPER_API_KEY:
        logger.error("🚨 ВНИМАНИЕ: НЕТ API КЛЮЧЕЙ ДЛЯ ПОИСКА!")
    
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
