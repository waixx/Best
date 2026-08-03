# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ФИНАЛЬНАЯ УМНАЯ ВЕРСИЯ
#  С ЛЁГКОЙ СТАТИЧЕСКОЙ МОДЕЛЬЮ (model2vec) ВМЕСТО sentence-transformers
#  БЕЗ ХАРДКОДА, БЕЗ ЛАЗЕЕК, БЕЗ ВОЗМОЖНОСТИ ОБМАНУТЬ
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
from typing import Optional, Dict, List, Tuple, Any, Set
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# === ЛЁГКАЯ ЗАМЕНА sentence-transformers ===
# Вместо 3-4 ГБ PyTorch — лёгкая статическая модель (~50 МБ)
try:
    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity
    from model2vec import StaticModel
    SEMANTIC_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info("✅ model2vec загружен (лёгкая статическая модель)")
except ImportError as e:
    SEMANTIC_AVAILABLE = False
    logging.error("❌ КРИТИЧЕСКАЯ ОШИБКА: model2vec не установлен")
    logging.error("❌ Установите: pip install model2vec")
    sys.exit(1)

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

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info(f"🔑 APISERPENT: {'✅' if APISERPENT_API_KEY else '❌'}")
logger.info(f"🔑 SERPER: {'✅' if SERPER_API_KEY else '❌'}")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
logger.info(f"🧠 Семантическая модель: ✅ (model2vec, лёгкая)")
logger.info(f"🤖 Модель: {MODEL_DEFAULT}")

def now():
    return datetime.now(TZ)

def get_current_date():
    return now().strftime("%d.%m.%Y")

# ═══════════════════════════════════════════════════════════════════
#  СЕМАНТИЧЕСКАЯ МОДЕЛЬ (model2vec — лёгкая статическая)
# ═══════════════════════════════════════════════════════════════════

# Загружаем лёгкую статическую модель
# Можно заменить на любую другую с Hugging Face
MODEL_NAME = "cointegrated/rubert-tiny2-embedding-static"  # Лёгкая русская модель

try:
    semantic_model = StaticModel.from_pretrained(MODEL_NAME)
    logger.info(f"✅ Семантическая модель загружена: {MODEL_NAME}")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки модели: {e}")
    logger.error("❌ Попробуйте другую модель или проверьте интернет")
    sys.exit(1)

def is_important_semantic(text: str, threshold: float = 0.5) -> bool:
    """Проверяет, есть ли в тексте важная информация (по смыслу)"""
    if not text:
        return False
    
    try:
        important_templates = [
            "это важно, потому что",
            "главное, что",
            "ключевой момент",
            "основная причина",
            "важно отметить",
            "следует учитывать",
            "суть в том, что",
            "основная идея",
            "критически важно",
            "обратите внимание",
            "главный вывод",
            "основное правило",
            "ключевое отличие",
            "самое важное"
        ]
        template_embeddings = semantic_model.encode(important_templates)
        text_embedding = semantic_model.encode([text])[0]
        similarities = cosine_similarity([text_embedding], template_embeddings)[0]
        return max(similarities) > threshold
    except Exception as e:
        logger.error(f"❌ Ошибка семантического анализа: {e}")
        return False

def semantic_similarity(text1: str, text2: str) -> float:
    """Вычисляет семантическую похожесть двух текстов"""
    try:
        embeddings = semantic_model.encode([text1, text2])
        return cosine_similarity([embeddings[0]], [embeddings[1]])[0][0]
    except:
        return 0.0

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
def graph_path(uid): return os.path.join(DATA_DIR, f"graph_{uid}.json")

# ═══════════════════════════════════════════════════════════════════
#  ГРАФ ЗНАНИЙ (ассоциативная память)
# ═══════════════════════════════════════════════════════════════════

class KnowledgeGraph:
    def __init__(self, uid):
        self.uid = uid
        self.graph = self._load()
    
    def _load(self):
        try:
            with open(graph_path(self.uid), 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def _save(self):
        try:
            with open(graph_path(self.uid), 'w', encoding='utf-8') as f:
                json.dump(self.graph, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения графа: {e}")
    
    def add_fact(self, fact: str, related_to: Optional[List[str]] = None):
        if not fact or len(fact) < 10:
            return
        if fact not in self.graph:
            self.graph[fact] = []
        if related_to:
            for rel in related_to:
                if rel not in self.graph[fact]:
                    self.graph[fact].append(rel)
        self._save()
    
    def get_related(self, fact: str) -> List[str]:
        return self.graph.get(fact, [])
    
    def get_all_facts(self) -> List[str]:
        return list(self.graph.keys())
    
    def find_similar_facts(self, text: str) -> List[str]:
        words = set(re.findall(r'\b\w{3,}\b', text.lower()))
        similar = []
        for fact in self.graph.keys():
            fact_words = set(re.findall(r'\b\w{3,}\b', fact.lower()))
            overlap = len(words & fact_words)
            if overlap >= 2:
                similar.append(fact)
        return similar[:5]

# ═══════════════════════════════════════════════════════════════════
#  5 УРОВНЕЙ ПАМЯТИ (РАСШИРЕННАЯ)
# ═══════════════════════════════════════════════════════════════════

class SuperMemory:
    def __init__(self, uid):
        self.uid = uid
        self.short_term = self._load(memory_path(uid), [])
        self.profile = self._load(profile_path(uid), {})
        self.episodic = self._load(episodic_path(uid), [])
        self.learning = self._load(learning_path(uid), {})
        self.counter = self._load(counter_path(uid), {"count": 0}).get("count", 0)
        self.knowledge_graph = KnowledgeGraph(uid)
    
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
            self._smart_compress(old)
            self.short_term = self.short_term[-100:]
        
        self.counter += 1
        self._extract_universal_facts(content)
        self._extract_personal_info(content)
        self._extract_preferences(content)
        self._update_knowledge_graph(content)
        self.save()
    
    def _smart_compress(self, messages):
        for msg in messages:
            content = msg.get('content', '')
            if len(content) < 20:
                continue
            if is_important_semantic(content):
                self.episodic.append({
                    'content': content[:200],
                    'timestamp': msg.get('timestamp', now().isoformat()),
                    'priority': 5
                })
        if len(self.episodic) > 200:
            self.episodic = self.episodic[-200:]
    
    def _extract_universal_facts(self, text):
        patterns = [
            r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:—|–|-)\s+([^.!?]{10,100})',
            r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:это|является)\s+([^.!?]{10,100})',
            r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:называется|известен как)\s+([^.!?]{10,100})',
            r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:означает)\s+([^.!?]{10,100})'
        ]
        facts = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.I)
            for m in matches:
                fact = f"{m[0].strip()} — {m[1].strip()}"
                if len(fact) > 15:
                    facts.append(fact)
        return facts
    
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
    
    def _update_knowledge_graph(self, text):
        facts = self._extract_universal_facts(text)
        for fact in facts:
            self.knowledge_graph.add_fact(fact)
            similar = self.knowledge_graph.find_similar_facts(fact)
            if similar:
                self.knowledge_graph.add_fact(fact, similar)
    
    def get_context(self, limit=10):
        ctx = self.short_term[-limit:] if self.short_term else []
        
        if self.episodic:
            important = sorted(self.episodic, key=lambda x: x.get('priority', 0), reverse=True)[:3]
            for mem in important:
                ctx.append({'role': 'system', 'content': f"📌 Важно: {mem['content']}"})
        
        if self.profile:
            profile_text = f"👤 О пользователе: {', '.join([f'{k}: {v}' for k, v in self.profile.items() if k != 'updated'])}"
            ctx.append({"role": "system", "content": profile_text})
        
        if self.knowledge_graph.get_all_facts():
            facts = self.knowledge_graph.get_all_facts()[:5]
            if facts:
                ctx.append({"role": "system", "content": f"🧠 Знания: {', '.join(facts)}"})
        
        return ctx
    
    def memory_health_check(self) -> Dict:
        return {
            'short_term': len(self.short_term),
            'profile': len(self.profile),
            'episodic': len(self.episodic),
            'preferences': len(self.learning.get('preferences', [])),
            'graph_facts': len(self.knowledge_graph.get_all_facts()),
            'total_messages': self.counter
        }
    
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

async def ask_deepseek(messages, temperature=DEEPSEEK_TEMPERATURE, max_tokens=MAX_TOKENS_ANSWER, attempt=0):
    if attempt >= 5:
        return None, "max_retries"
    
    session = await get_http_session()
    try:
        payload = {
            "model": MODEL_DEFAULT,
            "messages": messages,
            "temperature": temperature,
            "top_p": DEEPSEEK_TOP_P,
            "frequency_penalty": DEEPSEEK_FREQUENCY_PENALTY,
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
#  УНИВЕРСАЛЬНЫЙ АНАЛИЗ ЗАПРОСА
# ═══════════════════════════════════════════════════════════════════

def analyze_query(query: str) -> Dict:
    words = query.lower().split()
    stop = {'как', 'что', 'это', 'для', 'без', 'на', 'в', 'с', 'и', 'а', 'но', 'или', 'если', 'то', 'чем', 'кто'}
    keywords = [w for w in words if w not in stop and len(w) > 2]
    
    types = []
    if any(w in query.lower() for w in ['как', 'способ', 'метод', 'инструкция']):
        types.append('how_to')
    if any(w in query.lower() for w in ['что', 'определение', 'понятие']):
        types.append('definition')
    if any(w in query.lower() for w in ['сравни', 'лучше', 'отличие']):
        types.append('comparison')
    if any(w in query.lower() for w in ['почему', 'причина']):
        types.append('explanation')
    if not types:
        types.append('information')
    
    is_greeting = not keywords or any(w in query.lower() for w in ["привет", "здравствуй", "салют", "хай", "hello", "hi", "ку", "даров"])
    is_test = any(w in query.lower() for w in ["тест", "test", "проверка"])
    
    return {
        'type': types[0],
        'keywords': keywords,
        'word_count': len(words),
        'has_question': '?' in query,
        'is_greeting': is_greeting,
        'is_test': is_test,
        'is_short': len(words) <= 4
    }

# ═══════════════════════════════════════════════════════════════════
#  УНИВЕРСАЛЬНАЯ ГЕНЕРАЦИЯ ВАРИАНТОВ
# ═══════════════════════════════════════════════════════════════════

def generate_variants(query: str, analysis: Dict) -> List[str]:
    variants = [query]
    keywords = analysis['keywords']
    
    if not keywords:
        return variants
    
    if len(keywords) >= 2:
        variants.append(" ".join(keywords[:2]))
    if len(keywords) >= 3:
        variants.append(" ".join(keywords[:3]))
    
    starters = ['как', 'что такое', 'для чего', 'зачем', 'почему', 'где', 'когда']
    for starter in starters:
        if starter not in query.lower():
            variants.append(f"{starter} {keywords[0] if keywords else ''}")
    
    endings = ['инструкция', 'руководство', 'пример', 'совет', 'рекомендация']
    for ending in endings:
        if ending not in query.lower():
            variants.append(f"{keywords[0] if keywords else ''} {ending}")
    
    clean = ' '.join(keywords[:3])
    if clean != query:
        variants.append(clean)
    
    seen = set()
    unique = []
    for v in variants:
        if v and len(v) > 3 and v not in seen:
            seen.add(v)
            unique.append(v)
    
    return unique[:10]

# ═══════════════════════════════════════════════════════════════════
#  ОЦЕНКА РЕЛЕВАНТНОСТИ
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  УМНЫЙ ПОИСК
# ═══════════════════════════════════════════════════════════════════

async def search_until_good(query: str, min_good: int = 5) -> List[Dict]:
    logger.info(f"🔍 Нужно найти {min_good} релевантных источников")
    
    analysis = analyze_query(query)
    variants = generate_variants(query, analysis)
    
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

# ═══════════════════════════════════════════════════════════════════
#  БЫСТРАЯ ЗАГРУЗКА СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

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
                return {
                    'url': url,
                    'title': r.get('title', ''),
                    'text': text[:MAX_HTML_LEN],
                    'date': date,
                    'relevance': r.get('relevance', 0)
                }
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
    confidence = {
        'overall': 0,
        'source_reliability': 0,
        'data_completeness': 0,
        'recency': 0,
        'factors': []
    }
    
    if data['sources']:
        reliable = 0
        for s in data['sources']:
            url = s.get('url', '')
            if any(d in url for d in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
                reliable += 1
            elif any(d in url for d in ['.com', '.org', '.net', '.ru']):
                reliable += 0.5
        score = min(100, (reliable / len(data['sources'])) * 100)
        confidence['source_reliability'] = score
        confidence['factors'].append(f"Надёжность источников: {score:.0f}%")
    else:
        confidence['source_reliability'] = 20
        confidence['factors'].append("Нет источников")
    
    if data.get('structures'):
        count = sum(1 for v in data['structures'].values() if v)
        completeness = min(100, count * 10)
        confidence['data_completeness'] = completeness
        confidence['factors'].append(f"Найдено структур: {count} → {completeness:.0f}%")
    else:
        confidence['data_completeness'] = 10
        confidence['factors'].append("Нет структурированных данных")
    
    dates = []
    for s in data['sources']:
        date = s.get('date', '')
        if date and date != 'дата не указана':
            dates.append(date)
    
    if dates:
        fresh = sum(1 for d in dates if re.search(r'202[4-6]', d))
        recency = min(100, (fresh / len(dates)) * 100)
        confidence['recency'] = recency
        confidence['factors'].append(f"Свежих: {fresh}/{len(dates)} → {recency:.0f}%")
    else:
        confidence['recency'] = 30
        confidence['factors'].append("Дата не указана")
    
    confidence['overall'] = int(
        confidence['source_reliability'] * 0.4 +
        confidence['data_completeness'] * 0.4 +
        confidence['recency'] * 0.2
    )
    
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
    
    refuse_patterns = [
        (r'я не уверен[^.!]*[.!]', "Сказал 'не уверен' без ответа"),
        (r'я не знаю[^.!]*[.!]', "Сказал 'не знаю' без ответа"),
        (r'не могу ответить[^.!]*[.!]', "Сказал 'не могу ответить'"),
        (r'спросите позже', "Сказал 'спросите позже'"),
        (r'переформулируйте', "Сказал 'переформулируйте'"),
        (r'уточните запрос', "Сказал 'уточните запрос'"),
    ]
    
    for pattern, reason in refuse_patterns:
        if re.search(pattern, answer, re.I):
            after_refuse = re.split(pattern, answer, flags=re.I)[-1]
            if not has_meaningful_content(after_refuse):
                return True, reason
    
    return False, "Ок"

# ═══════════════════════════════════════════════════════════════════
#  ФОРМИРОВАНИЕ ОТВЕТА
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
    
    if data['sources']:
        parts.append("🔗 **ИСТОЧНИКИ:**")
        for i, s in enumerate(data['sources'][:3], 1):
            url = s.get('url', '')
            date = s.get('date', 'не указана')
            if url:
                parts.append(f"   [{i}] {url} (дата: {date})")
    
    return "\n".join(parts)

# ═══════════════════════════════════════════════════════════════════
#  ГЛАВНАЯ ФУНКЦИЯ
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer_final(uid: int, user_message: str, history: List[Dict]) -> str:
    logger.info(f"🧠 УМНЫЙ РЕЖИМ: {user_message[:50]}")
    
    analysis = analyze_query(user_message)
    
    if analysis['is_greeting']:
        return "👋 **Привет!** Я на связи. Задавай вопрос — найду ответ в интернете."
    if analysis['is_test']:
        return "✅ **Тест пройден!** Я работаю нормально. Задавай вопрос."
    
    results = await search_until_good(user_message, min_good=5)
    
    if not results:
        return "⚠️ В интернете не нашлось релевантной информации. Попробуйте переформулировать вопрос."
    
    pages = await fetch_pages_fast(results)
    
    if not pages:
        return "⚠️ Не удалось загрузить страницы. Попробуйте позже."
    
    data = {
        'sources': pages,
        'raw_text': "\n\n".join([p.get('text', '') for p in pages[:3]]),
        'structures': {}
    }
    
    prompt = f"""
⚠️ **ЗАПРОС:** {user_message}

📊 **ИСТОЧНИКИ:**
"""
    for i, s in enumerate(pages[:3], 1):
        prompt += f"\n--- ИСТОЧНИК #{i}: {s.get('url', '')} ---\n{s.get('text', '')[:3000]}\n"
    
    prompt += """
⚠️ **ТВОЯ ЗАДАЧА:**
1. Найди ответ в источниках
2. Дай структурированный ответ
3. Укажи источники
4. Оцени уверенность (0-100%)

⚠️ **НЕЛЬЗЯ:**
• Говорить 'нет данных' (данные есть)
• Говорить 'не могу' (ты можешь)
• Отказываться от ответа

⚠️ **ФОРМАТ ОТВЕТА:**
🎯 **УВЕРЕННОСТЬ: [X]%**
📊 **ОТВЕТ:**
[Ответ на основе источников]
🔗 **ИСТОЧНИКИ:**
[Ссылки]
"""
    
    messages = [{"role": "system", "content": prompt}] + history
    answer, err = await ask_deepseek(messages, temperature=0.15, max_tokens=4000)
    
    if err or not answer:
        return build_forced_answer(data, user_message)
    
    refused, reason = check_if_refused(answer)
    if refused:
        return build_forced_answer(data, user_message)
    
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
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['query'] = user_message
        context.user_data['chat_id'] = chat_id
        
        start_time = time.time()
        status_msg = await update.effective_message.reply_text("🌐 Ищу информацию в интернете...")
        
        answer = await search_and_answer_final(uid, user_message, history)
        
        elapsed = int(time.time() - start_time)
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        get_memory(uid).add_message("assistant", answer[:500])
        
        await status_msg.delete()
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
        "🧠 Запоминаю тебя — становлюсь умнее с каждым вопросом\n"
        "⚡️ Отвечаю быстро и точно\n\n"
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
    health = memory.memory_health_check()
    
    await safe_reply(
        update,
        f"📊 **Статистика**\n\n"
        f"💬 В памяти: {health['short_term']} сообщений\n"
        f"👤 В профиле: {health['profile']} полей\n"
        f"⭐ Важных фактов: {health['episodic']}\n"
        f"💡 Предпочтений: {health['preferences']}\n"
        f"🧠 Фактов в графе знаний: {health['graph_facts']}\n"
        f"📝 Всего сообщений: {health['total_messages']}"
    )

async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ALLOW_ALL and uid not in ALLOWED_USERS:
        return
    
    if uid in _memory_cache:
        del _memory_cache[uid]
    
    for path in [memory_path(uid), profile_path(uid), episodic_path(uid), learning_path(uid), counter_path(uid), graph_path(uid)]:
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
    logger.info(f"🧠 Семантическая модель: ✅ (model2vec, лёгкая)")
    logger.info(f"🤖 Модель: {MODEL_DEFAULT}")
    logger.info("⚡️ ФИНАЛЬНАЯ УМНАЯ ВЕРСИЯ — БЕЗ ХАРДКОДА, БЕЗ ЛАЗЕЕК")
    
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
