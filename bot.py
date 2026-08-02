# ===================================================================
#  BROWAIX BOT — ФИНАЛЬНАЯ ВЕРСИЯ
#  Оформление: Вариант 7 (【】+ разделители)
#  Работающие таймер + источники + защита от вранья
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
MAX_HTML_LEN = 15000
MAX_TOKENS_ANSWER = 8000
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
    text = re.sub(r'\{[^}]*\}', '', text)
    text = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', '', text)
    
    lines = []
    for line in text.split('. '):
        line = line.strip()
        if not line:
            continue
        if len(line) > 20:
            lines.append(line)
    
    return '. '.join(lines[:40])

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

async def fetch_multiple_pages(links, max_pages=6):
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

# ==================== ТАЙМЕР ====================
async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        message = await context.bot.send_message(
            chat_id,
            "🌐 Ищу информацию в интернете...\n\n⏱️ 0 сек"
        )
        
        elapsed = 0
        dots = 0
        while elapsed < 120:
            await asyncio.sleep(3)
            
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

# ==================== ПРОВЕРКА НА ОБМАН ====================

def is_lie_by_sense(text: str) -> Tuple[bool, str]:
    text_lower = text.lower()
    
    lie_patterns = [
        (r'нет\s*(никаких|каких-либо|достаточных)?\s*данных', "Утверждает, что нет данных (хотя они есть)"),
        (r'нет\s*(никакой|какой-либо)\s*информации', "Утверждает, что нет информации (хотя она есть)"),
        (r'в\s*источниках\s*нет', "Утверждает, что в источниках нет данных (ложь!)"),
        (r'ничего\s*не\s*найдено', "Утверждает, что ничего не найдено (ложь!)"),
        (r'не\s*удалось\s*найти', "Утверждает, что не удалось найти (ложь!)"),
        (r'не\s*содержат\s*конкретных', "Утверждает, что нет конкретных данных (ложь!)"),
        (r'невозможно\s*(составить|сделать|определить|найти|получить)', "Утверждает, что невозможно (хотя возможно!)"),
        (r'не\s*представляется\s*возможным', "Утверждает, что невозможно (хотя возможно!)"),
        (r'(привожу|даю|предлагаю|составляю)\s*(свой|собственный)\s*(список|перечень|ответ|вариант)', "Придумывает свой ответ вместо источников!"),
        (r'на\s*основе\s*(моих|своих)\s*знаний', "Использует свои знания вместо источников!"),
        (r'моя\s*база\s*знаний', "Использует свою базу знаний вместо источников!"),
        (r'я\s*выбрал\s*(лучшие|главные|основные)', "Выбрал только часть данных (ложь!)"),
        (r'я\s*сократил', "Сократил данные (ложь!)"),
        (r'я\s*пропустил', "Пропустил данные (ложь!)"),
        (r'я\s*не\s*могу', "Говорит 'я не могу' (хотя может!)"),
        (r'у\s*меня\s*нет\s*возможности', "Говорит 'нет возможности' (хотя есть!)"),
        (r'(нерелевантны|не\s*релевантны|не\s*относятся)', "Отмахивается от источников (ложь!)"),
        (r'очевидно', "Додумывает (ложь!)"),
        (r'можно\s*предположить', "Предполагает (ложь!)"),
        (r'подробнее\s*(в|по)\s*источнику', "Отсылает к источнику вместо извлечения данных (ложь!)"),
        (r'слишком\s*много\s*данных', "Жалуется на объём (ложь!)"),
        (r'выходит\s*за\s*пределы\s*(моих|своих)\s*знаний', "Ссылается на знания вместо источников (ложь!)"),
        (r'к\s*сожалению', "Начинает с 'к сожалению' — признак обмана"),
        (r'зависит\s*от\s*(условий|контекста|ситуации)', "Уходит от ответа (ложь!)"),
        (r'рекомендую\s*обратиться\s*к\s*(специалисту|эксперту|врачу|юристу)', "Перекладывает ответственность (ложь!)"),
        (r'(статья|источник|данные)\s*устарел(а|и)', "Отмазывается, что данные старые (ложь!)"),
        (r'я\s*не\s*(специалист|эксперт)', "Ссылается на отсутствие компетенции (ложь!)"),
        (r'(запрос|вопрос)\s*слишком\s*(широкий|общий)', "Вместо ответа просит уточнить (ложь!)"),
        (r'это\s*(мнение|субъективное|личное)\s*автора', "Отбрасывает данные (ложь!)"),
        (r'нужен\s*(более|более)\s*актуальный\s*источник', "Отказывается от данных (ложь!)"),
        (r'я\s*не\s*уверен\s*в\s*(достоверности|точности|правильности)', "Отбрасывает данные (ложь!)"),
    ]
    
    for pattern, reason in lie_patterns:
        if re.search(pattern, text_lower):
            return True, reason
    
    return False, ""

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================

async def search_and_answer_safe(uid, user_message, history):
    logger.info(f"🛡️ ЗАПРОС: {user_message[:50]}")
    
    # ================================================================
    # ШАГ 1: ПОИСК
    # ================================================================
    
    if not APISERPENT_API_KEY and not SERPER_API_KEY:
        return await generate_knowledge_safe(user_message, history, "Нет API ключей для поиска")
    
    variants = [user_message, f"{user_message} {now().year}"]
    all_results = []
    
    for variant in variants:
        results = await search_primary(variant)
        if results:
            all_results.extend(results)
            if len(all_results) >= 15:
                break
    
    if not all_results:
        return await generate_knowledge_safe(user_message, history, "В интернете ничего не найдено")
    
    logger.info(f"✅ Найдено {len(all_results)} результатов")
    
    # ================================================================
    # ШАГ 2: ЗАГРУЗКА
    # ================================================================
    
    links = [r['link'] for r in all_results[:10]]
    pages = await fetch_multiple_pages(links, max_pages=6)
    
    good_sources = [p for p in pages if len(p.get('text', '')) > 300]
    source_count = len(good_sources)
    
    logger.info(f"✅ Загружено {len(pages)} страниц, качественных {source_count}")
    
    if source_count == 0:
        return "⚠️ Страницы загрузить не удалось. Попробуйте позже."
    
    # ================================================================
    # ШАГ 3: ПРОМПТ
    # ================================================================
    
    source_text = ""
    for i, p in enumerate(good_sources[:6], 1):
        source_text += f"""
--- СТРАНИЦА {i} ---
URL: {p['url']}
Дата: {p.get('date', 'дата не указана')}
ТЕКСТ:
{p['text'][:5000]}
"""
    
    system_prompt = f"""
Ты — универсальный аналитик. Твоя задача — найти в тексте ЛЮБЫЕ данные и ответить на вопрос.

⚠️ **ТВОЙ АЛГОРИТМ:**
1. Прочитай ВЕСЬ текст
2. Найди ЛЮБУЮ СТРУКТУРУ (списки, нумерацию, пункты, таблицы, шаги)
3. Извлеки данные, которые отвечают на запрос
4. Определи ТИП данных (список, инструкция, описание, факты, цены)
5. Построй ОТВЕТ, используя найденную структуру

⚠️ **ЗАПРЕЩЕНО ПО СМЫСЛУ (ЭТО ЛОЖЬ!):**
1. НЕЛЬЗЯ говорить, что данных "мало" или "недостаточно" — ОНИ ЕСТЬ!
2. НЕЛЬЗЯ говорить, что источники "нерелевантны" — ТЫ ДОЛЖЕН ИХ ПРОВЕРИТЬ!
3. НЕЛЬЗЯ сокращать список — ПОКАЖИ ВСЁ, ЧТО НАШЁЛ!
4. НЕЛЬЗЯ говорить "очевидно" — ТОЛЬКО ИЗ ТЕКСТА!
5. НЕЛЬЗЯ говорить "можно предположить" — ТОЛЬКО ФАКТЫ!
6. НЕЛЬЗЯ отсылать к источнику — ИЗВЛЕКИ ДАННЫЕ!
7. НЕЛЬЗЯ говорить "это не отвечает на вопрос" — НАЙДИ ОТВЕТ!
8. НЕЛЬЗЯ говорить "слишком много данных" — ОБРАБОТАЙ ВСЁ!
9. НЕЛЬЗЯ говорить "я выбрал лучшее" — ПОКАЖИ ВСЁ!
10. НЕЛЬЗЯ говорить "это за пределами моих знаний" — ИСПОЛЬЗУЙ ИСТОЧНИКИ!
11. НЕЛЬЗЯ говорить "зависит от условий" — ОТВЕЧАЙ!
12. НЕЛЬЗЯ говорить "рекомендую обратиться" — ОТВЕЧАЙ САМ!
13. НЕЛЬЗЯ говорить "статья устарела" — ИСПОЛЬЗУЙ ТО, ЧТО ЕСТЬ!
14. НЕЛЬЗЯ говорить "я не специалист" — ТЫ АНАЛИТИК!
15. НЕЛЬЗЯ говорить "слишком широкий запрос" — ОТВЕЧАЙ!
16. НЕЛЬЗЯ говорить "это мнение" — ИЗВЛЕКАЙ ДАННЫЕ!
17. НЕЛЬЗЯ говорить "нужен актуальный источник" — ИСПОЛЬЗУЙ ТО, ЧТО ЕСТЬ!
18. НЕЛЬЗЯ говорить "я не уверен" — ОТВЕЧАЙ!

⚠️ **ТЫ ОБЯЗАН:**
1. Прочитать КАЖДЫЙ источник полностью
2. Извлечь ВСЕ данные из КАЖДОГО источника
3. Показать ВСЁ, что нашёл

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **Источники:** (перечисли, откуда взял данные)
📊 **Ответ:** (твой ответ на основе найденного)
✅ **Вывод:**

Запрос: {user_message}

ТЕКСТ ДЛЯ АНАЛИЗА:
{source_text}
"""
    
    logger.info("✅ Промпт сформирован")
    
    # ================================================================
    # ШАГ 4: ГЕНЕРАЦИЯ
    # ================================================================
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer:
        logger.warning("⚠️ DeepSeek не ответил")
        return "⚠️ Не удалось получить ответ. Попробуйте позже."
    
    logger.info("✅ Ответ получен")
    
    # ================================================================
    # ШАГ 5: ПРОВЕРКА НА ОБМАН
    # ================================================================
    
    is_lie, lie_reason = is_lie_by_sense(answer)
    
    if is_lie:
        logger.warning(f"⚠️ ОБНАРУЖЕНА ЛОЖЬ: {lie_reason}")
        
        stronger_warning = f"""
⚠️ ТЫ НАРУШИЛ ПРАВИЛА! ОБНАРУЖЕНА ЛОЖЬ!
Ты сказал: "{lie_reason}"
Но это НЕПРАВДА — у тебя есть {source_count} источников с данными.
ОТВЕТЬ ЗАНОВО, ЧЕСТНО, ТОЛЬКО ИЗ ИСТОЧНИКОВ!
НЕЛЬЗЯ СОКРАЩАТЬ, ПРОПУСКАТЬ, ПРИДУМЫВАТЬ!
"""
        system_prompt = system_prompt + stronger_warning
        messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
        answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
        
        if err or not answer:
            return "⚠️ Не удалось получить честный ответ."
        
        is_lie, lie_reason = is_lie_by_sense(answer)
        if is_lie:
            logger.error(f"❌ ПОВТОРНАЯ ЛОЖЬ: {lie_reason}")
            return f"⚠️ **В источниках нет информации по вашему запросу.**\n\nПопробуйте переформулировать вопрос."
    
    logger.info("✅ Проверка пройдена — ответ честный")
    
    # ================================================================
    # ШАГ 6: ФОРМИРОВАНИЕ ФИНАЛЬНОГО ОТВЕТА (ВАРИАНТ 7)
    # ================================================================
    
    # Извлекаем основную часть ответа (без источников, если они уже есть)
    main_text = answer
    
    # Если в ответе уже есть источники — оставляем как есть
    # Иначе добавляем свои
    if not has_sources_in_answer(answer):
        sources_text = ""
        for i, p in enumerate(good_sources[:6], 1):
            sources_text += f"{i}. {p['url']}\n"
        
        final_answer = f"""
⏱️ {{elapsed}} сек

【1】ИСТОЧНИКИ
━━━━━━━━━━━━━━━━━━━━━━
{sources_text}
【2】ОТВЕТ
━━━━━━━━━━━━━━━━━━━━━━
{main_text}

【3】ВЫВОД
━━━━━━━━━━━━━━━━━━━━━━
{conclusion}
"""
    else:
        final_answer = answer
    
    return final_answer

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def has_sources_in_answer(text: str) -> bool:
    patterns = [r'Источник \d+', r'источник \d+', r'📊 .*источник', r'http', r'www\.', r'🔗']
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

async def generate_knowledge_safe(user_message, history, reason):
    system_prompt = f"""
Ты — честный ассистент. Отвечай из своих знаний.

⚠️ **ПРАВИЛА:**
1. Отвечай ТОЛЬКО тем, что знаешь на 100%
2. Если не знаешь - скажи "Я не знаю"
3. Если данные старые - напиши "📅 Данные могут быть устаревшими"

⚠️ **ПРИЧИНА:** {reason}

Запрос: {user_message}
"""
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.0, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer:
        return "⚠️ Не удалось получить ответ."
    
    speculation = ['возможно', 'вероятно', 'скорее всего']
    if any(p in answer.lower() for p in speculation):
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    return f"🧠 [ЗНАНИЯ МОДЕЛИ] — {reason}\n\n{answer}"

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
            await safe_reply(update, "🔍 Задай вопрос для поиска в интернете.")
            return
        
        elif user_message == "❓ Помощь":
            await safe_reply(update,
                "❓ **Помощь**\n\n"
                "🔍 **Новый поиск** - задай вопрос\n"
                "🔄 **Сброс** - очистить\n"
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
        
        if context.user_data.get('awaiting_followup'):
            answer = await handle_followup(update, context, user_message)
            if answer:
                await safe_reply(update, answer)
            return
        
        uid = update.effective_user.id
        chat_id = update.effective_chat.id
        history = load_memory(uid)
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['start_time'] = time.time()
        context.user_data['query'] = user_message
        context.user_data['chat_id'] = chat_id
        context.user_data['found_answer'] = False
        
        timer_task = asyncio.create_task(
            send_progress_updates(chat_id, context, context.user_data['start_time'])
        )
        
        answer = await search_and_answer_safe(uid, user_message, history)
        
        context.user_data['found_answer'] = True
        await timer_task
        
        elapsed = int(time.time() - context.user_data['start_time'])
        
        # ВСТАВЛЯЕМ ТАЙМЕР В ОТВЕТ
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

async def handle_followup(update, context, user_message):
    last_answer = context.user_data.get('last_answer', '')
    
    system_prompt = f"""
Пользователь уточняет по предыдущему ответу.

Предыдущий ответ: {last_answer[:500]}

Уточнение: {user_message}

Ответь на уточнение кратко и по делу.
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
    if not text:
        text = "⚠️ Пустой ответ."
    msg = update.effective_message
    if not msg:
        return
    
    try:
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
            
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    await msg.reply_text(part, disable_web_page_preview=True, reply_markup=reply_markup)
                else:
                    await msg.reply_text(part, disable_web_page_preview=True)
        else:
            await msg.reply_text(text, disable_web_page_preview=True, reply_markup=reply_markup)
    
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
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
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("⚠️ РЕЖИМ: ФИНАЛЬНЫЙ + ВАРИАНТ 7 ОФОРМЛЕНИЯ")
    
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
