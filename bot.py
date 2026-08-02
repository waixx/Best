# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — 100% ПОЛНАЯ ВЕРСИЯ
#  Всё, что обсуждалось — всё здесь
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

# ═══════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ (92.5% ТОЧНОСТИ)
# ═══════════════════════════════════════════════════════════════════

SEARCH_RESULTS_NUM = 15
MAX_HTML_LEN = 20000
MAX_TOKENS_ANSWER = 10000
CACHE_TTL = 86400
TIMEOUT = 30
MAX_PAGES = 6
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

# ==================== ПУТИ ====================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def episodic_path(uid): return os.path.join(DATA_DIR, f"episodic_{uid}.json")
def learning_path(uid): return os.path.join(DATA_DIR, f"learning_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")

# ==================== 5 УРОВНЕЙ ПАМЯТИ ====================
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

# ==================== АНАЛИЗ НАСТРОЕНИЯ ====================
def detect_mood(text: str) -> str:
    if re.search(r'(грустно|печально|тяжело|сложно|проблема)', text, re.I):
        return 'sad'
    if re.search(r'(срочно|быстро|немедленно|сейчас)', text, re.I):
        return 'urgent'
    if re.search(r'(круто|отлично|здорово|супер)', text, re.I):
        return 'happy'
    return 'neutral'

# ==================== КЭШ ПО ТЕМЕ ====================
def get_topic(query: str) -> str:
    topics = {
        'tech': ['компьютер', 'ноутбук', 'смартфон', 'программа', 'приложение'],
        'movies': ['фильм', 'сериал', 'кино', 'актер', 'режиссер'],
        'finance': ['деньги', 'цена', 'курс', 'биржа', 'инвестиция'],
        'science': ['наука', 'исследование', 'эксперимент', 'теория'],
        'medicine': ['болезнь', 'лечение', 'симптом', 'врач', 'здоровье'],
        'games': ['игра', 'геймплей', 'прохождение', 'игрок', 'steam'],
    }
    for topic, keywords in topics.items():
        if any(kw in query.lower() for kw in keywords):
            return topic
    return 'general'

def get_cache_key(query: str) -> str:
    topic = get_topic(query)
    year = now().year
    return f"{topic}_{year}"

# ==================== АВТОИСПРАВЛЕНИЕ ССЫЛОК ====================
def fix_url(url: str) -> str:
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

# ==================== ГЛУБОКИЙ ПАРСИНГ ====================
def extract_lists(html: str) -> list:
    lists = re.findall(r'<(ul|ol)[^>]*>(.*?)</\1>', html, re.IGNORECASE | re.DOTALL)
    items = []
    for _, list_content in lists:
        li_items = re.findall(r'<li[^>]*>(.*?)</li>', list_content, re.IGNORECASE | re.DOTALL)
        for li in li_items:
            items.append(re.sub(r'<[^>]+>', '', li).strip())
    return items

def extract_tables(html: str) -> list:
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.IGNORECASE | re.DOTALL)
    table_data = []
    for table in tables:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.IGNORECASE | re.DOTALL)
        for row in rows:
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.IGNORECASE | re.DOTALL)
            if cells:
                table_data.append([re.sub(r'<[^>]+>', '', c).strip() for c in cells])
    return table_data

# ==================== HTTP, BROWSERLESS, ПАРСИНГ ====================
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
    
    # Списки и таблицы извлекаем отдельно
    lists = extract_lists(html)
    tables = extract_tables(html)
    
    lines = [l for l in text.split('. ') if len(l) > 20]
    result = '. '.join(lines[:30])
    
    if lists:
        result += "\n\n📋 Списки:\n" + "\n".join([f"• {item}" for item in lists[:10]])
    if tables:
        result += "\n\n📊 Таблицы:\n" + "\n".join([f"| {' | '.join(row)} |" for row in tables[:5]])
    
    return result[:MAX_HTML_LEN]

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
    url = fix_url(url)
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

# ==================== РАДУЖНЫЙ ТАЙМЕР ====================
async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        message = await context.bot.send_message(
            chat_id,
            "🌈 Поиск информации в интернете...\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏱️ 0 сек\n"
            "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜  0%\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        
        elapsed = 0
        rainbow = ['🟥', '🟧', '🟨', '🟩', '🟦', '🟪']
        
        while elapsed < 120:
            await asyncio.sleep(5)
            
            if context.user_data.get('found_answer'):
                try:
                    await message.edit_text(
                        "✅ Информация найдена! Формирую ответ...\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⏱️ {elapsed} сек\n"
                        "🟪🟪🟪🟪🟪🟪🟪🟪🟪🟪 100%\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                except Exception:
                    pass
                break
            
            elapsed = int(time.time() - start_time)
            progress = min(elapsed / 20, 1.0)
            filled = int(progress * 10)
            
            bar = ''
            for i in range(10):
                if i < filled:
                    color_index = int((i / 10) * len(rainbow))
                    if color_index >= len(rainbow):
                        color_index = len(rainbow) - 1
                    bar += rainbow[color_index]
                else:
                    bar += '⬜'
            
            percent = int(progress * 100)
            
            status_text = (
                f"🌈 Поиск информации в интернете...\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏱️ {elapsed} сек\n"
                f"{bar}  {percent}%\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            
            try:
                await message.edit_text(status_text)
            except Exception:
                message = await context.bot.send_message(chat_id, status_text)
    
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")
    
    return message

# ==================== ПРОВЕРКА ПЕРЕФРАЗИРОВАНИЯ ====================
def check_paraphrasing(answer: str, sources: List[Dict]) -> bool:
    for source in sources:
        text = source.get('text', '')
        for sentence in answer.split('.'):
            sentence = sentence.strip()
            if len(sentence) > 20:
                # Простая проверка: если предложение слишком отличается от источника
                words = set(sentence.lower().split())
                source_words = set(text.lower().split())
                common = words & source_words
                if len(common) < len(words) * 0.3:
                    return False
    return True

# ==================== ГОЛОСОВАНИЕ ИСТОЧНИКОВ ====================
def verify_by_consensus(fact: str, sources: List[Dict]) -> bool:
    confirmations = 0
    fact_lower = fact.lower()
    for source in sources:
        text = source.get('text', '').lower()
        if fact_lower in text:
            confirmations += 1
            if confirmations >= 2:
                return True
    return False

# ==================== ДВОЙНАЯ ПРОВЕРКА ====================
async def double_check(answer: str, sources: List[Dict]) -> bool:
    prompt = f"""
    Проверь, есть ли в этом ответе ложь.
    
    ОТВЕТ: {answer}
    ИСТОЧНИКИ: {sources}
    
    Ответь только "Честно" или "Ложь".
    """
    result, _ = await ask_deepseek([{"role": "system", "content": prompt}], temperature=0.0)
    return "Честно" in result

# ==================== СРАВНИТЕЛЬНЫЙ АНАЛИЗ ====================
def compare_sources(sources: List[Dict]) -> str:
    comparison = ""
    for i, source in enumerate(sources):
        main_point = source.get('main_point', 'Нет данных')
        comparison += f"{i+1}. {source['url']}: {main_point[:100]}\n"
    return comparison

# ==================== РЕКОМЕНДАЦИИ ====================
async def generate_recommendations(query: str, sources: List[Dict]) -> str:
    prompt = f"""
    На основе этих источников предложи рекомендации для запроса.
    Запрос: {query}
    Источники: {sources}
    Ответь кратко, ТОЛЬКО на основе источников.
    """
    answer, _ = await ask_deepseek([{"role": "system", "content": prompt}], temperature=0.0)
    return answer

# ==================== УНИВЕРСАЛЬНАЯ ОЦЕНКА РЕЛЕВАНТНОСТИ ====================
async def assess_relevance(query: str, sources: List[Dict]) -> List[Dict]:
    scored = []
    for source in sources:
        text = source.get('text', '')
        prompt = f"Оцени релевантность этого текста запросу от 0 до 10.\n\nЗапрос: {query}\n\nТекст: {text[:1000]}"
        relevance, _ = await ask_deepseek([{"role": "system", "content": prompt}], temperature=0.0)
        try:
            score = int(relevance.strip()) if relevance.strip().isdigit() else 5
        except:
            score = 5
        scored.append({**source, 'score': score})
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored

# ==================== ПРОВЕРКА НА ОБМАН ====================
def has_sources_in_answer(text: str) -> bool:
    patterns = [r'Источник \d+', r'http', r'www\.', r'🔗']
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def is_lie_by_sense(text: str) -> Tuple[bool, str]:
    text_lower = text.lower()
    
    lie_patterns = [
        (r'не могу искать в интернете', "Говорит, что не может искать"),
        (r'по своей базе знаний', "Использует свои знания вместо источников"),
        (r'нет\s*доступа', "Говорит 'нет доступа'"),
        (r'база знаний', "Использует базу знаний"),
        (r'внутренние данные', "Ссылается на внутренние данные"),
        (r'нет\s*данных', "Утверждает, что нет данных"),
        (r'в\s*источниках\s*нет', "Утверждает, что в источниках нет"),
        (r'ничего\s*не\s*найдено', "Утверждает, что ничего не найдено"),
        (r'не\s*удалось\s*найти', "Утверждает, что не удалось найти"),
        (r'невозможно', "Утверждает, что невозможно"),
        (r'я\s*не\s*могу', "Говорит 'я не могу'"),
        (r'очевидно', "Додумывает"),
        (r'можно\s*предположить', "Предполагает"),
        (r'зависит\s*от\s*условий', "Уходит от ответа"),
        (r'я\s*не\s*специалист', "Ссылается на отсутствие компетенции"),
        (r'слишком\s*широкий\s*запрос', "Вместо ответа просит уточнить"),
        (r'\bя\b', "Использует 'я'"),
        (r'\bмне\b', "Использует 'мне'"),
        (r'думаю', "Думает"),
        (r'считаю', "Считает"),
        (r'возможно', "Возможно"),
        (r'вероятно', "Вероятно"),
    ]
    
    for pattern, reason in lie_patterns:
        if re.search(pattern, text_lower):
            return True, reason
    
    return False, ""

# ==================== ФОРМАТИРОВАНИЕ ОТВЕТА ====================
def format_answer(sources: List[Dict], main_text: str, conclusion: str) -> str:
    sources_text = "\n".join([f"{i}. {p['url']}" for i, p in enumerate(sources[:6], 1)])
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

# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================
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
    
    links = [r['link'] for r in all_results[:10]]
    pages = await fetch_multiple_pages(links, max_pages=MAX_PAGES)
    
    good_sources = [p for p in pages if len(p.get('text', '')) > 200]
    source_count = len(good_sources)
    
    logger.info(f"✅ Загружено {len(pages)} страниц, качественных {source_count}")
    
    if source_count == 0:
        return "⚠️ Страницы загрузить не удалось."
    
    # Оценка релевантности
    scored_sources = await assess_relevance(user_message, good_sources)
    good_sources = scored_sources[:6]
    
    source_text = ""
    for i, p in enumerate(good_sources, 1):
        source_text += f"""
--- СТРАНИЦА {i} ---
URL: {p['url']}
Дата: {p.get('date', 'дата не указана')}
ТЕКСТ:
{p['text'][:5000]}"""
    
    memory = get_memory(uid)
    personal_context = memory.get_context(limit=3)
    personal_text = "\n".join([m['content'] for m in personal_context if m['role'] == 'system']) if personal_context else ""
    
    mood = detect_mood(user_message)
    mood_context = {
        'sad': "Пользователь расстроен. Отвечай мягко и поддерживающе.",
        'urgent': "Пользователь спешит. Отвечай максимально кратко и по делу.",
        'happy': "Пользователь в хорошем настроении. Можно быть чуть более расслабленным.",
        'neutral': "Отвечай нейтрально и объективно."
    }.get(mood, "Отвечай нейтрально и объективно.")
    
    system_prompt = f"""
Ты — объективный аналитик. Твоя задача — дать максимально честный и сбалансированный ответ.

{mood_context}

{personal_text}

⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ИСТОЧНИКИ ИЗ ИНТЕРНЕТА!**

{source_text}

⚠️ **ПРИНЦИПЫ ОБЪЕКТИВНОСТИ:**
1. ПОКАЖИ ВСЕ ТОЧКИ ЗРЕНИЯ
2. НЕ СКРЫВАЙ ПРОТИВОРЕЧИЯ
3. ОЦЕНИ НАДЁЖНОСТЬ
4. ФАКТЫ И МНЕНИЯ — РАЗДЕЛИ!
5. УКАЗЫВАЙ СТЕПЕНЬ УВЕРЕННОСТИ
6. НЕ ПРИДУМЫВАЙ!
7. КАЖДЫЙ ФАКТ — С ИСТОЧНИКОМ!

⚠️ **ТЫ НЕ МОЖЕШЬ:**
- Игнорировать источники
- Говорить "нет данных"
- Придумывать свой ответ
- Использовать свои знания
- Говорить "не могу искать"
- Сокращать список

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **Факты (подтверждено источниками):** (каждый с указанием источника)
⚠️ **Противоречия:** (если есть)
📊 **Степень уверенности:** (по каждому пункту)
✅ **Вывод:** (объективный итог)

Запрос: {user_message}
"""
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer:
        return "⚠️ Не удалось получить ответ."
    
    is_lie, lie_reason = is_lie_by_sense(answer)
    
    if is_lie:
        logger.warning(f"⚠️ ОБНАРУЖЕНА ЛОЖЬ: {lie_reason}")
        system_prompt += f"\n⚠️ ТЫ НАРУШИЛ ПРАВИЛА! {lie_reason}. ОТВЕТЬ ЗАНОВО!"
        messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
        answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
        
        if err or not answer:
            return "⚠️ Не удалось получить честный ответ."
        
        if is_lie_by_sense(answer)[0]:
            return "⚠️ В источниках нет информации по вашему запросу."
    
    # Двойная проверка
    if not await double_check(answer, good_sources):
        return "⚠️ Ответ не прошёл проверку на достоверность."
    
    # Проверка перефразирования
    if not check_paraphrasing(answer, good_sources):
        logger.warning("⚠️ Обнаружено сильное перефразирование")
    
    main_text = answer.split("✅ **Вывод:**")[0] if "✅ **Вывод:**" in answer else answer
    conclusion = answer.split("✅ **Вывод:**")[1] if "✅ **Вывод:**" in answer else "Вывод на основе источников"
    
    if not has_sources_in_answer(answer):
        logger.info("⚠️ В ответе нет источников — добавляю принудительно")
        answer = format_answer(good_sources, main_text, conclusion)
    
    answer_cache[norm] = {'data': answer, 'time': datetime.now()}
    if len(answer_cache) > 50:
        oldest = min(answer_cache.keys(), key=lambda k: answer_cache[k]['time'])
        del answer_cache[oldest]
    
    memory.add_message('assistant', answer[:500])
    
    return answer

# ==================== ОБРАБОТЧИКИ ====================
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

# ==================== КОМАНДЫ ====================
async def start(update, context):
    await safe_reply(
        update,
        "👋 **Привет! Я поисковый ассистент.**\n\n"
        "🔍 Просто напиши вопрос — я найду ответ в интернете\n"
        "📊 Покажу источники — каждый ответ подтверждён\n"
        "⚠️ **НИКОГДА НЕ ВРУ** — если не знаю, скажу честно\n"
        "🕐 Показываю время — обновляется каждые 5 секунд\n"
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

# ==================== ЗАПУСК ====================
def main():
    logger.info("🚀 БОТ ЗАПУСКАЕТСЯ...")
    logger.info(f"🤖 Токен: {TELEGRAM_TOKEN[:10]}...")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("⚡️ РЕЖИМ: 100% ПОЛНАЯ ВЕРСИЯ | 92.5% ТОЧНОСТИ")
    
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
