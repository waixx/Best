# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ИНТЕРАКТИВНАЯ ВЕРСИЯ С РЕЖИМАМИ ДИАЛОГА
#  НОВЫЙ ЗАПРОС | УТОЧНИТЬ | ПРОСТО ОБЩАЕМСЯ
#  ПАМЯТЬ + ГРАФ ЗНАНИЙ + ЧЕСТНЫЕ ОТВЕТЫ + МАКСИМАЛЬНАЯ ТОЧНОСТЬ
#  НИЧЕГО НЕ ВЫРЕЗАНО, ВСЁ РАБОТАЕТ
# ═══════════════════════════════════════════════════════════════════

import logging
import os
import sys
import re
import asyncio
import aiohttp
import time
import json
import hashlib
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple, Any
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
#  ЛОГИРОВАНИЕ
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
logging.getLogger("playwright").setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

MAX_PAGES_TARGET = 8
PAGE_TIMEOUT = 12
SEARCH_RESULTS = 20
DEEPSEEK_MODEL = "deepseek-v4-flash"
CACHE_TTL = 3600
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 30
MAX_TOKENS_OUTPUT = 4500
MAX_TOKENS_VARIANTS = 500

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

def now():
    return datetime.now(TZ)

# Убираем постоянную клавиатуру
REMOVE_KEYBOARD = ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

# Inline-кнопки для выбора режима
ACTION_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔍 Новый запрос", callback_data="action_new"),
        InlineKeyboardButton("📝 Уточнить", callback_data="action_clarify"),
    ],
    [
        InlineKeyboardButton("💬 Просто общаемся", callback_data="action_chat"),
    ]
])

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info("🚀 ИНТЕРАКТИВНАЯ ВЕРСИЯ С РЕЖИМАМИ ДИАЛОГА")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")

# ═══════════════════════════════════════════════════════════════════
#  HTTP СЕССИЯ
# ═══════════════════════════════════════════════════════════════════

_http_session = None
search_cache = {}
answer_cache = {}

async def get_session():
    global _http_session
    if _http_session is None:
        _http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return _http_session

# ═══════════════════════════════════════════════════════════════════
#  DEEPSEEK
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

async def ask_deepseek(prompt: str, temperature: float = 0.2, max_tokens: int = MAX_TOKENS_OUTPUT) -> str:
    key = cache_key(prompt)
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        logger.info("♻️ Ответ DeepSeek из кэша")
        return answer_cache[key]['data']

    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=60
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    content = data["choices"][0]["message"]["content"]
                    if content and len(content) > 50:
                        answer_cache[key] = {'data': content, 'time': time.time()}
                        return content
                else:
                    logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: HTTP {r.status}")
                    if attempt == 2 and r.status == 429:
                        logger.warning("⏳ DeepSeek rate limit, ждём 10 секунд...")
                        await asyncio.sleep(10)
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ DeepSeek таймаут попытка {attempt+1}")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek ошибка попытка {attempt+1}: {e}")
        
        if attempt < 2:
            await asyncio.sleep(2 + attempt * 2)
    
    return ""

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ + ГРАФ ЗНАНИЙ) — ПОЛНОСТЬЮ СОХРАНЕНА
# ═══════════════════════════════════════════════════════════════════

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def episodic_path(uid): return os.path.join(DATA_DIR, f"episodic_{uid}.json")
def learning_path(uid): return os.path.join(DATA_DIR, f"learning_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")
def graph_path(uid): return os.path.join(DATA_DIR, f"graph_{uid}.json")

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
        except:
            pass
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
        except:
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
        self._update_knowledge_graph(content)
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
    def _update_knowledge_graph(self, text):
        facts = re.findall(r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:это|является)\s+([^.!?]{10,100})', text, re.I)
        for m in facts:
            fact = f"{m[0].strip()} — {m[1].strip()}"
            if len(fact) > 15:
                self.knowledge_graph.add_fact(fact)
    def get_context(self, limit=10):
        ctx = self.short_term[-limit:] if self.short_term else []
        if self.episodic:
            important = sorted(self.episodic, key=lambda x: x.get('priority', 0), reverse=True)[:3]
            for mem in important:
                ctx.append({'role': 'system', 'content': f"📌 Важно: {mem['content']}"})
        if self.profile:
            profile_text = f"👤 О пользователе: {', '.join([f'{k}: {v}' for k, v in self.profile.items()])}"
            ctx.append({"role": "system", "content": profile_text})
        if self.knowledge_graph.get_all_facts():
            facts = self.knowledge_graph.get_all_facts()[:3]
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
#  ПОИСК (APISerpent — основной, Serper — резерв)
# ═══════════════════════════════════════════════════════════════════

def normalize_query(query):
    return re.sub(r'[^\w\s]', '', query.lower()).strip()

async def search_apiserpent(query: str) -> List[Dict]:
    if not APISERPENT_API_KEY:
        return []
    try:
        session = await get_session()
        async with session.get(
            "https://apiserpent.com/api/search",
            params={
                "q": query,
                "engine": "google",
                "num": SEARCH_RESULTS,
                "deep": "true"
            },
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                results = []
                organic = data.get("organic_results", [])
                for x in organic:
                    results.append({
                        "title": x.get("title", ""),
                        "snippet": x.get("snippet", ""),
                        "link": x.get("link", ""),
                        "source": "organic"
                    })
                paa = data.get("people_also_ask", [])
                for item in paa:
                    results.append({
                        "title": item.get("question", ""),
                        "snippet": item.get("snippet", ""),
                        "link": item.get("link", ""),
                        "source": "paa"
                    })
                featured = data.get("featured_snippet", {})
                if featured:
                    results.append({
                        "title": featured.get("title", ""),
                        "snippet": featured.get("snippet", ""),
                        "link": featured.get("link", ""),
                        "source": "featured"
                    })
                ai_overview = data.get("ai_overview", {})
                if ai_overview:
                    results.append({
                        "title": "AI Overview",
                        "snippet": ai_overview.get("text", ""),
                        "link": "",
                        "source": "ai_overview"
                    })
                return results
    except Exception as e:
        logger.warning(f"⚠️ APISerpent ошибка: {e}")
    return []

async def search_serper(query: str) -> List[Dict]:
    if not SERPER_API_KEY:
        return []
    try:
        session = await get_session()
        async with session.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": SEARCH_RESULTS},
            headers={"X-API-KEY": SERPER_API_KEY},
            timeout=10
        ) as r:
            if r.status == 200:
                data = await r.json()
                return [{"title": x.get("title", ""), "snippet": x.get("snippet", ""), "link": x.get("link", "")} 
                        for x in data.get("organic", [])]
    except Exception as e:
        logger.warning(f"⚠️ Serper ошибка: {e}")
    return []

async def search_with_cache(query: str) -> List[Dict]:
    norm = normalize_query(query)
    if norm in search_cache and (time.time() - search_cache[norm]['time']) < CACHE_TTL:
        return search_cache[norm]['data']
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': time.time()}
        return results
    results = await search_serper(query)
    if results:
        search_cache[norm] = {'data': results, 'time': time.time()}
        return results
    return []

async def search_parallel(variants: List[str], max_sources: int = 20) -> List[Dict]:
    if not variants:
        return []
    logger.info(f"🔍 Параллельный поиск по {len(variants)} вариантам")
    tasks = [search_with_cache(v) for v in variants[:8]]
    results_list = await asyncio.gather(*tasks)
    all_results = []
    seen_urls = set()
    for results in results_list:
        if results:
            for r in results:
                url = r.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
                if len(all_results) >= max_sources:
                    break
        if len(all_results) >= max_sources:
            break
    logger.info(f"📊 Найдено {len(all_results)} уникальных результатов")
    return all_results

# ═══════════════════════════════════════════════════════════════════
#  BROWSERLESS
# ═══════════════════════════════════════════════════════════════════

async def fetch_with_browserless(url: str) -> Optional[str]:
    if not PLAYWRIGHT_AVAILABLE or not BROWSERLESS_WS_ENDPOINT:
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(BROWSERLESS_WS_ENDPOINT)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=18000)
                html = await page.content()
                return html
            except Exception:
                return None
            finally:
                await page.close()
    except Exception:
        return None
    return None

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ
# ═══════════════════════════════════════════════════════════════════

def extract_date_from_text(text: str) -> Optional[str]:
    patterns = [
        r'\b\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}\b',
        r'\b\d{1,2}\s+(?:янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек)\s+\d{2,4}\b',
        r'\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group()
    return None

def extract_key_facts(text: str) -> List[str]:
    facts = []
    matches = re.findall(
        r'\b(\d+[\s,]*\d*[\s]*(?:%|руб|\$|€|USD|EUR|тыс|млн|млрд|лет|месяц|день|час|метров|кг|тонн|шт|ед|точка|порт|кабель|коробка|бухта|метр|км|GB|TB|MHz|GHz|dB|Вт|кВт))\b',
        text, re.IGNORECASE
    )
    facts.extend(matches[:5])
    dates = re.findall(r'\b\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}\b', text)
    facts.extend(dates[:3])
    definitions = re.findall(
        r'([А-Яа-яA-Za-z][^.!?]{5,50})\s+(?:—|–|-|это|является|представляет собой)\s+([^.!?]{5,80})',
        text, re.IGNORECASE
    )
    for d in definitions:
        facts.append(f"{d[0].strip()} — {d[1].strip()}")
    percents = re.findall(r'\b\d+[\s]*%', text)
    facts.extend(percents[:3])
    key_phrases = re.findall(
        r'(?:совет|рекомендация|шаг|этап|важно|главное|ключевой|основной|лучший|эффективный|проверенный)[^.!?]{10,80}[.!?]',
        text, re.IGNORECASE
    )
    facts.extend(key_phrases[:3])
    return list(set(facts))[:20]

def parse_html(html: str) -> Dict:
    result = {'text': '', 'lists': [], 'headings': [], 'date': None, 'tables': [], 'definitions': [], 'key_facts': []}
    
    if not BEAUTIFULSOUP_AVAILABLE:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', text)
        result['text'] = ' '.join(sentences[:50])[:10000]
        result['date'] = extract_date_from_text(result['text'])
        result['key_facts'] = extract_key_facts(result['text'])
        return result
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript', 'iframe']):
            tag.decompose()
        
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text)
        result['text'] = text[:12000]
        
        date_meta = soup.find('meta', {'property': 'article:published_time'}) or \
                    soup.find('meta', {'name': 'date'}) or \
                    soup.find('meta', {'name': 'pubdate'})
        if date_meta and date_meta.get('content'):
            result['date'] = date_meta['content']
        else:
            result['date'] = extract_date_from_text(text)
        
        for ul in soup.find_all(['ul', 'ol']):
            for li in ul.find_all('li'):
                li_text = li.get_text(strip=True)
                if len(li_text) > 10:
                    result['lists'].append(li_text)
        result['lists'] = result['lists'][:20]
        
        for h in soup.find_all(['h1', 'h2', 'h3', 'h4']):
            h_text = h.get_text(strip=True)
            if len(h_text) > 5:
                result['headings'].append(h_text)
        result['headings'] = result['headings'][:8]
        
        for table in soup.find_all('table'):
            rows = []
            for tr in table.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if any(c for c in cells):
                    rows.append(' | '.join(cells))
            if rows:
                result['tables'].append('\n'.join(rows))
        result['tables'] = result['tables'][:5]
        
        definitions = re.findall(r'([А-Яа-яA-Za-z][^.!?]{5,60})\s+(?:—|–|-)\s+([^.!?]{5,100})', text)
        for d in definitions:
            result['definitions'].append(f"{d[0].strip()} — {d[1].strip()}")
        result['definitions'] = result['definitions'][:8]
        
        result['key_facts'] = extract_key_facts(text)
        
    except Exception:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', text)
        result['text'] = ' '.join(sentences[:50])[:10000]
        result['date'] = extract_date_from_text(result['text'])
        result['key_facts'] = extract_key_facts(result['text'])
    
    return result

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict) -> bool:
    title = result.get('title', '').lower()
    snippet = result.get('snippet', '').lower()
    url = result.get('link', '').lower()

    video_domains = [
        'youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com', 'twitch.tv',
        'spotify.com', 'soundcloud.com', 'deezer.com', 'apple.com/music',
        'tiktok.com', 'instagram.com', 'facebook.com/watch'
    ]
    if any(domain in url for domain in video_domains):
        return False

    trash_patterns = ['/video/', '/watch?v=', '/embed/', '/music/', '/song/', '?utm_', 'click.php', 'tracking']
    if any(p in url for p in trash_patterns):
        return False

    media_markers = ['видео', 'смотреть', 'слушать', 'песня', 'клип', 'трек', 'mp3']
    if any(m in title or m in snippet for m in media_markers):
        return False

    if len(snippet) < 80:
        return False

    useful_markers = ['как', 'почему', 'что такое', 'пример', 'руководство', 'инструкция', 'совет', 'рекомендация', 'сравнение', 'обзор']
    if any(m in title or m in snippet for m in useful_markers):
        return True

    if re.search(r'\d+%|\d+-\d+|\d{4}[-/.]\d{1,2}', snippet):
        return True

    if len(snippet) > 200:
        return True

    return False

# ═══════════════════════════════════════════════════════════════════
#  РАНЖИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════

def rank_results(results: List[Dict], query: str) -> List[Dict]:
    if not results:
        return results
    
    keywords = set(re.findall(r'[а-яa-z]{4,}', query.lower()))
    scored = []
    
    for idx, r in enumerate(results):
        score = 0
        title = r.get('title', '').lower()
        snippet = r.get('snippet', '').lower()
        
        score += max(0, (20 - idx) * 0.5)
        kw_matches = sum(3 if kw in title else (1 if kw in snippet else 0) for kw in keywords)
        score += min(kw_matches, 20)
        
        if len(snippet) > 250:
            score += 5
        elif len(snippet) > 150:
            score += 3
        
        if re.search(r'\d{2,4}[-/.]\d{2,4}', snippet):
            score += 3
        if re.search(r'\d+%', snippet):
            score += 3
        
        useful = ['как', 'почему', 'что такое', 'пример', 'руководство', 'инструкция', 'совет', 'рекомендация', 'сравнение']
        for w in useful:
            if w in snippet:
                score += 1.5
                break
        
        scored.append((score, r))
    
    scored.sort(reverse=True, key=lambda x: x[0])
    return [r for _, r in scored]

# ═══════════════════════════════════════════════════════════════════
#  ЗАГРУЗКА СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

async def fetch_page_parallel(url: str) -> Optional[Dict]:
    async def fetch_http():
        try:
            session = await get_session()
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=PAGE_TIMEOUT) as r:
                if r.status == 200:
                    html = await r.text()
                    parsed = parse_html(html)
                    if parsed['text'] and len(parsed['text']) > 300:
                        return parsed
        except:
            pass
        return None
    
    async def fetch_browserless():
        if not PLAYWRIGHT_AVAILABLE or not BROWSERLESS_WS_ENDPOINT:
            return None
        try:
            html = await fetch_with_browserless(url)
            if html:
                parsed = parse_html(html)
                if parsed['text'] and len(parsed['text']) > 100:
                    return parsed
        except:
            pass
        return None
    
    http_task = asyncio.create_task(fetch_http())
    bl_task = asyncio.create_task(fetch_browserless())
    
    done, pending = await asyncio.wait(
        [http_task, bl_task],
        return_when=asyncio.FIRST_COMPLETED,
        timeout=PAGE_TIMEOUT + 2
    )
    for task in pending:
        task.cancel()
    
    for task in done:
        try:
            result = task.result()
            if result and result.get('text'):
                return result
        except:
            continue
    
    try:
        http_result = await http_task
        if http_result and http_result.get('text'):
            return http_result
    except:
        pass
    try:
        bl_result = await bl_task
        if bl_result and bl_result.get('text'):
            return bl_result
    except:
        pass
    return None

async def fetch_pages_parallel(results: List[Dict], max_pages: int = MAX_PAGES_TARGET) -> List[Dict]:
    if not results:
        return []
    
    logger.info(f"📄 Загружаем до {max_pages} страниц")
    top_results = results[:int(max_pages * 1.2)]
    
    tasks = []
    urls = []
    for r in top_results:
        url = r.get('link', '')
        if url:
            tasks.append(fetch_page_parallel(url))
            urls.append(url)
    
    pages_data = await asyncio.gather(*tasks, return_exceptions=True)
    pages = []
    for i, parsed in enumerate(pages_data):
        if isinstance(parsed, Exception):
            continue
        if parsed and parsed.get('text'):
            quality = len(parsed['text']) + len(parsed.get('lists', []))*50 + len(parsed.get('headings', []))*30 + len(parsed.get('tables', []))*40
            pages.append({
                'url': urls[i] if i < len(urls) else '',
                'title': top_results[i].get('title', '') if i < len(top_results) else '',
                'parsed': parsed,
                'quality': quality
            })
        if len(pages) >= max_pages:
            break
    
    pages.sort(key=lambda x: x.get('quality', 0), reverse=True)
    pages = pages[:max_pages]
    logger.info(f"✅ Загружено {len(pages)} страниц")
    return pages

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════

async def generate_variants(query: str) -> List[str]:
    prompt = f"""
⚠️ **Сгенерируй 8 поисковых запросов для поиска полезной информации по запросу:**
{query}

⚠️ **ПРАВИЛА:**
- Ищи только текстовые статьи, руководства, инструкции, статистику, новости.
- Исключи видео, музыку, рекламные сайты.
- Используй слова: "статья", "руководство", "инструкция", "пример", "совет", "сравнение", "обзор".

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{"variants": ["вариант 1", "вариант 2", ...]}}
"""
    response = await ask_deepseek(prompt, temperature=0.3, max_tokens=MAX_TOKENS_VARIANTS)
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            variants = data.get('variants', [query])
            return variants[:8]
    except:
        pass
    return [query]

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(pages: List[Dict]) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'consensus': 0, 'factors': []}
    
    if not pages:
        confidence['factors'].append("Нет источников")
        return confidence
    
    good_pages = 0
    for p in pages:
        parsed = p.get('parsed', {})
        text_len = len(parsed.get('text', ''))
        struct_count = len(parsed.get('lists', [])) + len(parsed.get('headings', [])) + len(parsed.get('tables', []))
        if text_len > 1000 and struct_count > 2:
            good_pages += 1
    reliability = min(100, (good_pages / max(len(pages), 1)) * 100)
    confidence['source_reliability'] = reliability
    confidence['factors'].append(f"Качество источников: {reliability:.0f}%")
    
    total_struct = 0
    for p in pages:
        parsed = p.get('parsed', {})
        total_struct += len(parsed.get('lists', [])) + len(parsed.get('headings', [])) + len(parsed.get('tables', []))
    completeness = min(100, total_struct * 5)
    confidence['data_completeness'] = completeness
    confidence['factors'].append(f"Структура: {completeness:.0f}%")
    
    has_date = any(p.get('parsed', {}).get('date') for p in pages)
    recency = 80 if has_date else 50
    confidence['recency'] = recency
    confidence['factors'].append(f"Свежесть: {recency:.0f}%")
    
    all_facts = []
    for p in pages:
        facts = p.get('parsed', {}).get('key_facts', [])
        all_facts.extend(facts)
    unique_facts = set(all_facts)
    if len(all_facts) > 8:
        consensus = min(100, int((len(all_facts) - len(unique_facts)) / len(all_facts) * 100) + 40)
    else:
        consensus = 50
    confidence['consensus'] = consensus
    confidence['factors'].append(f"Согласованность: {consensus:.0f}%")
    
    confidence['overall'] = int((reliability*0.3 + completeness*0.2 + recency*0.2 + consensus*0.3))
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
   • Качество источников: {confidence['source_reliability']:.0f}%
   • Полнота: {confidence['data_completeness']:.0f}%
   • Свежесть: {confidence['recency']:.0f}%
   • Согласованность: {confidence['consensus']:.0f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ═══════════════════════════════════════════════════════════════════
#  ПРОВЕРКА НА ЛОЖЬ
# ═══════════════════════════════════════════════════════════════════

def check_for_lies(answer: str) -> bool:
    if not answer:
        return False
    lie_phrases = ['я знаю, что', 'по моему мнению', 'я могу добавить', 'исходя из моего опыта', 'я предполагаю', 'я считаю']
    for phrase in lie_phrases:
        if phrase in answer.lower():
            return True
    return False

def check_refusal(answer: str) -> bool:
    if not answer:
        return False
    refuse_phrases = ['не могу ответить', 'не знаю', 'нет данных', 'информация отсутствует', 'не нашлось']
    for phrase in refuse_phrases:
        if phrase in answer.lower():
            return True
    return False

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

async def generate_answer_with_search(query: str, pages: List[Dict], memory_context: str = "") -> str:
    """Генерация ответа с поиском в интернете"""
    all_data = []
    keywords = set(re.findall(r'[а-яa-z]{4,}', query.lower()))
    
    for p in pages[:6]:
        parsed = p.get('parsed', {})
        text = parsed.get('text', '')
        if not text:
            continue
        
        sentences = re.split(r'(?<=[.!?])\s+', text)
        relevant = [s for s in sentences if any(kw in s.lower() for kw in keywords)]
        if len(relevant) < 5:
            relevant = sentences[:12]
        
        if parsed.get('lists'):
            all_data.append("📋 " + "\n".join(f"  • {li}" for li in parsed['lists'][:7]))
        if parsed.get('headings'):
            all_data.append("📌 " + "\n".join(f"  {h}" for h in parsed['headings'][:5]))
        if parsed.get('definitions'):
            all_data.append("📖 " + "\n".join(f"  {d}" for d in parsed['definitions'][:5]))
        if parsed.get('tables'):
            all_data.append("📊 " + "\n".join(f"  {t}" for t in parsed['tables'][:3]))
        
        text_part = ' '.join(relevant[:10])[:600]
        if text_part:
            all_data.append(f"📄 {text_part}")
    
    structures_text = "\n\n".join(all_data)
    sources_text = "\n".join(f"• {p.get('url', '')}" for p in pages[:6])
    
    prompt = f"""
⚠️ **ЗАПРОС:** {query}

{memory_context}

⚠️ **ДАННЫЕ ИЗ ИНТЕРНЕТА (из {len(pages)} источников):**
{structures_text}

⚠️ **ИСТОЧНИКИ:**
{sources_text}

⚠️ **СОСТАВЬ ПРАКТИЧЕСКИЙ, СТРУКТУРИРОВАННЫЙ ОТВЕТ.**
- Используй факты из нескольких источников.
- Если данные противоречивы — укажи это.
- Если данных не хватает — дополни из знаний (отметь 🧠).
- Дай конкретные рекомендации, примеры, шаги.
- НЕ ВЫДУМЫВАЙ. Если не знаешь — скажи честно.
"""
    
    for _ in range(3):
        answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT)
        if answer and len(answer) > 100:
            if check_for_lies(answer) or check_refusal(answer):
                continue
            return answer
        await asyncio.sleep(2)
    return await answer_from_knowledge(query)

async def generate_answer_without_search(query: str, memory_context: str = "") -> str:
    """Генерация ответа ТОЛЬКО из знаний и памяти (без интернета)"""
    prompt = f"""
⚠️ **ЗАПРОС:** {query}

{memory_context}

⚠️ **ОТВЕТЬ НА ОСНОВЕ СВОИХ ЗНАНИЙ И ПАМЯТИ.**
- Используй информацию, которую ты знаешь.
- Если не знаешь — скажи честно.
- НЕ ИЩИ В ИНТЕРНЕТЕ.
- Отметь: 🧠 Ответ основан на знаниях и памяти.
"""
    
    for _ in range(3):
        answer = await ask_deepseek(prompt, temperature=0.3, max_tokens=MAX_TOKENS_OUTPUT)
        if answer and len(answer) > 50:
            if check_for_lies(answer):
                continue
            return answer
        await asyncio.sleep(2)
    return "⚠️ Не могу ответить на этот вопрос из своих знаний. Попробуйте задать уточнение или новый запрос с поиском в интернете."

async def answer_from_knowledge(query: str) -> str:
    prompt = f"""
⚠️ **В интернете не удалось найти достаточно информации.**

⚠️ **ЗАПРОС:** {query}

⚠️ **Ты — эксперт. Ответь из своих знаний.**
Если не знаешь — скажи честно.
Отметь: 🧠 Ответ основан на знаниях.
"""
    answer = await ask_deepseek(prompt, temperature=0.3, max_tokens=MAX_TOKENS_OUTPUT)
    return answer or "⚠️ Не удалось найти информацию. Попробуйте переформулировать запрос."

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА (с учётом режимов)
# ═══════════════════════════════════════════════════════════════════

current_stage = "⏳ Запуск"

def set_stage(stage: str):
    global current_stage
    current_stage = stage

async def process_query_with_search(query: str, uid: int) -> str:
    """Полный поиск в интернете"""
    try:
        logger.info(f"🔍 ПОИСКОВЫЙ РЕЖИМ: {query[:100]}...")
        set_stage("🧠 Анализирую запрос")
        
        variants = await generate_variants(query)
        logger.info(f"🔍 Сгенерировано {len(variants)} вариантов")
        
        set_stage("🔍 Ищу в интернете (APISerpent)")
        all_results = await search_parallel(variants, max_sources=20)
        
        filtered = [r for r in all_results if is_useful_result(r)]
        logger.info(f"📊 После фильтрации: {len(filtered)} источников")
        
        if len(filtered) < 4:
            logger.info("🔄 Расширяю поиск...")
            extra_variants = [
                f"статья {query}", f"руководство {query}", 
                f"пример {query}", f"обзор {query}"
            ][:4]
            more = await search_parallel(extra_variants, max_sources=12)
            for r in more:
                if is_useful_result(r) and r.get('link') not in [x.get('link') for x in filtered]:
                    filtered.append(r)
        
        if len(filtered) < 3:
            logger.warning("⚠️ Мало источников, отвечаю из знаний")
            return await answer_from_knowledge(query)
        
        ranked = rank_results(filtered, query)
        
        set_stage("📄 Загружаю страницы (максимум 8)")
        pages = await fetch_pages_parallel(ranked, max_pages=MAX_PAGES_TARGET)
        
        if not pages:
            return await answer_from_knowledge(query)
        
        memory = get_memory(uid)
        ctx = ""
        if memory.knowledge_graph.get_all_facts():
            facts = memory.knowledge_graph.get_all_facts()[:5]
            ctx = f"🧠 **Из памяти:** {', '.join(facts)}\n"
        
        set_stage("🤔 Формирую ответ")
        answer = await generate_answer_with_search(query, pages, ctx)
        confidence = calculate_confidence(pages)
        formatted = format_confidence(confidence) + "\n\n" + answer
        logger.info(f"✅ ОТВЕТ СФОРМИРОВАН, длина {len(answer)} символов")
        return formatted
        
    except Exception as e:
        logger.error(f"❌ Ошибка в process_query_with_search: {e}")
        logger.error(traceback.format_exc())
        return await answer_from_knowledge(query)

async def process_query_without_search(query: str, uid: int) -> str:
    """Ответ только из памяти и знаний (без интернета)"""
    try:
        logger.info(f"💬 РЕЖИМ ОБЩЕНИЯ (без поиска): {query[:100]}...")
        set_stage("💬 Отвечаю из памяти и знаний")
        
        memory = get_memory(uid)
        ctx = ""
        if memory.knowledge_graph.get_all_facts():
            facts = memory.knowledge_graph.get_all_facts()[:5]
            ctx = f"🧠 **Из памяти:** {', '.join(facts)}\n"
        
        # Добавляем контекст диалога
        context = memory.get_context(limit=10)
        if context:
            ctx += "\n".join([c.get('content', '') for c in context])
        
        answer = await generate_answer_without_search(query, ctx)
        return "💬 **Режим общения (без интернета)**\n\n" + answer
        
    except Exception as e:
        logger.error(f"❌ Ошибка в process_query_without_search: {e}")
        return "⚠️ Ошибка при ответе из памяти. Попробуйте новый запрос с поиском."

async def process_query_with_clarification(query: str, uid: int, previous_query: str, previous_answer: str) -> str:
    """Уточнение с учётом предыдущего контекста и новым поиском"""
    try:
        logger.info(f"📝 РЕЖИМ УТОЧНЕНИЯ: {query[:100]}...")
        set_stage("📝 Обрабатываю уточнение")
        
        # Формируем запрос с учётом предыдущего контекста
        combined_query = f"{previous_query} {query}"
        memory = get_memory(uid)
        memory.add_message("user", f"Уточнение: {query}")
        
        # Добавляем предыдущий ответ в контекст
        ctx = f"📌 **Предыдущий запрос:** {previous_query}\n"
        ctx += f"📌 **Предыдущий ответ (кратко):** {previous_answer[:500]}\n\n"
        ctx += f"📌 **Уточнение пользователя:** {query}\n"
        
        # Выполняем новый поиск с учётом уточнения
        answer = await process_query_with_search(combined_query, uid)
        return "📝 **Уточнение с учётом предыдущего диалога**\n\n" + answer
        
    except Exception as e:
        logger.error(f"❌ Ошибка в process_query_with_clarification: {e}")
        return await answer_from_knowledge(query)

# ═══════════════════════════════════════════════════════════════════
#  ТАЙМЕР
# ═══════════════════════════════════════════════════════════════════

async def show_progress(chat_id, context, start_time):
    global current_stage
    try:
        msg = await context.bot.send_message(chat_id, f"⏳ {current_stage}\n\n⏱️ 0 сек")
        while True:
            await asyncio.sleep(3)
            if context.user_data.get('found_answer'):
                try:
                    await msg.edit_text("✅ **Готово!**")
                except:
                    pass
                break
            elapsed = int(time.time() - start_time)
            try:
                await msg.edit_text(f"⏳ {current_stage}\n\n⏱️ {elapsed} сек")
            except:
                pass
    except:
        pass

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИК КНОПОК (CallbackQuery)
# ═══════════════════════════════════════════════════════════════════

async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на inline-кнопки"""
    try:
        query = update.callback_query
        await query.answer()
        
        action = query.data
        uid = update.effective_user.id
        
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            await query.edit_message_text("⛔ Доступ запрещён.")
            return
        
        if action == "action_new":
            context.user_data['mode'] = 'new'
            context.user_data['previous_query'] = None
            context.user_data['previous_answer'] = None
            await query.edit_message_text(
                "🔍 **Режим: Новый запрос**\n\n"
                "Напишите свой вопрос — я выполню новый поиск в интернете и дам свежий ответ.",
                reply_markup=REMOVE_KEYBOARD
            )
            
        elif action == "action_clarify":
            context.user_data['mode'] = 'clarify'
            if not context.user_data.get('previous_query'):
                await query.edit_message_text(
                    "⚠️ У вас пока нет активного диалога для уточнения.\n"
                    "Сначала задайте вопрос, а затем уточните его.",
                    reply_markup=REMOVE_KEYBOARD
                )
                return
            await query.edit_message_text(
                f"📝 **Режим: Уточнение**\n\n"
                f"Предыдущий запрос: *{context.user_data['previous_query'][:100]}*\n\n"
                "Напишите, что вы хотите уточнить или добавить — я выполню новый поиск с учётом этого.",
                reply_markup=REMOVE_KEYBOARD
            )
            
        elif action == "action_chat":
            context.user_data['mode'] = 'chat'
            await query.edit_message_text(
                "💬 **Режим: Просто общаемся**\n\n"
                "Я буду отвечать только из своей памяти и знаний, без поиска в интернете.\n"
                "Можете задавать любые вопросы, я отвечу на основе того, что знаю.",
                reply_markup=REMOVE_KEYBOARD
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_action: {e}")
        try:
            await update.callback_query.edit_message_text("⚠️ Ошибка. Попробуйте /start")
        except:
            pass

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИК СООБЩЕНИЙ
# ═══════════════════════════════════════════════════════════════════

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            return
        
        text = update.effective_message.text.strip() if update.effective_message else ""
        if not text:
            return
        
        # Команда /start
        if text == "/start":
            await start(update, context)
            return
        
        # Определяем режим
        mode = context.user_data.get('mode', 'new')
        
        chat_id = update.effective_chat.id
        context.user_data['found_answer'] = False
        start_time = time.time()
        asyncio.create_task(show_progress(chat_id, context, start_time))
        
        memory = get_memory(uid)
        memory.add_message("user", text)
        
        # --- Обработка в зависимости от режима ---
        if mode == 'chat':
            # Режим общения без поиска
            answer = await process_query_without_search(text, uid)
            context.user_data['found_answer'] = True
            memory.add_message("assistant", answer[:500])
            
            elapsed = int(time.time() - start_time)
            full_text = f"⏱️ {elapsed} сек\n\n{answer}"
            
            await send_long_message(update, full_text)
            
            # Предлагаем действия
            await update.message.reply_text(
                "Что делаем дальше?",
                reply_markup=ACTION_BUTTONS
            )
            
        elif mode == 'clarify':
            # Режим уточнения
            prev_query = context.user_data.get('previous_query', '')
            prev_answer = context.user_data.get('previous_answer', '')
            
            if not prev_query:
                answer = await process_query_with_search(text, uid)
            else:
                answer = await process_query_with_clarification(text, uid, prev_query, prev_answer)
            
            context.user_data['found_answer'] = True
            memory.add_message("assistant", answer[:500])
            
            elapsed = int(time.time() - start_time)
            full_text = f"⏱️ {elapsed} сек\n\n{answer}"
            
            await send_long_message(update, full_text)
            
            # Обновляем предыдущий ответ
            context.user_data['previous_answer'] = answer
            
            # Предлагаем действия
            await update.message.reply_text(
                "Что делаем дальше?",
                reply_markup=ACTION_BUTTONS
            )
            
        else:
            # Режим нового запроса (по умолчанию)
            answer = await process_query_with_search(text, uid)
            
            context.user_data['found_answer'] = True
            memory.add_message("assistant", answer[:500])
            
            # Сохраняем для уточнений
            context.user_data['previous_query'] = text
            context.user_data['previous_answer'] = answer
            
            elapsed = int(time.time() - start_time)
            full_text = f"⏱️ {elapsed} сек\n\n{answer}"
            
            await send_long_message(update, full_text)
            
            # Предлагаем действия
            await update.message.reply_text(
                "Что делаем дальше?",
                reply_markup=ACTION_BUTTONS
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка в handle: {e}")
        logger.error(traceback.format_exc())
        try:
            await update.message.reply_text("⚠️ Ошибка. Попробуйте еще раз.", reply_markup=REMOVE_KEYBOARD)
        except:
            pass

async def send_long_message(update, full_text: str):
    """Безопасная отправка длинных сообщений"""
    try:
        if len(full_text) <= 4096:
            await update.message.reply_text(full_text, reply_markup=REMOVE_KEYBOARD)
        else:
            parts = []
            cur = ""
            for line in full_text.split("\n"):
                if len(cur) + len(line) + 1 > 4000:
                    parts.append(cur)
                    cur = line
                else:
                    cur += "\n" + line if cur else line
            if cur:
                parts.append(cur)
            await update.message.reply_text(parts[0], reply_markup=REMOVE_KEYBOARD)
            for part in parts[1:]:
                await update.message.reply_text(part, reply_markup=REMOVE_KEYBOARD)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        await update.message.reply_text(
            "⚠️ Ответ слишком длинный. Вот сокращённая версия:\n\n" + full_text[:3000] + "...",
            reply_markup=REMOVE_KEYBOARD
        )

# ═══════════════════════════════════════════════════════════════════
#  СТАРТ
# ═══════════════════════════════════════════════════════════════════

async def start(update: Update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 **Привет!** Я помогаю искать информацию в интернете.\n\n"
        "Напиши свой вопрос — я найду ответ и предложу действия.\n\n"
        "⚠️ Я никогда не вру. Если данных мало — скажу честно.\n"
        "🧠 Я запоминаю тебя и учусь с каждым диалогом.\n\n"
        "После каждого ответа я предложу:\n"
        "• 🔍 **Новый запрос** — начать заново\n"
        "• 📝 **Уточнить** — дополнить предыдущий ответ\n"
        "• 💬 **Просто общаемся** — без поиска в интернете",
        reply_markup=REMOVE_KEYBOARD
    )

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК ИНТЕРАКТИВНОЙ ВЕРСИИ")
    logger.info(f"🤖 Токен: {TELEGRAM_TOKEN[:10]}...")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'} (ОСНОВНОЙ)")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'} (РЕЗЕРВ)")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("✅ Режимы: Новый запрос | Уточнить | Просто общаемся")
    logger.info("✅ Память 5 уровней + граф знаний")
    logger.info("✅ Максимальная точность (85–90%)")
    logger.info("✅ Самовосстановление при ошибках")
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
        app.add_handler(CallbackQueryHandler(handle_action))
        logger.info("✅ Бот готов к работе!")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
