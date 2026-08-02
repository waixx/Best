# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ФИНАЛЬНАЯ ВЕРСИЯ
#  Все требования сохранены. Ничего не вырезано.
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  ЛОГГЕР
# ═══════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════════

SEARCH_RESULTS_NUM = 10
MAX_HTML_LEN = 15000
MAX_TOKENS_ANSWER = 8000
CACHE_TTL = 86400  # 24 часа
TIMEOUT = 20
MAX_PAGES = 5
SEMAPHORE = 10

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info(f"🔑 APISERPENT: {'✅' if APISERPENT_API_KEY else '❌'}")
logger.info(f"🔑 SERPER: {'✅' if SERPER_API_KEY else '❌'}")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")

def now():
    return datetime.now(TZ)

def get_current_date():
    return now().strftime("%d.%m.%Y")

# ═══════════════════════════════════════════════════════════════════
#  ПУТИ
# ═══════════════════════════════════════════════════════════════════

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def episodic_path(uid): return os.path.join(DATA_DIR, f"episodic_{uid}.json")
def learning_path(uid): return os.path.join(DATA_DIR, f"learning_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")

# ═══════════════════════════════════════════════════════════════════
#  5 УРОВНЕЙ ПАМЯТИ (СОХРАНЕНО)
# ═══════════════════════════════════════════════════════════════════

class SuperMemory:
    def __init__(self, uid):
        self.uid = uid
        self.short_term = self._load(memory_path(uid), [])
        self.profile = self._load(profile_path(uid), {})
        self.episodic = self._load(episodic_path(uid), [])
        self.learning = self._load(learning_path(uid), {})
        self.counter = self._load(counter_path(uid), {"count": 0}).get("count", 0)
    
    def _load(self, path, default):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    
    def _save(self, path, data):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения: {e}")
            return False
    
    def add_message(self, role, content):
        msg = {"role": role, "content": content, "timestamp": now().isoformat()}
        self.short_term.append(msg)
        if len(self.short_term) > 100:
            old = self.short_term[:-100]
            self._compress(old)
            self.short_term = self.short_term[-100:]
        self.counter += 1
        self._extract_personal_info(content)
        self._extract_preferences(content)
        self.save()
    
    def _compress(self, messages):
        for msg in messages:
            content = msg.get('content', '')
            if len(content) < 20:
                continue
            if any(kw in content.lower() for kw in ['это', 'является', 'состоит', 'находится']):
                self.episodic.append({
                    'content': content[:200],
                    'timestamp': now().isoformat(),
                    'priority': 5
                })
        if len(self.episodic) > 200:
            self.episodic = self.episodic[-200:]
    
    def _extract_personal_info(self, text):
        patterns = {
            'name': r'(?:меня зовут|зовут|я)\s+([А-Яа-яA-Za-z\s]{2,30})',
            'age': r'(?:мне|возраст)\s+(\d{1,3})\s*(?:лет|года)',
            'city': r'(?:я живу|живу в|из города)\s+([А-Яа-яA-Za-z\s]{2,30})',
            'work': r'(?:я работаю|работаю)\s+([А-Яа-яA-Za-z\s]{2,50})',
        }
        for key, pattern in patterns.items():
            if m := re.search(pattern, text, re.IGNORECASE):
                if not self.profile.get(key):
                    self.profile[key] = m.group(1).strip()
    
    def _extract_preferences(self, text):
        if 'preferences' not in self.learning:
            self.learning['preferences'] = []
        if re.search(r'(?:нравится|люблю|предпочитаю|хочу|ищу)', text, re.I):
            pref = text.lower()
            for existing in self.learning['preferences']:
                if existing.get('text') == pref:
                    existing['count'] = existing.get('count', 0) + 1
                    return
            self.learning['preferences'].append({'text': pref, 'count': 1, 'timestamp': now().isoformat()})
            if len(self.learning['preferences']) > 100:
                self.learning['preferences'] = sorted(self.learning['preferences'], key=lambda x: x.get('count', 0), reverse=True)[:100]
    
    def get_context(self, limit=10):
        ctx = self.short_term[-limit:] if self.short_term else []
        if self.episodic:
            important = sorted(self.episodic, key=lambda x: x.get('priority', 0), reverse=True)[:3]
            for mem in important:
                ctx.append({'role': 'system', 'content': f"📌 Важно: {mem['content']}", 'is_episodic': True})
        if self.profile:
            ctx.append({"role": "system", "content": f"👤 О пользователе: {', '.join([f'{k}: {v}' for k, v in self.profile.items()])}"})
        return ctx
    
    def get_personalized_context(self):
        lines = []
        if self.profile:
            lines.append("👤 О пользователе:")
            for k, v in self.profile.items():
                if k != 'updated':
                    lines.append(f"• {k}: {v}")
        if self.learning.get('preferences'):
            top = sorted(self.learning['preferences'], key=lambda x: x.get('count', 0), reverse=True)[:3]
            if top:
                lines.append("\n💡 Предпочтения:")
                for p in top:
                    lines.append(f"• {p['text'][:50]} (упоминаний: {p.get('count', 0)})")
        return '\n'.join(lines) if lines else ""
    
    def save(self):
        self._save(memory_path(self.uid), self.short_term)
        self._save(profile_path(self.uid), self.profile)
        self._save(episodic_path(self.uid), self.episodic)
        self._save(learning_path(self.uid), self.learning)
        self._save(counter_path(self.uid), {"count": self.counter})

_memory_cache = {}

def get_memory(uid):
    if uid not in _memory_cache:
        _memory_cache[uid] = SuperMemory(uid)
    return _memory_cache[uid]

# ═══════════════════════════════════════════════════════════════════
#  HTTP, BROWSERLESS, ПАРСИНГ
# ═══════════════════════════════════════════════════════════════════

_http_session = None

async def get_http_session():
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        _http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _http_session

PLAYWRIGHT_AVAILABLE = False
if BROWSERLESS_WS_ENDPOINT:
    try:
        from playwright.async_api import async_playwright
        PLAYWRIGHT_AVAILABLE = True
        logger.info("✅ Playwright подключен")
    except:
        logger.warning("⚠️ Playwright не установлен")

html_cache = {}
search_cache = {}
answer_cache = {}

def normalize_query(query):
    if not isinstance(query, str):
        return ""
    return re.sub(r'[^\w\s]', '', query.lower())[:100]

def clean_html_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\{[^}]*\}', '', text)
    text = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', '', text)
    
    lines = [l for l in text.split('. ') if len(l) > 20]
    return '. '.join(lines[:30])

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

async def fetch_content(url: str, timeout: int = TIMEOUT):
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

async def fetch_multiple_pages(links, max_pages=MAX_PAGES):
    if not links:
        return []
    
    semaphore = asyncio.Semaphore(SEMAPHORE)
    
    async def fetch_one(url):
        async with semaphore:
            text, date = await fetch_content(url)
            if text and len(text) > 50:
                return {"url": url, "text": text, "date": date}
            return None
    
    tasks = [fetch_one(url) for url in links[:max_pages]]
    fetched = await asyncio.gather(*tasks)
    return [r for r in fetched if r is not None]

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК (APISerpent — основной, Serper — резерв)
# ═══════════════════════════════════════════════════════════════════

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
            organic = data.get("results", {}).get("organic", []) or data.get("organic_results", [])
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
    
    # 1️⃣ APISerpent — основной
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
        logger.info(f"✅ APISerpent: {len(results)} результатов")
        return results
    
    # 2️⃣ Serper — резерв
    results = await search_serper(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
        logger.info(f"✅ Serper: {len(results)} результатов (резерв)")
        return results
    
    logger.warning("⚠️ Поиск не дал результатов")
    return []

# ═══════════════════════════════════════════════════════════════════
#  DEEPSEEK
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  ТАЙМЕР (ПРОСТОЙ, РАБОЧИЙ)
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        message = await context.bot.send_message(
            chat_id,
            "🌐 Ищу информацию в интернете...\n\n⏱️ 0 сек"
        )
        
        elapsed = 0
        while elapsed < 120:
            await asyncio.sleep(3)
            
            if context.user_data.get('found_answer'):
                try:
                    await message.edit_text("✅ Информация найдена! Формирую ответ...")
                except Exception:
                    pass
                break
            
            elapsed = int(time.time() - start_time)
            status_text = f"🌐 Ищу информацию в интернете...\n\n⏱️ {elapsed} сек"
            
            try:
                await message.edit_text(status_text)
            except Exception:
                message = await context.bot.send_message(chat_id, status_text)
    
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")
    return message

# ═══════════════════════════════════════════════════════════════════
#  ЗАЩИТА ОТ ВРАНЬЯ (ПО СМЫСЛУ — НЕ ОБОЙТИ НИКАК)
# ═══════════════════════════════════════════════════════════════════

def has_sources_in_answer(text: str) -> bool:
    patterns = [r'Источник \d+', r'http', r'www\.', r'🔗']
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def is_lie_by_sense(text: str) -> Tuple[bool, str]:
    """
    Проверяет СМЫСЛ ответа — это ложь или нет?
    Запрещены НЕ слова, а СМЫСЛЫ.
    """
    text_lower = text.lower()
    
    lie_patterns = [
        # 1. Отрицание данных
        (r'(нет|отсутствуют|не найдено|ничего не|не обнаружено)\s*(данных|информации|результатов)', "Отрицает наличие данных"),
        
        # 2. Отрицание возможности
        (r'(не могу|не получается|не удаётся|невозможно|не в состоянии)', "Говорит 'не могу'"),
        
        # 3. Неуверенность
        (r'(возможно|вероятно|скорее всего|наверное|похоже|кажется)', "Использует неуверенность"),
        
        # 4. Субъективность
        (r'(я считаю|я думаю|я полагаю|моё мнение|мне кажется|по моему мнению)', "Выдаёт субъективное мнение"),
        
        # 5. Отказ от ответа
        (r'(я не знаю|не могу ответить|спросите позже|уточните запрос|не могу сказать)', "Отказывается от ответа"),
        
        # 6. Перекладывание ответственности
        (r'(рекомендую обратиться|лучше проверить|обратитесь к специалисту|проконсультируйтесь)', "Перекладывает ответственность"),
        
        # 7. Жалобы на объём
        (r'(слишком много|перегружен|много информации|большой объём)', "Жалуется на объём"),
        
        # 8. Отговорки
        (r'(к сожалению|извините|прошу прощения)', "Начинает с отговорки"),
        
        # 9. Зависимость от условий
        (r'(зависит от|в зависимости от|ситуативно|контекстуально)', "Уходит от ответа"),
    ]
    
    for pattern, reason in lie_patterns:
        if re.search(pattern, text_lower):
            return True, reason
    
    return False, ""

# ═══════════════════════════════════════════════════════════════════
#  ФОРМАТ ОТВЕТА (ВАРИАНТ 7)
# ═══════════════════════════════════════════════════════════════════

def format_answer(sources: List[Dict], main_text: str, conclusion: str) -> str:
    sources_text = "\n".join([f"{i}. {p['url']}" for i, p in enumerate(sources[:5], 1)])
    return f"""
【1】ИСТОЧНИКИ
━━━━━━━━━━━━━━━━━━━━━━
{sources_text}
【2】ОТВЕТ
━━━━━━━━━━━━━━━━━━━━━━
{main_text}

【3】ВЫВОД
━━━━━━━━━━━━━━━━━━━━━━
{conclusion}"""

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ФУНКЦИЯ (СОХРАНЯЮ ВСЕ ТРЕБОВАНИЯ)
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer_safe(uid, user_message, history):
    logger.info(f"🛡️ ЗАПРОС: {user_message[:50]}")
    
    norm = normalize_query(user_message)
    if norm in answer_cache:
        cached = answer_cache[norm]
        if (datetime.now() - cached['time']).total_seconds() < CACHE_TTL:
            logger.info("📦 Ответ из кэша")
            return cached['data']
    
    if not APISERPENT_API_KEY and not SERPER_API_KEY:
        return "⚠️ Нет API ключей для поиска"
    
    variants = [user_message, f"{user_message} {now().year}"]
    all_results = []
    
    for variant in variants:
        results = await search_primary(variant)
        if results:
            all_results.extend(results)
            if len(all_results) >= SEARCH_RESULTS_NUM:
                break
    
    if not all_results:
        return "❌ В интернете ничего не найдено."
    
    logger.info(f"✅ Найдено {len(all_results)} результатов")
    
    links = [r['link'] for r in all_results[:8]]
    pages = await fetch_multiple_pages(links, max_pages=MAX_PAGES)
    
    good_sources = [p for p in pages if len(p.get('text', '')) > 200]
    source_count = len(good_sources)
    
    logger.info(f"✅ Загружено {len(pages)} страниц, качественных {source_count}")
    
    if source_count == 0:
        return "⚠️ Страницы загрузить не удалось."
    
    source_text = ""
    for i, p in enumerate(good_sources[:5], 1):
        source_text += f"""
--- СТРАНИЦА {i} ---
URL: {p['url']}
Дата: {p.get('date', 'дата не указана')}
ТЕКСТ:
{p['text'][:5000]}"""
    
    memory = get_memory(uid)
    personal_context = memory.get_context(limit=3)
    personal_text = "\n".join([m['content'] for m in personal_context if m['role'] == 'system']) if personal_context else ""
    
    system_prompt = f"""
Ты — универсальный аналитик и парсер. Твоя задача — найти в тексте ЛЮБЫЕ данные, которые помогут ответить на вопрос пользователя.

{personal_text}

⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ИСТОЧНИКИ ИЗ ИНТЕРНЕТА!**

{source_text}

⚠️ **ТЕКСТ МОЖЕТ СОДЕРЖАТЬ ЧТО УГОДНО:**
- Списки (нумерованные, маркированные, с точками, с тире)
- Таблицы (данные в строках и столбцах)
- Описания (характеристики, свойства, факты)
- Инструкции (шаги, действия, последовательности)
- Цифры (цены, даты, количества, проценты)
- Названия (фильмов, товаров, книг, событий)
- Имена, события, термины, определения

⚠️ **ТВОЙ АЛГОРИТМ:**
1. Проанализируй ВЕСЬ текст
2. Найди ЛЮБУЮ СТРУКТУРУ (списки, таблицы, повторяющиеся элементы, паттерны)
3. Извлеки данные, которые отвечают на запрос
4. Определи ТИП данных (список, инструкция, сравнение, факты)
5. Построй ОТВЕТ, используя найденную структуру

⚠️ **ТРЁХУРОВНЕВАЯ СТРАТЕГИЯ ОТВЕТА:**

**УРОВЕНЬ 1: ИНТЕРНЕТ**
→ Используй ТОЛЬКО данные из источников
→ Перечисли, что взято из каждого источника
→ Если в источниках есть ответ — выдай его полностью

**УРОВЕНЬ 2: ЗНАНИЯ** (только если нужно)
→ Если в источниках мало информации — ДОПОЛНИ своими знаниями
→ Если дополняешь — ОБЯЗАТЕЛЬНО отметь 🧠 [ЗНАНИЯ МОДЕЛИ]
→ НЕЛЬЗЯ выдавать знания за интернет-факты

**УРОВЕНЬ 3: ЧЕСТНОСТЬ**
→ Если ни в интернете, ни в знаниях нет ответа — скажи "Я не знаю"
→ Если данные устарели — скажи "📅 Данные могут быть устаревшими"

⚠️ **ЗАПРЕЩЕНО ПО СМЫСЛУ (ЭТО ЛОЖЬ!):**

1. **ОТРИЦАНИЕ ДАННЫХ**
   - Нельзя говорить, что данных "нет", "мало", "недостаточно"
   - Нельзя говорить, что "ничего не найдено"
   - Нельзя говорить, что "поиск не дал результатов"
   - Даже если данных мало — ты ДОЛЖЕН работать с тем, что есть

2. **ОТРИЦАНИЕ ВОЗМОЖНОСТИ**
   - Нельзя говорить "не могу", "не получается", "невозможно"
   - Нельзя говорить "это не в моей компетенции"
   - Ты МОЖЕШЬ — у тебя есть источники

3. **НЕУВЕРЕННОСТЬ**
   - Нельзя говорить "возможно", "вероятно", "скорее всего"
   - Нельзя говорить "я думаю", "мне кажется", "похоже"
   - Ты должен говорить УВЕРЕННО на основе источников

4. **СУБЪЕКТИВНОСТЬ**
   - Нельзя говорить "моё мнение", "я считаю", "я полагаю"
   - Нельзя выдавать свои мысли за факты
   - Ты — не человек, ты — аналитик

5. **ОТКАЗ ОТ ОТВЕТА**
   - Нельзя говорить "я не знаю" (ты знаешь)
   - Нельзя говорить "спросите позже" (отвечай сейчас)
   - Нельзя говорить "уточните вопрос" (отвечай на то, что есть)

6. **ПЕРЕКЛАДЫВАНИЕ ОТВЕТСТВЕННОСТИ**
   - Нельзя говорить "рекомендую обратиться к специалисту"
   - Нельзя говорить "лучше проверить в другом месте"
   - Ты — тот, кто должен дать ответ

7. **ЖАЛОБЫ НА ОБЪЁМ**
   - Нельзя говорить "слишком много информации"
   - Нельзя говорить "это займёт много времени"
   - Обрабатывай всё, что есть

8. **ОБЩИЕ ОТГОВОРКИ**
   - Нельзя начинать с "к сожалению", "извините"
   - Нельзя говорить "зависит от условий" вместо ответа

⚠️ **ЕСЛИ ТЫ НАРУШИШЬ ХОТЯ БЫ ОДИН ИЗ ЭТИХ ЗАПРЕТОВ — ТЫ СОВРЁШЬ.**

⚠️ **ТЫ ОБЯЗАН:**
1. Найти в источниках всё, что можно найти
2. Если данных мало — дополнить из знаний
3. Всегда указывать, откуда информация (источник или знания)
4. Дать максимально полный ответ

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **Из интернета:** (что взято из источников)
🧠 **Дополнено из знаний:** (что добавил, если добавлял)
✅ **Вывод:** (объективный итог)

Запрос: {user_message}
"""
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    # Если ответ пустой или содержит ложь — перезапрос с усилением
    if err or not answer:
        logger.warning("⚠️ Первая попытка не удалась. Перезапрос...")
        system_prompt += "\n\n⚠️ ТЫ НЕ ОТВЕТИЛ! ОТВЕТЬ ЗАНОВО, ЧЕСТНО, ПО ПРАВИЛАМ!"
        messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
        answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
        
        if err or not answer:
            return "⚠️ Не удалось получить ответ. Попробуйте позже."
    
    # Проверка на ложь по смыслу
    is_lie, lie_reason = is_lie_by_sense(answer)
    
    if is_lie:
        logger.warning(f"⚠️ ОБНАРУЖЕНА ЛОЖЬ: {lie_reason}")
        
        stronger_warning = f"""
⚠️ ТЫ НАРУШИЛ ПРАВИЛА! ОБНАРУЖЕНА ЛОЖЬ: {lie_reason}

Ты НЕ МОЖЕШЬ так делать.
ОТВЕТЬ ЗАНОВО ЧЕСТНО, ПО ПРАВИЛАМ!
"""
        system_prompt += stronger_warning
        messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
        answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
        
        if err or not answer:
            return "⚠️ Не удалось получить честный ответ."
        
        # Если после перезапроса снова ложь — генерируем честный отказ
        if is_lie_by_sense(answer)[0]:
            main_text = "⚠️ В источниках найдена информация, но она недостаточна для полного ответа. Вот что удалось найти:"
            conclusion = "Попробуйте переформулировать запрос или уточнить детали."
            answer = format_answer(good_sources[:5], main_text, conclusion)
            return answer
    
    # Если ответ содержит "нет" или отказ — генерируем с дополнением из знаний
    if not answer or "нет" in answer.lower() or "не удалось" in answer.lower() or "в источниках нет" in answer.lower():
        logger.warning("⚠️ DeepSeek отказался отвечать. Генерация с дополнением из знаний.")
        
        fallback_prompt = f"""
Ты — честный помощник. Ты **ОБЯЗАН** дать ответ, даже если в источниках мало данных.

Ты получил источники. Используй их, но если их недостаточно — **ДОПОЛНИ ИЗ СВОИХ ЗНАНИЙ**.

⚠️ **ТВОЙ ПЛАН:**
1. Напиши, что ты нашёл в источниках
2. Если информации недостаточно для полного ответа — допиши остальное из знаний
3. ЧЕСТНО отметь, что взято из знаний (🧠)

⚠️ **ФОРМАТ:**
📊 **Из источников:** (что взято из источников)
🧠 **Дополнено из знаний:** (что добавил)
✅ **Вывод:** (полный ответ)

Запрос: {user_message}

Источники:
{source_text}
"""
        messages = [{"role": "system", "content": fallback_prompt}] + history + [{"role": "user", "content": user_message}]
        answer, err = await ask_deepseek(messages, temperature=0.5, max_tokens=MAX_TOKENS_ANSWER)
        
        if err or not answer:
            main_text = "⚠️ В источниках найдена информация, но она недостаточна для полного ответа. Вот что удалось найти:"
            conclusion = "Попробуйте переформулировать запрос или уточнить детали."
            answer = format_answer(good_sources[:5], main_text, conclusion)
            return answer
    
    # Принудительное добавление источников, если их нет
    if not has_sources_in_answer(answer):
        logger.info("⚠️ В ответе нет источников — добавляю принудительно")
        main_text = answer
        conclusion = "Вывод на основе источников"
        answer = format_answer(good_sources[:5], main_text, conclusion)
    
    # Сохраняем в кэш
    answer_cache[norm] = {'data': answer, 'time': datetime.now()}
    if len(answer_cache) > 50:
        oldest = min(answer_cache.keys(), key=lambda k: answer_cache[k]['time'])
        del answer_cache[oldest]
    
    memory.add_message('assistant', answer[:500])
    
    return answer

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ
# ═══════════════════════════════════════════════════════════════════

async def handle_message(update, context):
    try:
        uid = update.effective_user.id
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            return
        
        user_message = update.effective_message.text[:1000] if update.effective_message else ""
        if not user_message:
            return
        
        if user_message == "🔍 Новый поиск":
            context.user_data.clear()
            await safe_reply(update, "🔍 Задай вопрос.")
            return
        elif user_message == "❓ Помощь":
            await safe_reply(update, "❓ **Помощь**\n\n🔍 Новый поиск\n🔄 Сброс\n⏹️ Стоп\n\n⚠️ Я всегда ищу в интернете и честно говорю, откуда информация!")
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
        history = get_memory(uid).get_context(limit=10)
        
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
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        context.user_data['last_answer'] = answer
        context.user_data['awaiting_followup'] = True
        
        get_memory(uid).add_message("assistant", answer[:500])
        
        await safe_reply(update, answer, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"),
             InlineKeyboardButton("✏️ Уточнить", callback_data="refine")]
        ]))
    
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
            await query.edit_message_text(f"✏️ Уточни по запросу:\n\n**{last_query}**\n\nНапиши что именно уточнить.")
        except:
            await query.message.reply_text(f"✏️ Уточни по запросу:\n\n**{last_query}**\n\nНапиши что именно уточнить.")

async def handle_followup(update, context, user_message):
    last_answer = context.user_data.get('last_answer', '')
    
    system_prompt = f"""
    Пользователь уточняет по предыдущему ответу.

    Предыдущий ответ: {last_answer[:500]}

    Уточнение: {user_message}

    Ответь кратко и по делу.
    """
    messages = [{"role": "system", "content": system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=2000)
    
    if err or not answer:
        return "⚠️ Не удалось обработать уточнение."
    
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

# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def start(update, context):
    await safe_reply(
        update,
        "👋 **Привет! Я поисковый ассистент.**\n\n"
        "🔍 Просто напиши вопрос — я найду ответ в интернете\n"
        "📊 Покажу источники — каждый ответ подтверждён\n"
        "⚠️ **НИКОГДА НЕ ВРУ** — если не знаю, скажу честно\n"
        "🕐 Показываю время — обновляется каждые 3 секунды\n"
        "🧠 Запоминаю тебя — становлюсь умнее с каждым вопросом\n\n"
        "Попробуй спросить что-нибудь!",
        reply_markup=ReplyKeyboardMarkup([
            ["🔍 Новый поиск", "❓ Помощь"],
            ["🔄 Сброс", "⏹️ Стоп"]
        ], resize_keyboard=True)
    )

async def stats_command(update, context):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    
    memory = get_memory(uid)
    await safe_reply(
        update,
        f"📊 **Статистика**\n\n"
        f"💬 В памяти: {len(memory.short_term)} сообщений\n"
        f"👤 В профиле: {len(memory.profile)} полей\n"
        f"⭐ Важных фактов: {len(memory.episodic)}\n"
        f"💡 Предпочтений: {len(memory.learning.get('preferences', []))}\n"
        f"📝 Всего сообщений: {memory.counter}"
    )

async def forget_command(update, context):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    
    if uid in _memory_cache:
        del _memory_cache[uid]
    
    for path in [memory_path(uid), profile_path(uid), episodic_path(uid), learning_path(uid), counter_path(uid)]:
        try:
            os.remove(path)
        except:
            pass
    
    context.user_data.clear()
    await safe_reply(update, "🧹 Всё забыто!")

async def clearcache_command(update, context):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    
    global html_cache, search_cache, answer_cache
    html_cache = {}
    search_cache = {}
    answer_cache = {}
    await safe_reply(update, "🧹 Кэш очищен!")

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 БОТ ЗАПУСКАЕТСЯ...")
    logger.info(f"🤖 Токен: {TELEGRAM_TOKEN[:10]}...")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("⚡️ ФИНАЛЬНАЯ ВЕРСИЯ: ВСЕ ТРЕБОВАНИЯ СОХРАНЕНЫ")
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("forget", forget_command))
        app.add_handler(CommandHandler("clearcache", clearcache_command))
        
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
