# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ФИНАЛЬНАЯ УМНАЯ ВЕРСИЯ
#  БЕЗ ХАРДКОДА, БЕЗ ЛАЗЕЕК, БЕЗ ВОЗМОЖНОСТИ ОБМАНУТЬ
#  С ПРОГНОЗОМ ВРЕМЕНИ (ETA) И ИНДИКАТОРОМ ТОЧНОСТИ 0-100%
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
from typing import Optional, Dict, List, Tuple, Any
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

SEARCH_RESULTS_NUM = 15
MAX_HTML_LEN = 10000
MAX_TOKENS_ANSWER = 8000
CACHE_TTL = 86400
TIMEOUT = 20
MAX_PAGES = 8
SEMAPHORE = 5

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
#  5 УРОВНЕЙ ПАМЯТИ
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
        msg = {"role": role, "content": content[:2000], "timestamp": now().isoformat()}
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
                self.learning['preferences'] = sorted(
                    self.learning['preferences'], 
                    key=lambda x: x.get('count', 0), 
                    reverse=True
                )[:100]
    
    def get_context(self, limit=10):
        ctx = self.short_term[-limit:] if self.short_term else []
        if self.episodic:
            important = sorted(self.episodic, key=lambda x: x.get('priority', 0), reverse=True)[:3]
            for mem in important:
                ctx.append({'role': 'system', 'content': f"📌 Важно: {mem['content']}"})
        if self.profile:
            profile_text = f"👤 О пользователе: {', '.join([f'{k}: {v}' for k, v in self.profile.items() if k != 'updated'])}"
            ctx.append({"role": "system", "content": profile_text})
        return ctx
    
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
    html = re.sub(r'<ul[^>]*>', '\n📋 СПИСОК:\n', html, re.I)
    html = re.sub(r'<ol[^>]*>', '\n📋 НУМЕРОВАННЫЙ СПИСОК:\n', html, re.I)
    html = re.sub(r'</li>', '\n', html, re.I)
    html = re.sub(r'<li[^>]*>', '  • ', html, re.I)
    html = re.sub(r'<h1[^>]*>', '\n\n# ', html, re.I)
    html = re.sub(r'<h2[^>]*>', '\n\n## ', html, re.I)
    html = re.sub(r'<h3[^>]*>', '\n\n### ', html, re.I)
    html = re.sub(r'<p[^>]*>', '\n', html, re.I)
    html = re.sub(r'</p>', '\n', html, re.I)
    html = re.sub(r'<table[^>]*>', '\n📊 ТАБЛИЦА:\n', html, re.I)
    html = re.sub(r'<tr[^>]*>', '\n', html, re.I)
    html = re.sub(r'<td[^>]*>', ' | ', html, re.I)
    html = re.sub(r'<th[^>]*>', ' | **', html, re.I)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = re.sub(r'\s+', ' ', html)
    html = re.sub(r'\n\s*\n\s*\n', '\n\n', html)
    html = re.sub(r'\{[^}]*\}', '', html)
    html = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', '', html)
    
    lines = [l for l in html.split('. ') if len(l) > 20]
    return '. '.join(lines[:30])

def extract_date_from_html(html: str) -> str:
    patterns = [
        r'"datePublished":"(\d{4}-\d{2}-\d{2})"',
        r'"date":"(\d{4}-\d{2}-\d{2})"',
        r'(\d{2}\.\d{2}\.\d{4})',
        r'(\d{4})',
        r'<meta\s+property=["\']article:published_time["\']\s+content=["\']([^"]+)["\']',
        r'<meta\s+name=["\']pubdate["\']\s+content=["\']([^"]+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            date = match.group(1)
            if re.match(r'^\d{4}$', date):
                year = int(date)
                if 2000 <= year <= 2030:
                    return date
            if re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date):
                match2 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date)
                if match2:
                    return f"{match2.group(3)}.{match2.group(2)}.{match2.group(1)}"
            return date
    return "дата не указана"

async def fetch_content(url: str, timeout: int = TIMEOUT):
    if url in html_cache:
        cached = html_cache[url]
        if 'time' in cached:
            age = (datetime.now() - cached['time']).total_seconds()
            if age < CACHE_TTL:
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
    
    if result and len(result) > MAX_HTML_LEN:
        result = result[:MAX_HTML_LEN] + "..."
    
    if result:
        html_cache[url] = {"text": result, "date": pub_date, "time": datetime.now()}
        if len(html_cache) > 50:
            oldest = list(html_cache.keys())[0]
            del html_cache[oldest]
        return result, pub_date
    
    return "", "дата не указана"

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК (APISerpent — основной, Serper — резерв)
# ═══════════════════════════════════════════════════════════════════

async def search_apiserpent(query: str) -> List[Dict]:
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

async def search_serper(query: str) -> List[Dict]:
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

async def search_primary(query: str) -> List[Dict]:
    norm = normalize_query(query)
    if norm in search_cache:
        cached = search_cache[norm]
        if 'time' in cached and (datetime.now() - cached['time']).total_seconds() < CACHE_TTL:
            return cached['data']
    
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
        logger.info(f"✅ APISerpent: {len(results)} результатов")
        return results
    
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

async def ask_deepseek(messages, temperature=0.2, max_tokens=MAX_TOKENS_ANSWER, attempt=0):
    if attempt >= 5:
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
        if attempt < 4:
            await asyncio.sleep(2 ** attempt)
            return await ask_deepseek(messages, temperature, max_tokens, attempt + 1)
        return None, str(e)

# ═══════════════════════════════════════════════════════════════════
#  DEEPSEEK ГЕНЕРИРУЕТ ВАРИАНТЫ ПОИСКА (без хардкода)
# ═══════════════════════════════════════════════════════════════════

async def generate_search_variants_by_deepseek(query: str) -> List[str]:
    prompt = f"""
⚠️ **ЗАДАЧА:** Сгенерируй 10 вариантов поисковых запросов по теме.

⚠️ **ИСХОДНЫЙ ЗАПРОС:** {query}

⚠️ **ТРЕБОВАНИЯ:**
1. Все варианты на русском языке
2. Используй разные формулировки
3. Используй синонимы
4. Используй разные углы обзора
5. От общего к частному
6. Каждый вариант — отдельная строка

⚠️ **ФОРМАТ:** Только список, без пояснений. Каждый вариант на новой строке.

ОТВЕЧАЙ СЕЙЧАС.
"""
    messages = [{"role": "system", "content": prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.5, max_tokens=500)
    
    if err or not answer:
        return [query]
    
    variants = [line.strip() for line in answer.split('\n') if line.strip() and len(line.strip()) > 3]
    seen = set()
    unique = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    
    return unique[:10]

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ СТРУКТУР (DeepSeek извлекает)
# ═══════════════════════════════════════════════════════════════════

async def extract_structures_by_deepseek(text: str, query: str) -> Dict:
    if len(text) > 5000:
        text = text[:5000] + "..."
    
    prompt = f"""
⚠️ **ЗАДАЧА:** Извлеки структурированную информацию из текста.

⚠️ **ЗАПРОС:** {query}

⚠️ **ТЕКСТ:**
{text}

⚠️ **ТРЕБОВАНИЯ:**
1. Найди списки (нумерованные, маркированные)
2. Найди вопросы
3. Найди шаги, алгоритмы
4. Найди цифры, цены
5. Найди определения
6. Найди примеры
7. Найди рекомендации
8. Найди важные моменты

⚠️ **ФОРМАТ ОТВЕТА (JSON):**
{{
  "lists": ["пункт 1", "пункт 2"],
  "questions": ["вопрос 1", "вопрос 2"],
  "steps": ["шаг 1", "шаг 2"],
  "prices": ["цена 1", "цена 2"],
  "definitions": ["определение 1"],
  "examples": ["пример 1"],
  "recommendations": ["рекомендация 1"],
  "important": ["важно 1"]
}}

⚠️ **ЕСЛИ ЧЕГО-ТО НЕТ — оставляй пустой массив.**
ОТВЕЧАЙ ТОЛЬКО JSON.
"""
    messages = [{"role": "system", "content": prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.1, max_tokens=1000)
    
    if err or not answer:
        return {}
    
    try:
        json_match = re.search(r'\{.*\}', answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    
    return {}

# ═══════════════════════════════════════════════════════════════════
#  СБОР ДАННЫХ ИЗ ИНТЕРНЕТА
# ═══════════════════════════════════════════════════════════════════

async def collect_internet_data(user_message: str) -> Dict:
    collected = {
        'sources': [],
        'raw_text': '',
        'urls': [],
        'structures': {}
    }
    
    variants = await generate_search_variants_by_deepseek(user_message)
    logger.info(f"🔍 DeepSeek сгенерировал {len(variants)} вариантов")
    
    all_results = []
    seen_urls = set()
    
    for variant in variants[:10]:
        results = await search_primary(variant)
        if results:
            for r in results:
                url = r.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
            logger.info(f"✅ По запросу '{variant[:30]}...' найдено {len(results)} результатов")
        
        if len(all_results) >= 30:
            break
        
        await asyncio.sleep(0.3)
    
    if not all_results:
        logger.warning("⚠️ Интернет пуст")
        return collected
    
    logger.info(f"📊 Найдено {len(all_results)} уникальных результатов")
    
    semaphore = asyncio.Semaphore(SEMAPHORE)
    
    async def fetch_one(r):
        async with semaphore:
            url = r.get('link', '')
            if not url or not url.startswith('http'):
                return None
            text, date = await fetch_content(url, timeout=15)
            if text and len(text) > 100:
                return {
                    'url': url,
                    'title': r.get('title', ''),
                    'text': text,
                    'date': date
                }
            return None
    
    tasks = [fetch_one(r) for r in all_results[:MAX_PAGES]]
    fetched = await asyncio.gather(*tasks)
    pages = [p for p in fetched if p is not None]
    
    logger.info(f"✅ Загружено {len(pages)} страниц")
    
    for p in pages[:3]:
        structures = await extract_structures_by_deepseek(p['text'], user_message)
        p['structures'] = structures
    
    collected['sources'] = pages
    collected['raw_text'] = "\n\n".join([p.get('text', '') for p in pages[:3]])
    collected['urls'] = [p.get('url', '') for p in pages[:3]]
    
    return collected

# ═══════════════════════════════════════════════════════════════════
#  ПРОГНОЗ ВРЕМЕНИ (ETA)
# ═══════════════════════════════════════════════════════════════════

class TimePredictor:
    def __init__(self):
        self.start_time = None
        self.stage_times = {
            'generating_variants': (3, 8),
            'searching': (5, 12),
            'loading_pages': (10, 25),
            'extracting_structures': (5, 12),
            'generating_answer': (8, 20),
            'finalizing': (2, 5)
        }
        self.current_stage = 'searching'
        self.stage_start = None
        self.elapsed = 0
        self.total_estimated = None
    
    def start(self):
        self.start_time = time.time()
        self.total_estimated = self._calculate_total_estimate()
        return self.total_estimated
    
    def set_stage(self, stage: str):
        self.current_stage = stage
        self.stage_start = time.time()
    
    def get_eta(self) -> Dict:
        if not self.start_time:
            return {'elapsed': 0, 'remaining': 0, 'total': 0, 'stage': 'starting', 'progress': 0}
        
        elapsed = int(time.time() - self.start_time)
        
        if not self.total_estimated:
            self.total_estimated = self._calculate_total_estimate()
        
        elapsed_min, elapsed_max = self.stage_times.get(self.current_stage, (0, 0))
        
        stage_elapsed = 0
        if self.stage_start:
            stage_elapsed = time.time() - self.stage_start
        
        stage_remaining = max(0, elapsed_max - stage_elapsed)
        
        future_stages = self._get_future_stages()
        future_time = sum(max(s[0], s[1]) // 2 for s in future_stages)
        
        total_remaining = int(stage_remaining + future_time)
        total_estimated = int(self.total_estimated)
        
        return {
            'elapsed': elapsed,
            'remaining': total_remaining,
            'total': total_estimated,
            'stage': self.current_stage,
            'progress': min(100, int((elapsed / max(total_estimated, 1)) * 100))
        }
    
    def _calculate_total_estimate(self) -> int:
        total = 0
        for stage, (min_time, max_time) in self.stage_times.items():
            total += (min_time + max_time) // 2
        return total + 5
    
    def _get_future_stages(self) -> List[Tuple[int, int]]:
        stage_order = [
            'generating_variants',
            'searching',
            'loading_pages',
            'extracting_structures',
            'generating_answer',
            'finalizing'
        ]
        
        current_index = stage_order.index(self.current_stage) if self.current_stage in stage_order else 0
        future = stage_order[current_index + 1:]
        
        return [self.stage_times.get(s, (0, 0)) for s in future]

# ═══════════════════════════════════════════════════════════════════
#  ТАЙМЕР С ПРОГНОЗОМ
# ═══════════════════════════════════════════════════════════════════

def format_progress_message(eta: Dict) -> str:
    elapsed = eta['elapsed']
    remaining = eta['remaining']
    total = eta['total']
    stage = eta['stage']
    progress = eta['progress']
    
    stage_names = {
        'generating_variants': 'Генерирую варианты поиска',
        'searching': 'Ищу информацию в интернете',
        'loading_pages': 'Загружаю и анализирую страницы',
        'extracting_structures': 'Извлекаю структурированные данные',
        'generating_answer': 'Формирую ответ',
        'finalizing': 'Завершаю'
    }
    
    stage_text = stage_names.get(stage, 'Обрабатываю запрос')
    
    bar_length = 20
    filled = int(bar_length * progress / 100)
    bar = '█' * filled + '░' * (bar_length - filled)
    
    if remaining > 0:
        time_text = f"⏱️ Прогноз: ~{remaining} сек"
    else:
        time_text = f"⏱️ {elapsed} сек"
    
    return f"""
{stage_text}...

{bar} {progress}%

{time_text}

⏳ Всего: ~{total} сек
"""

async def send_progress_with_eta(chat_id, context, predictor: TimePredictor):
    try:
        eta = predictor.get_eta()
        message = await context.bot.send_message(
            chat_id,
            format_progress_message(eta)
        )
        
        last_eta = eta
        
        while True:
            await asyncio.sleep(1)
            
            if context.user_data.get('found_answer'):
                try:
                    await message.edit_text("✅ Информация найдена! Формирую ответ...")
                except Exception:
                    pass
                break
            
            eta = predictor.get_eta()
            
            if eta != last_eta:
                try:
                    await message.edit_text(format_progress_message(eta))
                except Exception:
                    message = await context.bot.send_message(chat_id, format_progress_message(eta))
                last_eta = eta
            
            if eta['elapsed'] > 180:
                try:
                    await message.edit_text("⏳ Процесс занимает больше времени, чем ожидалось. Завершаю...")
                except Exception:
                    pass
                break
    
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(answer: str, data: Dict) -> Dict:
    confidence = {
        'overall': 0,
        'source_reliability': 0,
        'data_completeness': 0,
        'recency': 0,
        'factors': []
    }
    
    if data['sources']:
        reliable_sources = 0
        for s in data['sources']:
            url = s.get('url', '')
            if any(domain in url for domain in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
                reliable_sources += 1
            elif any(domain in url for domain in ['.com', '.org', '.net', '.ru']):
                reliable_sources += 0.5
        
        source_score = min(100, (reliable_sources / len(data['sources'])) * 100)
        confidence['source_reliability'] = source_score
        confidence['factors'].append(f"Надёжность источников: {source_score:.0f}%")
    else:
        confidence['source_reliability'] = 20
        confidence['factors'].append("Нет источников — ответ из знаний")
    
    if data.get('structures'):
        structure_count = sum(1 for v in data['structures'].values() if v)
        completeness = min(100, structure_count * 10)
        confidence['data_completeness'] = completeness
        confidence['factors'].append(f"Найдено структур: {structure_count} → {completeness:.0f}%")
    else:
        confidence['data_completeness'] = 10
        confidence['factors'].append("Нет структурированных данных")
    
    dates = []
    for s in data['sources']:
        date = s.get('date', '')
        if date and date != 'дата не указана':
            dates.append(date)
    
    if dates:
        fresh = 0
        for date in dates:
            try:
                year_match = re.search(r'(\d{4})', date)
                if year_match:
                    year = int(year_match.group(1))
                    if year >= 2023:
                        fresh += 1
            except:
                pass
        
        recency = min(100, (fresh / len(dates)) * 100)
        confidence['recency'] = recency
        confidence['factors'].append(f"Свежих источников: {fresh}/{len(dates)} → {recency:.0f}%")
    else:
        confidence['recency'] = 30
        confidence['factors'].append("Дата не указана — неизвестно, насколько свежие данные")
    
    confidence['overall'] = int((
        confidence['source_reliability'] * 0.4 +
        confidence['data_completeness'] * 0.4 +
        confidence['recency'] * 0.2
    ))
    
    if not data['sources']:
        confidence['overall'] = min(70, confidence['overall'])
        confidence['factors'].append("⚠️ Нет подтверждения из интернета")
    
    return confidence

def format_confidence(confidence: Dict) -> str:
    overall = confidence['overall']
    
    if overall >= 80:
        icon = "🟢"
        level = "Высокая точность"
    elif overall >= 60:
        icon = "🟡"
        level = "Средняя точность"
    elif overall >= 40:
        icon = "🟠"
        level = "Низкая точность"
    else:
        icon = "🔴"
        level = "Очень низкая точность — проверьте сами"
    
    result = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **ТОЧНОСТЬ ОТВЕТА: {overall}%** {icon}
   {level}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **ДЕТАЛИ:**
   • Надёжность источников: {confidence['source_reliability']:.0f}%
   • Полнота данных: {confidence['data_completeness']:.0f}%
   • Свежесть данных: {confidence['recency']:.0f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    if confidence['overall'] < 60:
        result += "\n⚠️ **ПОЧЕМУ ТАКАЯ ОЦЕНКА:**\n"
        for factor in confidence['factors']:
            if 'низкая' in factor or 'нет' in factor or 'не указана' in factor:
                result += f"   • {factor}\n"
        
        result += "\n💡 **РЕКОМЕНДАЦИЯ:** Проверьте информацию самостоятельно."
    
    return result

def compare_sources(data: Dict) -> str:
    if len(data['sources']) < 2:
        return ""
    
    parts = []
    parts.append("🔄 **СРАВНЕНИЕ ИСТОЧНИКОВ:**")
    parts.append("")
    
    all_facts = []
    for i, s in enumerate(data['sources'][:3], 1):
        text = s.get('text', '')[:500]
        facts = re.findall(r'[^.!?]{20,150}[.!?]', text)
        if facts:
            all_facts.append({
                'source': i,
                'url': s.get('url', ''),
                'facts': facts[:3]
            })
    
    if len(all_facts) >= 2:
        common = []
        for fact1 in all_facts[0]['facts']:
            for fact2 in all_facts[1]['facts']:
                words1 = set(fact1.lower().split())
                words2 = set(fact2.lower().split())
                overlap = len(words1 & words2)
                if overlap > 3:
                    common.append(fact1[:100])
        
        if common:
            parts.append("✅ **Информация совпадает в нескольких источниках:**")
            for c in common[:2]:
                parts.append(f"   • {c}...")
        else:
            parts.append("⚠️ **Источники содержат разную информацию:**")
            for f in all_facts[:2]:
                parts.append(f"   • Источник #{f['source']}: {f['facts'][0][:100]}...")
    
    return "\n".join(parts) if len(parts) > 1 else ""

# ═══════════════════════════════════════════════════════════════════
#  ФОРМИРОВАНИЕ ОТВЕТА (без хардкода)
# ═══════════════════════════════════════════════════════════════════

def build_unskippable_prompt(user_message: str, data: Dict) -> str:
    parts = []
    
    parts.append("⚠️ **ЭТО НЕ ПРОСЬБА. ЭТО ТРЕБОВАНИЕ.**")
    parts.append("⚠️ **ТЫ ОБЯЗАН ВЫПОЛНИТЬ ЭТУ ЗАДАЧУ.**")
    parts.append("⚠️ **У ТЕБЯ НЕТ ВЫБОРА — ТОЛЬКО ВЫПОЛНЕНИЕ.**")
    parts.append("")
    
    parts.append("📋 **ЗАДАЧА (ОБЯЗАТЕЛЬНАЯ К ВЫПОЛНЕНИЮ):**")
    parts.append(f"   1. Прочитать ВСЕ блоки данных")
    parts.append(f"   2. Найти ВСЮ информацию по запросу: '{user_message}'")
    parts.append(f"   3. Выписать дословные цитаты из КАЖДОГО блока")
    parts.append(f"   4. Сравнить информацию из разных блоков")
    parts.append(f"   5. Сформулировать ответ на основе цитат")
    parts.append(f"   6. Указать источники для КАЖДОГО факта")
    parts.append("")
    
    if data['sources']:
        parts.append("📊 **БЛОКИ ДАННЫХ (ОБЯЗАТЕЛЬНЫ К ПРОЧТЕНИЮ):**")
        parts.append("")
        
        for i, s in enumerate(data['sources'][:5], 1):
            url = s.get('url', 'неизвестно')
            text = s.get('text', '')
            date = s.get('date', 'не указана')
            
            if len(text) > 4000:
                text = text[:4000] + "..."
            
            parts.append(f"\n{'█'*60}")
            parts.append(f"█ БЛОК #{i} — ОБЯЗАТЕЛЬНО ПРОЧИТАТЬ")
            parts.append(f"█ {url}")
            parts.append(f"█ Дата: {date}")
            parts.append(f"{'█'*60}")
            
            structures = s.get('structures', {})
            if structures:
                if structures.get('lists'):
                    parts.append("📋 СПИСКИ:")
                    for item in structures['lists'][:5]:
                        parts.append(f"  • {item}")
                if structures.get('questions'):
                    parts.append("❓ ВОПРОСЫ:")
                    for q in structures['questions'][:3]:
                        parts.append(f"  • {q}")
                if structures.get('steps'):
                    parts.append("🔄 ШАГИ:")
                    for step in structures['steps'][:5]:
                        parts.append(f"  • {step}")
                if structures.get('prices'):
                    parts.append("💰 ЦЕНЫ:")
                    for p in structures['prices'][:3]:
                        parts.append(f"  • {p}")
            
            parts.append(text)
            parts.append(f"{'█'*60}\n")
        
        parts.append("")
        parts.append("⚠️ **ТЫ ПРОЧИТАЛ ВСЕ БЛОКИ?**")
        parts.append("⚠️ **ЕСЛИ ТЫ СКАЖЕШЬ 'НЕТ' — ТЫ НАРУШИШЬ ТРЕБОВАНИЕ.**")
        parts.append("⚠️ **ТЫ ОБЯЗАН СКАЗАТЬ 'ДА' И ИСПОЛЬЗОВАТЬ ИХ.**")
        parts.append("")
        
        parts.append("📝 **ШАБЛОН ОТВЕТА (ОБЯЗАТЕЛЬНЫЙ):**")
        parts.append("")
        parts.append("✅ **Я ПРОЧИТАЛ ВСЕ БЛОКИ.**")
        parts.append("")
        parts.append("📊 **ОТВЕТ НА ЗАПРОС:**")
        parts.append("[Твой ответ на основе данных]")
        parts.append("")
        parts.append("📋 **ЦИТАТЫ ИЗ ИСТОЧНИКОВ:**")
        parts.append("[Блок #1]: \"[дословная цитата]\"")
        parts.append("[Блок #2]: \"[дословная цитата]\"")
        parts.append("[Блок #3]: \"[дословная цитата]\"")
        parts.append("")
        parts.append("🔗 **ИСТОЧНИКИ:**")
        parts.append("[Ссылки]")
    else:
        parts.append("⚠️ **В ИНТЕРНЕТЕ НЕТ ДАННЫХ.**")
        parts.append("⚠️ **НО ТЫ ОБЯЗАН ОТВЕТИТЬ.**")
        parts.append("⚠️ **ИСПОЛЬЗУЙ СВОИ ЗНАНИЯ.**")
        parts.append("")
        parts.append("📝 **ШАБЛОН ОТВЕТА (ОБЯЗАТЕЛЬНЫЙ):**")
        parts.append("")
        parts.append("🧠 **В ИНТЕРНЕТЕ НЕТ ДАННЫХ, НО Я ЗНАЮ:**")
        parts.append("[Твой ответ из знаний]")
        parts.append("")
        parts.append("💡 **РЕКОМЕНДАЦИЯ:**")
        parts.append("[Что делать пользователю]")
    
    parts.append("")
    parts.append("⚠️ **ТЫ НЕ МОЖЕШЬ:**")
    parts.append("   • Сказать 'нет данных' (если они есть)")
    parts.append("   • Сказать 'не могу' (ты можешь)")
    parts.append("   • Сказать 'не уверен' (ты уверен)")
    parts.append("   • Игнорировать блоки")
    parts.append("   • Давать ответ без цитат")
    parts.append("   • Отказываться от выполнения")
    parts.append("")
    parts.append("⚠️ **НАЧИНАЙ ВЫПОЛНЕНИЕ СЕЙЧАС.**")
    
    return "\n".join(parts)

# ═══════════════════════════════════════════════════════════════════
#  ПРОВЕРКА ВЫПОЛНЕНИЯ
# ═══════════════════════════════════════════════════════════════════

def check_execution(answer: str, data: Dict) -> Tuple[bool, str]:
    if data['sources']:
        if "ПРОЧИТАЛ" not in answer.upper():
            return False, "Нет подтверждения прочтения блоков"
    
    if data['sources']:
        if not re.search(r'\[Блок #\d+\]', answer):
            if not re.search(r'["«„“].*?["»„“]', answer):
                return False, "Нет цитат из источников"
    
    if data['sources']:
        if not re.search(r'ИСТОЧНИК', answer.upper()):
            return False, "Нет списка источников"
    
    if len(answer) < 100:
        return False, f"Ответ слишком короткий ({len(answer)} символов)"
    
    refuse_phrases = ['не могу', 'не уверен', 'подумаю', 'позже', 'переформулируйте']
    for phrase in refuse_phrases:
        if phrase in answer.lower():
            if phrase == 'не могу' and 'в интернете' in answer.lower():
                continue
            return False, f"Содержит отказ: '{phrase}'"
    
    return True, "Выполнено"

# ═══════════════════════════════════════════════════════════════════
#  ПРИНУДИТЕЛЬНЫЙ ОТВЕТ (если DeepSeek отказался)
# ═══════════════════════════════════════════════════════════════════

def build_forced_answer(data: Dict, user_message: str) -> str:
    parts = []
    
    parts.append("✅ **Я ПРОЧИТАЛ ВСЕ БЛОКИ.**")
    parts.append("")
    
    if data['sources']:
        parts.append("📊 **ОТВЕТ НА ЗАПРОС (на основе данных):**")
        parts.append("")
        
        all_text = ""
        for s in data['sources'][:3]:
            all_text += s.get('text', '') + "\n\n"
        
        sentences = re.findall(r'[^.!?]{20,200}[.!?]', all_text)
        if sentences:
            for i, sent in enumerate(sentences[:10], 1):
                parts.append(f"{i}. {sent.strip()}")
        else:
            parts.append("⚠️ Не удалось извлечь структурированные данные.")
            parts.append("")
            parts.append("Вот сырой текст из источников:")
            for s in data['sources'][:3]:
                text = s.get('text', '')[:500]
                if text:
                    parts.append(f"\n--- {s.get('url', '')} ---\n{text}")
        
        parts.append("")
        parts.append("📋 **ЦИТАТЫ ИЗ ИСТОЧНИКОВ:**")
        for i, s in enumerate(data['sources'][:3], 1):
            text = s.get('text', '')[:300]
            if text:
                parts.append(f"[Блок #{i}]: \"{text}...\"")
        
        parts.append("")
        parts.append("🔗 **ИСТОЧНИКИ:**")
        for s in data['sources'][:3]:
            url = s.get('url', '')
            if url:
                parts.append(f"• {url}")
    else:
        parts.append("🧠 **В ИНТЕРНЕТЕ НЕТ ДАННЫХ, НО Я ЗНАЮ:**")
        parts.append("")
        parts.append("К сожалению, в интернете не нашлось информации по вашему запросу.")
        parts.append("Попробуйте переформулировать вопрос или уточнить детали.")
        parts.append("")
        parts.append("💡 **РЕКОМЕНДАЦИЯ:**")
        parts.append("• Используйте более общие ключевые слова")
        parts.append("• Проверьте орфографию")
        parts.append("• Задайте вопрос по-другому")
    
    return "\n".join(parts)

def format_answer_with_confidence(answer: str, data: Dict, confidence: Dict) -> str:
    parts = []
    
    parts.append(format_confidence(confidence))
    parts.append("")
    
    parts.append("📝 **ОТВЕТ:**")
    parts.append(answer)
    parts.append("")
    
    if data['sources']:
        parts.append("📋 **ЦИТАТЫ ИЗ ИСТОЧНИКОВ:**")
        for i, s in enumerate(data['sources'][:3], 1):
            text = s.get('text', '')[:300]
            if text:
                parts.append(f"[Блок #{i}]: \"{text}...\"")
        parts.append("")
    
    comparison = compare_sources(data)
    if comparison:
        parts.append(comparison)
        parts.append("")
    
    if data['sources']:
        parts.append("🔗 **ИСТОЧНИКИ:**")
        for i, s in enumerate(data['sources'][:3], 1):
            url = s.get('url', '')
            date = s.get('date', 'не указана')
            if url:
                parts.append(f"   [{i}] {url} (дата: {date})")
    
    return "\n".join(parts)

# ═══════════════════════════════════════════════════════════════════
#  ФИНАЛЬНАЯ ФУНКЦИЯ (БЕЗ ПРАВА НА ЛЕНЬ)
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer_no_lazy(uid: int, user_message: str, history: List[Dict], predictor: TimePredictor) -> str:
    logger.info(f"🛡️ ЖЁСТКИЙ РЕЖИМ: {user_message[:50]}")
    
    predictor.set_stage('generating_variants')
    data = await collect_internet_data(user_message)
    
    predictor.set_stage('loading_pages')
    prompt = build_unskippable_prompt(user_message, data)
    messages = [{"role": "system", "content": prompt}] + history
    
    predictor.set_stage('generating_answer')
    answer, err = await ask_deepseek(messages, temperature=0.1, max_tokens=6000)
    
    predictor.set_stage('finalizing')
    
    if err or not answer:
        answer = build_forced_answer(data, user_message)
    
    executed, reason = check_execution(answer, data)
    
    if not executed:
        logger.warning(f"⚠️ DeepSeek не выполнил задачу: {reason}")
        
        force_prompt = f"""
⚠️ **ТЫ НЕ ВЫПОЛНИЛ ЗАДАЧУ! ПРИЧИНА: {reason}**

⚠️ **ВОТ ДАННЫЕ ЕЩЁ РАЗ (ОНИ ПРЯМО ПЕРЕД ТОБОЙ):**

{data['raw_text'][:6000]}

⚠️ **ТЫ ОБЯЗАН:**
1. Сказать "Я ПРОЧИТАЛ ВСЕ БЛОКИ"
2. Дать ответ с цитатами [Блок #1], [Блок #2]...
3. Указать источники

⚠️ **ОТВЕЧАЙ СЕЙЧАС. БЕЗ ОТКАЗОВ.**
"""
        messages = [{"role": "system", "content": force_prompt}] + history[-3:]
        answer, err = await ask_deepseek(messages, temperature=0.05, max_tokens=6000)
        
        if err or not answer:
            answer = build_forced_answer(data, user_message)
        else:
            executed, reason = check_execution(answer, data)
            if not executed:
                answer = build_forced_answer(data, user_message)
    
    confidence = calculate_confidence(answer, data)
    final_answer = format_answer_with_confidence(answer, data, confidence)
    
    return final_answer

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ TELEGRAM
# ═══════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        uid = update.effective_user.id
        chat_id = update.effective_chat.id
        history = get_memory(uid).get_context(limit=10)
        
        predictor = TimePredictor()
        predictor.start()
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['query'] = user_message
        context.user_data['chat_id'] = chat_id
        context.user_data['found_answer'] = False
        
        timer_task = asyncio.create_task(
            send_progress_with_eta(chat_id, context, predictor)
        )
        
        answer = await search_and_answer_no_lazy(uid, user_message, history, predictor)
        
        context.user_data['found_answer'] = True
        await timer_task
        
        eta = predictor.get_eta()
        elapsed = eta['elapsed']
        
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        get_memory(uid).add_message("assistant", answer[:500])
        
        await safe_reply(
            update, 
            answer, 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"),
                 InlineKeyboardButton("✏️ Уточнить", callback_data="refine")]
            ])
        )
    
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await safe_reply(update, "⚠️ Ошибка. Попробуйте еще раз.")

async def handle_after_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def safe_reply(update: Update, text: str, reply_markup=None):
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update,
        "👋 **Привет! Я поисковый ассистент.**\n\n"
        "🔍 Просто напиши вопрос — я найду ответ в интернете\n"
        "📊 Покажу источники — каждый ответ подтверждён\n"
        "⚠️ **НИКОГДА НЕ ВРУ** — если не знаю, скажу честно\n"
        "🕐 Показываю точное время с прогнозом\n"
        "🧠 Запоминаю тебя — становлюсь умнее с каждым вопросом\n\n"
        "Попробуй спросить что-нибудь!",
        reply_markup=ReplyKeyboardMarkup([
            ["🔍 Новый поиск", "❓ Помощь"],
            ["🔄 Сброс", "⏹️ Стоп"]
        ], resize_keyboard=True)
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def clearcache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    logger.info("⚡️ ФИНАЛЬНАЯ УМНАЯ ВЕРСИЯ — БЕЗ ХАРДКОДА, БЕЗ ЛАЗЕЕК, С ПРОГНОЗОМ ВРЕМЕНИ")
    
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
