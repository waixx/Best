# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — УНИВЕРСАЛЬНАЯ ВЕРСИЯ
#  БЕЗ ХАРДКОДА (DeepSeek анализирует)
#  С ПОСТОЯННЫМИ КНОПКАМИ И РАБОЧИМ ТАЙМЕРОМ
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

MODEL_DEFAULT = os.getenv("MODEL_DEFAULT", "deepseek-v4")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

SEARCH_RESULTS_NUM = 15
MAX_HTML_LEN = 15000
MAX_TOKENS_ANSWER = 4000
CACHE_TTL = 86400
TIMEOUT = 20
MAX_PAGES = 5
SEMAPHORE = 5

DEEPSEEK_TEMPERATURE = 0.15
DEEPSEEK_TOP_P = 0.88
DEEPSEEK_FREQUENCY_PENALTY = 0.6

SKIP_DOMAINS = ['youtube.com', 'instagram.com', 'facebook.com', 'tiktok.com', 'twitter.com']

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

# Постоянные кнопки
MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["🔍 Новый поиск", "⏹️ Стоп"],
    ["❓ Помощь", "📊 Статистика"]
], resize_keyboard=True)

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info(f"🔑 APISERPENT: {'✅' if APISERPENT_API_KEY else '❌'}")
logger.info(f"🔑 SERPER: {'✅' if SERPER_API_KEY else '❌'}")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
logger.info(f"🤖 Модель: {MODEL_DEFAULT}")
logger.info("⚡️ УНИВЕРСАЛЬНАЯ ВЕРСИЯ")

def now():
    return datetime.now(TZ)

# ═══════════════════════════════════════════════════════════════════
#  КРАСИВЫЙ РАДУЖНЫЙ ТАЙМЕР (С РЕАЛЬНЫМ ПРОГНОЗОМ)
# ═══════════════════════════════════════════════════════════════════

class RainbowTimer:
    RAINBOW_COLORS = ['🟥', '🟧', '🟨', '🟩', '🟦', '🟪']
    
    STAGE_ICONS = {
        'start': '🚀', 'analyzing': '🧠', 'searching': '🔍',
        'loading': '📄', 'parsing': '🧩', 'thinking': '🤔',
        'generating': '✍️', 'validating': '✅', 'finalizing': '🎯', 'done': '🏁'
    }
    
    STAGE_NAMES = {
        'start': 'Запуск', 'analyzing': 'Анализирую запрос',
        'searching': 'Ищу в интернете', 'loading': 'Загружаю страницы',
        'parsing': 'Извлекаю данные', 'thinking': 'Думаю над ответом',
        'generating': 'Формирую ответ', 'validating': 'Проверяю точность',
        'finalizing': 'Завершаю', 'done': 'Готово!'
    }
    
    # Реалистичное время для каждого этапа (в секундах)
    STAGE_DURATIONS = {
        'analyzing': (2, 5),
        'searching': (5, 15),
        'loading': (8, 20),
        'parsing': (3, 8),
        'thinking': (2, 5),
        'generating': (5, 15),
        'validating': (2, 5),
        'finalizing': (1, 3)
    }
    
    def __init__(self):
        self.start_time = None
        self.current_stage = 'start'
        self.stage_start = None
        self.total_estimated = 60
        self.progress = 0
        self.running = False
        self.message = None
        self.rainbow_position = 0
        self.stage_history = []
        self.actual_times = {}
    
    def start(self):
        self.start_time = time.time()
        self.current_stage = 'start'
        self.stage_start = time.time()
        self.progress = 0
        self.running = True
        self.rainbow_position = 0
        self.stage_history = []
        self.total_estimated = self._calculate_total_estimate()
        return self
    
    def set_stage(self, stage: str):
        if self.stage_start and self.current_stage != stage:
            # Сохраняем время предыдущего этапа
            elapsed = time.time() - self.stage_start
            self.stage_history.append((self.current_stage, elapsed))
        self.current_stage = stage
        self.stage_start = time.time()
    
    def get_elapsed(self) -> int:
        if not self.start_time:
            return 0
        return int(time.time() - self.start_time)
    
    def get_remaining(self) -> int:
        elapsed = self.get_elapsed()
        return max(0, self.total_estimated - elapsed)
    
    def get_progress(self) -> int:
        elapsed = self.get_elapsed()
        if elapsed == 0:
            return 0
        return min(100, int((elapsed / max(self.total_estimated, 1)) * 100))
    
    def get_stage_icon(self) -> str:
        return self.STAGE_ICONS.get(self.current_stage, '⚙️')
    
    def get_stage_name(self) -> str:
        return self.STAGE_NAMES.get(self.current_stage, 'Обработка')
    
    def get_rainbow_bar(self, length: int = 30) -> str:
        progress = self.get_progress()
        filled = int(length * progress / 100)
        self.rainbow_position = (self.rainbow_position + 1) % len(self.RAINBOW_COLORS)
        bar = []
        for i in range(length):
            if i < filled:
                color_idx = (i + self.rainbow_position) % len(self.RAINBOW_COLORS)
                bar.append(self.RAINBOW_COLORS[color_idx])
            else:
                bar.append('⬜')
        return ''.join(bar)
    
    def _calculate_total_estimate(self) -> int:
        """Рассчитывает реалистичный прогноз на основе этапов"""
        total = 5  # Базовое время
        for stage, (min_time, max_time) in self.STAGE_DURATIONS.items():
            # Берём среднее
            total += (min_time + max_time) // 2
        return total
    
    def get_status_line(self) -> str:
        icon = self.get_stage_icon()
        name = self.get_stage_name()
        progress = self.get_progress()
        elapsed = self.get_elapsed()
        remaining = self.get_remaining()
        
        time_str = f"⏱️ **{elapsed}** сек · ~**{remaining}** сек" if remaining > 0 else f"⏱️ **{elapsed}** сек"
        return f"""
{icon} **{name}**

{self.get_rainbow_bar(30)}

📊 **{progress}%**
{time_str}

⏳ Всего: **{self.total_estimated}** сек
"""
    
    def finish(self):
        self.current_stage = 'done'
        self.progress = 100
        self.running = False

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ
# ═══════════════════════════════════════════════════════════════════

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def episodic_path(uid): return os.path.join(DATA_DIR, f"episodic_{uid}.json")
def learning_path(uid): return os.path.join(DATA_DIR, f"learning_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")

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
        important_keywords = ['это', 'является', 'состоит', 'находится', 'важно', 'главное', 'ключевой', 'основной']
        for msg in messages:
            content = msg.get('content', '')
            if len(content) < 20:
                continue
            if any(kw in content.lower() for kw in important_keywords):
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
    lists = []
    list_matches = re.findall(r'(?:^|\n)\s*([•\-*\d+.]\s*[^\n]{10,})', html, re.MULTILINE)
    if list_matches:
        lists = [f"  • {l.strip()}" for l in list_matches[:10]]
    html = re.sub(r'<ul[^>]*>', '\n', html, re.I)
    html = re.sub(r'<ol[^>]*>', '\n', html, re.I)
    html = re.sub(r'</li>', '\n', html, re.I)
    html = re.sub(r'<li[^>]*>', '  • ', html, re.I)
    html = re.sub(r'<h[1-6][^>]*>', '\n', html, re.I)
    html = re.sub(r'<p[^>]*>', '\n', html, re.I)
    html = re.sub(r'</p>', '\n', html, re.I)
    html = re.sub(r'<br[^>]*>', '\n', html, re.I)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = re.sub(r'\s+', ' ', html)
    html = re.sub(r'\{[^}]*\}', '', html)
    html = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', '', html)
    sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', html)
    result = ' '.join(sentences[:25])
    if lists:
        result = "📋 СПИСКИ:\n" + '\n'.join(lists) + "\n\n" + result
    return result[:MAX_HTML_LEN]

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
#  ПОИСК
# ═══════════════════════════════════════════════════════════════════

async def search_apiserpent(query: str) -> List[Dict]:
    if not APISERPENT_API_KEY:
        return []
    session = await get_http_session()
    try:
        params = {"q": query, "engine": "google", "num": SEARCH_RESULTS_NUM}
        async with session.get("https://apiserpent.com/api/search", params=params, headers={"X-API-Key": APISERPENT_API_KEY}, timeout=15) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            organic = data.get("results", {}).get("organic", []) or data.get("organic_results", [])
            for x in organic[:SEARCH_RESULTS_NUM]:
                if isinstance(x, dict):
                    results.append({"title": str(x.get("title", ""))[:120], "snippet": str(x.get("snippet", ""))[:300], "link": str(x.get("url", x.get("link", "#")))[:120]})
            return results
    except Exception as e:
        logger.warning(f"⚠️ APISerpent: {e}")
        return []

async def search_serper(query: str) -> List[Dict]:
    if not SERPER_API_KEY:
        return []
    session = await get_http_session()
    try:
        async with session.post("https://google.serper.dev/search", json={"q": query, "num": SEARCH_RESULTS_NUM}, headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}, timeout=10) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            for item in data.get("organic", [])[:SEARCH_RESULTS_NUM]:
                results.append({"title": item.get("title", "")[:120], "snippet": item.get("snippet", "")[:300], "link": item.get("link", "#")[:120]})
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

async def ask_deepseek(messages, temperature=DEEPSEEK_TEMPERATURE, max_tokens=MAX_TOKENS_ANSWER, attempt=0):
    if attempt >= 5:
        return None, "max_retries"
    session = await get_http_session()
    try:
        payload = {"model": MODEL_DEFAULT, "messages": messages, "temperature": temperature, "top_p": DEEPSEEK_TOP_P, "frequency_penalty": DEEPSEEK_FREQUENCY_PENALTY, "max_tokens": max_tokens}
        async with session.post(f"{DEEPSEEK_API_BASE}/chat/completions", headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, json=payload, timeout=45) as resp:
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
#  УНИВЕРСАЛЬНЫЙ АНАЛИЗАТОР (DeepSeek решает)
# ═══════════════════════════════════════════════════════════════════

async def analyze_with_deepseek(message: str, history: List[Dict]) -> Dict:
    history_text = ""
    for msg in history[-5:]:
        history_text += f"{msg.get('role', '')}: {msg.get('content', '')[:200]}\n"
    
    prompt = f"""
⚠️ **ТЫ — УНИВЕРСАЛЬНЫЙ АНАЛИТИК. ОПРЕДЕЛИ, ЧТО ДЕЛАТЬ.**

⚠️ **КОНТЕКСТ ДИАЛОГА:**
{history_text if history_text else "Нет предыдущих сообщений"}

⚠️ **СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:**
{message}

⚠️ **ОПРЕДЕЛИ:**
1. **Тип:** greeting / question / instruction / clarification / stop
2. **Действие:** respond (ответить сразу) / search (искать в интернете) / refine (уточнить)
3. **Сложность:** simple / complex

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{
  "type": "question",
  "action": "search",
  "complexity": "complex",
  "confidence": 85,
  "reason": "причина",
  "response": null
}}

⚠️ **ЕСЛИ ПРИВЕТСТВИЕ:**
{{
  "type": "greeting",
  "action": "respond",
  "complexity": "simple",
  "confidence": 95,
  "reason": "пользователь здоровается",
  "response": "👋 **Привет!** Я на связи. Задавай вопрос."
}}

⚠️ **ОТВЕЧАЙ ТОЛЬКО JSON.**
"""
    messages = [{"role": "system", "content": prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.15, max_tokens=500)
    
    if err or not answer:
        return {"type": "question", "action": "search", "complexity": "complex", "confidence": 50, "reason": "анализ не удался", "response": None}
    
    try:
        json_match = re.search(r'\{.*\}', answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    
    return {"type": "question", "action": "search", "complexity": "complex", "confidence": 50, "reason": "ошибка парсинга", "response": None}

# ═══════════════════════════════════════════════════════════════════
#  УМНЫЙ ПОИСК
# ═══════════════════════════════════════════════════════════════════

def generate_variants(query: str, keywords: List[str]) -> List[str]:
    variants = [query]
    if not keywords:
        return variants
    if len(keywords) >= 2:
        variants.append(" ".join(keywords[:2]))
    if len(keywords) >= 3:
        variants.append(" ".join(keywords[:3]))
    starters = ['как', 'что такое', 'для чего', 'зачем']
    for starter in starters:
        if starter not in query.lower():
            variants.append(f"{starter} {keywords[0] if keywords else ''}")
    clean = ' '.join(keywords[:3])
    if clean != query:
        variants.append(clean)
    seen = set()
    unique = []
    for v in variants:
        if v and len(v) > 3 and v not in seen:
            seen.add(v)
            unique.append(v)
    return unique[:8]

def is_valid_result(result: Dict) -> bool:
    title = result.get('title', '')
    snippet = result.get('snippet', '')
    url = result.get('link', '')
    if len(title) < 5 or len(snippet) < 20:
        return False
    if any(domain in url for domain in SKIP_DOMAINS):
        return False
    text = title + ' ' + snippet
    if not re.search(r'[А-Яа-яA-Za-z][^.!?]{10,50}[.!?]', text):
        return False
    return True

def score_relevance(result: Dict, query: str) -> float:
    title = result.get('title', '')
    snippet = result.get('snippet', '')
    text = (title + ' ' + snippet).lower()
    query_words = set(w for w in query.lower().split() if len(w) > 2)
    if not query_words:
        return 0.1
    text_words = set(re.findall(r'\b\w+\b', text))
    overlap = len(query_words & text_words)
    return overlap / len(query_words) if query_words else 0.0

async def search_until_good(query: str, min_good: int = 5) -> List[Dict]:
    logger.info(f"🔍 Нужно найти {min_good} релевантных источников")
    keywords = re.findall(r'\b\w{4,}\b', query.lower())
    variants = generate_variants(query, keywords)
    good_results = []
    seen_urls = set()
    for variant in variants:
        results = await search_primary(variant)
        if not results:
            continue
        for r in results:
            url = r.get('link', '')
            if url in seen_urls:
                continue
            if not is_valid_result(r):
                continue
            score = score_relevance(r, query)
            if score > 0.1:
                seen_urls.add(url)
                r['relevance'] = score
                good_results.append(r)
                logger.info(f"   ✅ Найден релевантный источник (score={score:.2f}): {url[:50]}...")
        if len(good_results) >= min_good:
            break
        await asyncio.sleep(0.5)
    good_results.sort(key=lambda x: -x.get('relevance', 0))
    logger.info(f"✅ Найдено {len(good_results)} релевантных источников")
    return good_results[:min_good]

async def fetch_pages_fast(results: List[Dict]) -> List[Dict]:
    if not results:
        return []
    top_results = results[:MAX_PAGES]
    semaphore = asyncio.Semaphore(SEMAPHORE)
    async def fetch_one(r):
        async with semaphore:
            url = r.get('link', '')
            if not url or not url.startswith('http'):
                return None
            text, date = await fetch_content(url, timeout=TIMEOUT)
            if text and len(text) > 200:
                return {'url': url, 'title': r.get('title', ''), 'text': text[:MAX_HTML_LEN], 'date': date, 'relevance': r.get('relevance', 0)}
            return None
    tasks = [fetch_one(r) for r in top_results]
    fetched = await asyncio.gather(*tasks)
    pages = [p for p in fetched if p is not None]
    logger.info(f"✅ Загружено {len(pages)} страниц")
    return pages

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(answer: str, data: Dict) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'factors': []}
    if data.get('sources'):
        reliable = 0
        for s in data['sources']:
            url = s.get('url', '')
            if any(d in url for d in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
                reliable += 1
            elif any(d in url for d in ['.com', '.org', '.net', '.ru']):
                reliable += 0.5
        score = min(100, (reliable / len(data['sources'])) * 100)
        confidence['source_reliability'] = score
        confidence['factors'].append(f"Надёжность: {score:.0f}%")
    else:
        confidence['source_reliability'] = 20
        confidence['factors'].append("Нет источников")
    confidence['data_completeness'] = 50
    confidence['factors'].append("Данные: средние")
    confidence['recency'] = 50
    confidence['factors'].append("Свежесть: средняя")
    confidence['overall'] = int((confidence['source_reliability'] + confidence['data_completeness'] + confidence['recency']) / 3)
    return confidence

def format_confidence(confidence: Dict) -> str:
    overall = confidence['overall']
    icon = "🟢" if overall >= 80 else "🟡" if overall >= 60 else "🟠" if overall >= 40 else "🔴"
    level = "Высокая" if overall >= 80 else "Средняя" if overall >= 60 else "Низкая" if overall >= 40 else "Очень низкая"
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **ТОЧНОСТЬ: {overall}%** {icon} ({level})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **ДЕТАЛИ:**
   • Надёжность: {confidence['source_reliability']:.0f}%
   • Полнота: {confidence['data_completeness']:.0f}%
   • Свежесть: {confidence['recency']:.0f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ═══════════════════════════════════════════════════════════════════
#  ПРОВЕРКА НА ОТКАЗ
# ═══════════════════════════════════════════════════════════════════

def has_meaningful_content(text: str) -> bool:
    if not text:
        return False
    sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,100}[.!?]', text)
    if len(sentences) >= 1:
        return True
    if re.search(r'\d+[.)]', text) or re.search(r'[•\-*]', text):
        return True
    if '?' in text:
        return True
    return False

def check_if_refused(answer: str) -> Tuple[bool, str]:
    if not has_meaningful_content(answer):
        return True, "Нет содержательного ответа"
    refuse_patterns = [('я не уверен', 'не уверен'), ('я не знаю', 'не знаю'), ('не могу ответить', 'не могу'), ('переформулируйте', 'переформулируйте')]
    for pattern, reason in refuse_patterns:
        if re.search(pattern, answer, re.I):
            after = re.split(pattern, answer, flags=re.I)[-1]
            if not has_meaningful_content(after):
                return True, reason
    return False, "Ок"

# ═══════════════════════════════════════════════════════════════════
#  ФОРМИРОВАНИЕ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

def build_prompt_with_rules(user_message: str, pages: List[Dict]) -> str:
    prompt = f"""
⚠️ **ЗАПРОС:** {user_message}

📊 **ИСТОЧНИКИ:**
"""
    for i, s in enumerate(pages[:3], 1):
        prompt += f"\n--- ИСТОЧНИК #{i}: {s.get('url', '')} ---\n{s.get('text', '')[:2000]}\n"
    
    prompt += """
⚠️ **ЖЁСТКИЕ ПРАВИЛА:**
❌ **НЕЛЬЗЯ:** говорить "не могу", "не уверен", "нет данных", "переформулируйте", игнорировать источники
✅ **МОЖНО:** сказать "в интернете нет, но я знаю...", указать уверенность в %

⚠️ **ФОРМАТ:**
🎯 УВЕРЕННОСТЬ: [X]%
📊 ОТВЕТ: [ответ]
📋 ЦИТАТЫ: [Источник #1]: "цитата"
🔗 ИСТОЧНИКИ: [ссылки]
"""
    return prompt

def build_forced_answer(pages: List[Dict], user_message: str) -> str:
    parts = ["✅ **ОТВЕТ НА ОСНОВЕ ДАННЫХ:**", ""]
    if pages:
        all_text = "\n\n".join([p.get('text', '') for p in pages[:3]])
        sentences = re.findall(r'[^.!?]{20,200}[.!?]', all_text)
        if sentences:
            for i, sent in enumerate(sentences[:10], 1):
                parts.append(f"{i}. {sent.strip()}")
        parts.append("")
        parts.append("📋 **ЦИТАТЫ:**")
        for i, s in enumerate(pages[:3], 1):
            text = s.get('text', '')[:300]
            if text:
                parts.append(f"[Источник #{i}]: \"{text}...\"")
        parts.append("")
        parts.append("🔗 **ИСТОЧНИКИ:**")
        for s in pages[:3]:
            url = s.get('url', '')
            if url:
                parts.append(f"• {url}")
    else:
        parts.append("⚠️ В интернете не нашлось информации.")
    return "\n".join(parts)

def format_answer_with_confidence(answer: str, data: Dict, confidence: Dict) -> str:
    parts = [format_confidence(confidence), "", "📝 **ОТВЕТ:**", answer, ""]
    if data.get('sources'):
        parts.append("📋 **ЦИТАТЫ:**")
        for i, s in enumerate(data['sources'][:3], 1):
            text = s.get('text', '')[:300]
            if text:
                parts.append(f"[Источник #{i}]: \"{text}...\"")
        parts.append("")
        parts.append("🔗 **ИСТОЧНИКИ:**")
        for s in data['sources'][:3]:
            url = s.get('url', '')
            if url:
                parts.append(f"• {url}")
    return "\n".join(parts)

# ═══════════════════════════════════════════════════════════════════
#  ТАЙМЕР ДЛЯ TELEGRAM
# ═══════════════════════════════════════════════════════════════════

async def send_rainbow_timer(chat_id, context, timer: RainbowTimer, update_interval: float = 0.5):
    try:
        initial_text = timer.get_status_line()
        message = await context.bot.send_message(chat_id, initial_text, parse_mode='Markdown')
        timer.message = message
        last_text = initial_text
        
        while timer.running:
            await asyncio.sleep(update_interval)
            if context.user_data.get('found_answer'):
                timer.finish()
                try:
                    await message.edit_text("✅ **Готово!** Формирую ответ...", parse_mode='Markdown')
                except Exception:
                    pass
                break
            
            new_text = timer.get_status_line()
            if new_text != last_text:
                try:
                    await message.edit_text(new_text, parse_mode='Markdown')
                except Exception:
                    try:
                        message = await context.bot.send_message(chat_id, new_text, parse_mode='Markdown')
                    except Exception:
                        pass
                last_text = new_text
            
            if timer.is_finished():
                break
        
        if not context.user_data.get('found_answer'):
            try:
                await message.edit_text("⏳ **Завершаю...**", parse_mode='Markdown')
            except Exception:
                pass
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer_final(uid: int, user_message: str, history: List[Dict], timer: RainbowTimer) -> str:
    logger.info(f"🧠 УМНЫЙ РЕЖИМ: {user_message[:50]}")
    
    timer.set_stage('searching')
    results = await search_until_good(user_message, min_good=5)
    if not results:
        timer.finish()
        return "⚠️ В интернете не нашлось релевантной информации."
    
    timer.set_stage('loading')
    pages = await fetch_pages_fast(results)
    if not pages:
        timer.finish()
        return "⚠️ Не удалось загрузить страницы."
    
    timer.set_stage('thinking')
    data = {'sources': pages, 'raw_text': "\n\n".join([p.get('text', '') for p in pages[:3]])}
    
    timer.set_stage('generating')
    prompt = build_prompt_with_rules(user_message, pages)
    messages = [{"role": "system", "content": prompt}] + history
    answer, err = await ask_deepseek(messages, temperature=0.15, max_tokens=4000)
    
    timer.set_stage('validating')
    if err or not answer:
        answer = build_forced_answer(pages, user_message)
    refused, reason = check_if_refused(answer)
    if refused:
        answer = build_forced_answer(pages, user_message)
    
    confidence = calculate_confidence(answer, data)
    final_answer = format_answer_with_confidence(answer, data, confidence)
    timer.finish()
    return final_answer

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ TELEGRAM
# ═══════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Привет! Я поисковый ассистент.**\n\n"
        "🔍 Напиши вопрос — я найду ответ в интернете\n"
        "📊 Покажу источники — каждый ответ подтверждён\n"
        "⚠️ **НИКОГДА НЕ ВРУ**\n"
        "🧠 Запоминаю тебя\n\n"
        "Просто напиши что хочешь найти!",
        reply_markup=MAIN_KEYBOARD
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            return
        
        user_message = update.effective_message.text.strip() if update.effective_message else ""
        if not user_message:
            return
        
        # Кнопки
        if user_message == "⏹️ Стоп":
            context.user_data.clear()
            await update.message.reply_text("⏹️ **Остановлено.**", reply_markup=MAIN_KEYBOARD)
            return
        
        if user_message == "🔍 Новый поиск":
            context.user_data.clear()
            await update.message.reply_text("🔍 **Напиши вопрос.**", reply_markup=MAIN_KEYBOARD)
            return
        
        if user_message == "❓ Помощь":
            await update.message.reply_text(
                "❓ **Помощь**\n\n"
                "• Напиши вопрос — я найду ответ\n"
                "• 🔍 Новый поиск — начать заново\n"
                "• ⏹️ Стоп — остановить всё\n"
                "• 📊 Статистика — память",
                reply_markup=MAIN_KEYBOARD
            )
            return
        
        if user_message == "📊 Статистика":
            memory = get_memory(uid)
            await update.message.reply_text(
                f"📊 **Статистика**\n\n"
                f"💬 Сообщений: {len(memory.short_term)}\n"
                f"👤 Профиль: {len(memory.profile)} полей\n"
                f"⭐ Фактов: {len(memory.episodic)}\n"
                f"📝 Всего: {memory.counter}",
                reply_markup=MAIN_KEYBOARD
            )
            return
        
        # Универсальный анализ через DeepSeek
        history = get_memory(uid).get_context(limit=10)
        analysis = await analyze_with_deepseek(user_message, history)
        
        # Если DeepSeek сказал отвечать сразу
        if analysis.get('action') == 'respond':
            response = analysis.get('response', "👋 Я на связи. Задавай вопрос.")
            await update.message.reply_text(response, reply_markup=MAIN_KEYBOARD)
            return
        
        # Если DeepSeek сказал уточнить
        if analysis.get('action') == 'refine':
            await update.message.reply_text(
                "📝 **Уточни запрос:**\n\nЧто именно ты хочешь найти? Напиши подробнее.",
                reply_markup=MAIN_KEYBOARD
            )
            context.user_data['awaiting_refine'] = True
            return
        
        # Если нужен поиск или неясно — ищем
        chat_id = update.effective_chat.id
        full_history = get_memory(uid).get_context(limit=10)
        
        context.user_data['uid'] = uid
        context.user_data['history'] = full_history
        context.user_data['query'] = user_message
        context.user_data['chat_id'] = chat_id
        context.user_data['found_answer'] = False
        
        timer = RainbowTimer()
        timer.start()
        
        timer_task = asyncio.create_task(send_rainbow_timer(chat_id, context, timer))
        answer = await search_and_answer_final(uid, user_message, full_history, timer)
        
        context.user_data['found_answer'] = True
        await timer_task
        
        elapsed = timer.get_elapsed()
        full_answer = f"⏱️ {elapsed} сек\n\n{answer}"
        get_memory(uid).add_message("assistant", full_answer[:500])
        
        await update.message.reply_text(full_answer, reply_markup=MAIN_KEYBOARD)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте еще раз.", reply_markup=MAIN_KEYBOARD)

# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    memory = get_memory(uid)
    await update.message.reply_text(
        f"📊 **Статистика**\n\n"
        f"💬 Сообщений: {len(memory.short_term)}\n"
        f"👤 Профиль: {len(memory.profile)}\n"
        f"⭐ Фактов: {len(memory.episodic)}\n"
        f"📝 Всего: {memory.counter}",
        reply_markup=MAIN_KEYBOARD
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
    await update.message.reply_text("🧹 **Всё забыто!**", reply_markup=MAIN_KEYBOARD)

async def clearcache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    global html_cache, search_cache, answer_cache
    html_cache = {}
    search_cache = {}
    answer_cache = {}
    await update.message.reply_text("🧹 **Кэш очищен!**", reply_markup=MAIN_KEYBOARD)

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
    logger.info("⚡️ УНИВЕРСАЛЬНАЯ ВЕРСИЯ")
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("forget", forget_command))
        app.add_handler(CommandHandler("clearcache", clearcache_command))
        
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
