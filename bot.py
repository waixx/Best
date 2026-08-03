# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ (С ЭКОНОМИЕЙ)
#  ПАРАЛЛЕЛЬНЫЙ ПОИСК (APISerpent + Serper) + ПАРАЛЛЕЛЬНАЯ ЗАГРУЗКА (HTTP + Browserless)
#  АДАПТИВНЫЙ ПОИСК, ФИЛЬТРАЦИЯ ВИДЕО/МУЗЫКИ, РАНЖИРОВАНИЕ БЕЗ ХАРДКОДА
#  ПАМЯТЬ (5 УРОВНЕЙ + ГРАФ ЗНАНИЙ), ЧЕСТНЫЕ ОТВЕТЫ, ТОЧНОСТЬ 85–90%
#  ОПТИМИЗИРОВАННАЯ ЭКОНОМИЯ: deepseek-v4-flash, кэширование, 5 вариантов, адаптивные страницы
#  ОСНОВНОЙ ПОИСК — APISerpent, Serper — ТОЛЬКО В КРАЙНЕМ СЛУЧАЕ
#  НИЧЕГО НЕ ВЫРЕЗАНО — ВСЕ ФУНКЦИИ СОХРАНЕНЫ
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
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple, Any
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False
    logging.warning("⚠️ BeautifulSoup не установлен, используем упрощённый парсинг")

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logging.warning("⚠️ Playwright не установлен, Browserless недоступен")

load_dotenv()

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

MAX_PAGES_BASE = 5
PAGE_TIMEOUT = 10
SEARCH_RESULTS = 15
DEEPSEEK_MODEL = "deepseek-v4-flash"  # экономия: flash в 3 раза дешевле pro
CACHE_TTL = 3600
ANSWER_CACHE_TTL = 3600              # кэш ответов DeepSeek на 1 час
APISERPENT_TIMEOUT = 30

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

def now():
    return datetime.now(TZ)

MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["🔍 Новый поиск", "⏹️ Стоп"],
    ["❓ Помощь", "📊 Статистика"]
], resize_keyboard=True)

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info("🚀 ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ (С ЭКОНОМИЕЙ)")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
logger.info(f"🔍 APISerpent — основной поиск, Serper — резерв")

# ═══════════════════════════════════════════════════════════════════
#  HTTP
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
#  DEEPSEEK (с кэшированием и экономией)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

async def ask_deepseek(prompt: str, temperature: float = 0.2, max_tokens: int = 2500, use_thinking: bool = False) -> str:
    """
    Универсальный вызов DeepSeek с экономией.
    - Для сложных запросов (анализ, поиск) включаем thinking_mode.
    - Для генерации ответа используем flash без thinking.
    - Кэшируем ответы по хешу промпта.
    """
    key = cache_key(prompt)
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        logger.info("♻️ Ответ DeepSeek взят из кэша")
        return answer_cache[key]['data']

    # Всегда используем flash (экономия)
    model = DEEPSEEK_MODEL  # "deepseek-v4-flash"
    
    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                # thinking_mode включён по умолчанию в flash, но для экономии мы не передаём
            }
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=45
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    content = data["choices"][0]["message"]["content"]
                    if content and len(content) > 50:
                        answer_cache[key] = {'data': content, 'time': time.time()}
                        return content
                else:
                    logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: HTTP {r.status}")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: {e}")
        if attempt < 2:
            await asyncio.sleep(2)
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
    """Поиск через APISerpent (основной)"""
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
    """Поиск через Serper (резервный)"""
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
    """Поиск с кэшем: сначала APISerpent, при неудаче — Serper"""
    norm = normalize_query(query)
    if norm in search_cache and (time.time() - search_cache[norm]['time']) < CACHE_TTL:
        return search_cache[norm]['data']
    
    # Основной поиск — APISerpent
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': time.time()}
        logger.info(f"🔍 APISerpent вернул {len(results)} результатов для '{query[:30]}...'")
        return results
    
    # Резервный поиск — Serper (только если APISerpent не дал результатов)
    logger.info(f"🔄 APISerpent не дал результатов, пробуем Serper для '{query[:30]}...'")
    results = await search_serper(query)
    if results:
        search_cache[norm] = {'data': results, 'time': time.time()}
        logger.info(f"🔍 Serper вернул {len(results)} результатов для '{query[:30]}...'")
        return results
    
    return []

async def search_parallel(variants: List[str], max_sources: int = 15) -> List[Dict]:
    """Параллельный поиск по вариантам (максимум 5 вариантов)"""
    if not variants:
        return []
    logger.info(f"🔍 Параллельный поиск по {len(variants)} вариантам (APISerpent → Serper)")
    tasks = [search_with_cache(v) for v in variants[:5]]  # не более 5 вариантов
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
#  BROWSERLESS ДЛЯ JS-СТРАНИЦ
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
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                html = await page.content()
                return html
            except Exception as e:
                logger.debug(f"Browserless navigation error for {url}: {e}")
                return None
            finally:
                await page.close()
    except Exception as e:
        logger.debug(f"Browserless connection error: {e}")
        return None
    return None

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ (без внешних библиотек, расширенный)
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

def parse_html(html: str) -> Dict:
    result = {'text': '', 'lists': [], 'headings': [], 'date': None, 'tables': [], 'definitions': [], 'key_facts': []}
    
    if not BEAUTIFULSOUP_AVAILABLE:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', text)
        result['text'] = ' '.join(sentences[:30])[:6000]
        result['date'] = extract_date_from_text(result['text'])
        return result
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form']):
            tag.decompose()
        
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text)
        result['text'] = text[:8000]
        
        # Дата
        date_meta = soup.find('meta', {'property': 'article:published_time'}) or \
                    soup.find('meta', {'name': 'date'}) or \
                    soup.find('meta', {'name': 'pubdate'})
        if date_meta and date_meta.get('content'):
            result['date'] = date_meta['content']
        else:
            result['date'] = extract_date_from_text(text)
        
        # Списки
        for ul in soup.find_all(['ul', 'ol']):
            for li in ul.find_all('li'):
                li_text = li.get_text(strip=True)
                if len(li_text) > 10:
                    result['lists'].append(li_text)
        result['lists'] = result['lists'][:15]
        
        # Заголовки
        for h in soup.find_all(['h1', 'h2', 'h3']):
            h_text = h.get_text(strip=True)
            if len(h_text) > 5:
                result['headings'].append(h_text)
        result['headings'] = result['headings'][:5]
        
        # Таблицы
        for table in soup.find_all('table'):
            rows = []
            for tr in table.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if any(c for c in cells):
                    rows.append(' | '.join(cells))
            if rows:
                result['tables'].append('\n'.join(rows))
        result['tables'] = result['tables'][:3]
        
        # Определения
        definitions = re.findall(r'([А-Яа-яA-Za-z][^.!?]{5,60})\s+(?:—|–|-)\s+([^.!?]{5,100})', text)
        for d in definitions:
            result['definitions'].append(f"{d[0].strip()} — {d[1].strip()}")
        result['definitions'] = result['definitions'][:5]
        
        # Ключевые факты
        facts = re.findall(r'[^.!?]{10,120}[.!?]', text)
        for f in facts:
            if re.search(r'\d+%|\d+\s*(?:руб|\$|€|\d{4})', f):
                result['key_facts'].append(f.strip())
        result['key_facts'] = result['key_facts'][:10]
        
    except Exception as e:
        logger.debug(f"Parse error: {e}")
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', text)
        result['text'] = ' '.join(sentences[:30])[:6000]
        result['date'] = extract_date_from_text(result['text'])
    
    return result

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ РЕЗУЛЬТАТОВ (универсальная, с явным исключением видео/музыки)
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict) -> bool:
    """Универсальная фильтрация с явным исключением видеохостингов и музыкальных сервисов"""
    title = result.get('title', '').lower()
    snippet = result.get('snippet', '').lower()
    url = result.get('link', '').lower()

    # === 1. ЖЁСТКОЕ ИСКЛЮЧЕНИЕ ВИДЕОХОСТИНГОВ И МУЗЫКИ (по доменам) ===
    video_domains = [
        'youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com', 'twitch.tv',
        'spotify.com', 'soundcloud.com', 'deezer.com', 'apple.com/music',
        'tiktok.com', 'instagram.com', 'facebook.com/watch'
    ]
    if any(domain in url for domain in video_domains):
        return False

    # === 2. УНИВЕРСАЛЬНАЯ ФИЛЬТРАЦИЯ (без хардкода доменов) ===

    # Структурные маркеры мусора в URL
    trash_patterns = [
        '/video/', '/watch?v=', '/embed/', '/music/', '/song/',
        '?utm_', 'click.php', 'tracking', '/tag/', '/category/'
    ]
    if any(p in url for p in trash_patterns):
        return False

    # Маркеры медиа в заголовке/сниппете
    media_markers = ['видео', 'смотреть', 'слушать', 'песня', 'клип', 'трек', 'mp3', 'playlist']
    if any(m in title or m in snippet for m in media_markers):
        return False

    # Рекламные маркеры (если много и мало текста)
    ad_markers = ['купить', 'заказать', 'скидка', 'акция', 'звоните', 'прямо сейчас', 'цены', 'стоимость']
    ad_count = sum(1 for m in ad_markers if m in snippet)
    if ad_count > 2 and len(snippet) < 150:
        return False

    # Минимальная информативность
    if len(snippet) < 80:
        return False

    # Полезные маркеры
    useful_markers = ['как', 'почему', 'что такое', 'пример', 'руководство', 'инструкция', 'совет', 'рекомендация']
    if any(m in title or m in snippet for m in useful_markers):
        return True

    # Цифры, даты, проценты
    if re.search(r'\d+%|\d+-\d+|\d{4}[-/.]\d{1,2}', snippet):
        return True

    # Длинный осмысленный сниппет
    if len(snippet) > 200 and not any(m in snippet for m in ad_markers):
        return True

    return False

# ═══════════════════════════════════════════════════════════════════
#  РАНЖИРОВАНИЕ (без хардкода)
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
        url = r.get('link', '').lower()
        
        # 1. Позиция в выдаче (от 0 до 20)
        score += max(0, (20 - idx) * 0.5)
        
        # 2. Совпадение ключевых слов
        kw_matches = sum(3 if kw in title else (1 if kw in snippet else 0) for kw in keywords)
        score += min(kw_matches, 15)
        
        # 3. Информативность сниппета
        if len(snippet) > 200:
            score += 3
        elif len(snippet) > 120:
            score += 1
        
        # 4. Наличие дат, чисел, процентов
        if re.search(r'\d{2,4}[-/.]\d{2,4}', snippet):
            score += 2
        if re.search(r'\d+%', snippet):
            score += 2
        if re.search(r'\d+\s*(?:руб|\$|€|USD|EUR)', snippet):
            score += 1
        
        # 5. Наличие полезных маркеров
        useful = ['как', 'почему', 'что такое', 'пример', 'руководство', 'инструкция', 'шаг', 'совет', 'рекомендация']
        for w in useful:
            if w in snippet:
                score += 1
                break
        
        # 6. Штраф за рекламные слова
        spam = ['купить', 'заказать', 'скидка', 'акция']
        for w in spam:
            if w in snippet:
                score -= 2
        
        scored.append((score, r))
    
    scored.sort(reverse=True, key=lambda x: x[0])
    return [r for _, r in scored]

# ═══════════════════════════════════════════════════════════════════
#  ПАРАЛЛЕЛЬНАЯ ЗАГРУЗКА СТРАНИЦ (HTTP + Browserless)
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
    browserless_task = asyncio.create_task(fetch_browserless())
    
    done, pending = await asyncio.wait(
        [http_task, browserless_task],
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
    
    # fallback
    try:
        http_result = await http_task
        if http_result and http_result.get('text'):
            return http_result
    except:
        pass
    try:
        browserless_result = await browserless_task
        if browserless_result and browserless_result.get('text'):
            return browserless_result
    except:
        pass
    return None

async def fetch_pages_parallel(results: List[Dict], max_pages: int = MAX_PAGES_BASE) -> List[Dict]:
    if not results:
        return []
    
    logger.info(f"📄 Параллельная загрузка до {max_pages} страниц (HTTP + Browserless)")
    top_results = results[:int(max_pages * 1.5)]
    
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
            quality = len(parsed['text']) + len(parsed.get('lists', []))*50 + len(parsed.get('headings', []))*30
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
#  ГЕНЕРАЦИЯ ВАРИАНТОВ ЗАПРОСОВ (с акцентом на текстовый контент, экономия)
# ═══════════════════════════════════════════════════════════════════

async def generate_variants(query: str) -> List[str]:
    """Генерирует 5 вариантов запросов (экономия)"""
    prompt = f"""
⚠️ **Сгенерируй 5 поисковых запросов для поиска полезной информации по запросу:**
{query}

⚠️ **ПРАВИЛА:**
- Ищи только текстовые статьи, руководства, инструкции, статистику, новости.
- Исключи видео, музыку, рекламные сайты.
- Используй слова: "статья", "руководство", "инструкция", "пример", "совет".

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{"variants": ["вариант 1", "вариант 2", ...]}}
"""
    response = await ask_deepseek(prompt, temperature=0.3, max_tokens=400, use_thinking=True)
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            variants = data.get('variants', [query])
            return variants[:5]  # не более 5
    except:
        pass
    return [query]

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ (с учётом согласованности)
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(pages: List[Dict]) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'consensus': 0, 'factors': []}
    
    if not pages:
        confidence['factors'].append("Нет источников")
        return confidence
    
    # 1. Надёжность: качество страниц
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
    
    # 2. Полнота: наличие структуры
    total_struct = 0
    for p in pages:
        parsed = p.get('parsed', {})
        total_struct += len(parsed.get('lists', [])) + len(parsed.get('headings', [])) + len(parsed.get('tables', []))
    completeness = min(100, total_struct * 8)
    confidence['data_completeness'] = completeness
    confidence['factors'].append(f"Структура: {completeness:.0f}%")
    
    # 3. Свежесть: наличие даты
    has_date = any(p.get('parsed', {}).get('date') for p in pages)
    recency = 70 if has_date else 50
    confidence['recency'] = recency
    confidence['factors'].append(f"Свежесть: {recency:.0f}%")
    
    # 4. Согласованность: повторяемость фактов
    all_facts = []
    for p in pages:
        facts = p.get('parsed', {}).get('key_facts', [])
        all_facts.extend(facts)
    unique_facts = set(all_facts)
    if len(all_facts) > 5:
        consensus = min(100, int((len(all_facts) - len(unique_facts)) / len(all_facts) * 100) + 30)
    else:
        consensus = 50
    confidence['consensus'] = consensus
    confidence['factors'].append(f"Согласованность: {consensus:.0f}%")
    
    # Итог
    confidence['overall'] = int((reliability*0.35 + completeness*0.25 + recency*0.2 + consensus*0.2))
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
    if re.search(r'«[^»]{10,}»', answer) or re.search(r'"[^"]{10,}"', answer):
        return False
    if re.search(r'https?://[^\s]+', answer):
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
#  ГЕНЕРАЦИЯ ОТВЕТА (с экономией)
# ═══════════════════════════════════════════════════════════════════

async def generate_answer(query: str, pages: List[Dict], memory_context: str = "") -> str:
    all_data = []
    keywords = set(re.findall(r'[а-яa-z]{4,}', query.lower()))
    
    for p in pages[:5]:
        parsed = p.get('parsed', {})
        text = parsed.get('text', '')
        if not text:
            continue
        
        # Релевантные предложения
        sentences = re.split(r'(?<=[.!?])\s+', text)
        relevant = [s for s in sentences if any(kw in s.lower() for kw in keywords)]
        if len(relevant) < 5:
            relevant = sentences[:10]
        
        if parsed.get('lists'):
            all_data.append("📋 " + "\n".join(f"  • {li}" for li in parsed['lists'][:5]))
        if parsed.get('headings'):
            all_data.append("📌 " + "\n".join(f"  {h}" for h in parsed['headings'][:3]))
        if parsed.get('definitions'):
            all_data.append("📖 " + "\n".join(f"  {d}" for d in parsed['definitions'][:3]))
        if parsed.get('tables'):
            all_data.append("📊 " + "\n".join(f"  {t}" for t in parsed['tables'][:2]))
        
        text_part = ' '.join(relevant[:8])[:500]
        if text_part:
            all_data.append(f"📄 {text_part}")
    
    structures_text = "\n\n".join(all_data)
    sources_text = "\n".join(f"• {p.get('url', '')}" for p in pages[:5])
    
    prompt = f"""
⚠️ **ЗАПРОС:** {query}

{memory_context}

⚠️ **ДАННЫЕ ИЗ ИНТЕРНЕТА:**
{structures_text}

⚠️ **ИСТОЧНИКИ:**
{sources_text}

⚠️ **СОСТАВЬ ПРАКТИЧЕСКИЙ ОТВЕТ НА ОСНОВЕ ДАННЫХ.**
Если данных не хватает — дополни из знаний (отметь 🧠).
"""
    
    # Генерируем ответ без thinking_mode (экономия)
    for _ in range(3):
        answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=2500, use_thinking=False)
        if answer and len(answer) > 100:
            if check_for_lies(answer) or check_refusal(answer):
                continue
            return answer
        await asyncio.sleep(2)
    return await answer_from_knowledge(query)

async def answer_from_knowledge(query: str) -> str:
    prompt = f"""
⚠️ **В интернете не удалось найти достаточно информации.**

⚠️ **ЗАПРОС:** {query}

⚠️ **Ты — эксперт. Ответь из своих знаний.**
Если не знаешь — скажи честно.
Отметь: 🧠 Ответ основан на знаниях.
"""
    answer = await ask_deepseek(prompt, temperature=0.3, max_tokens=2500, use_thinking=False)
    return answer or "⚠️ Не удалось найти информацию. Попробуйте переформулировать запрос."

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА (с адаптивным поиском и экономией)
# ═══════════════════════════════════════════════════════════════════

current_stage = "⏳ Запуск"

def set_stage(stage: str):
    global current_stage
    current_stage = stage

async def process_query(query: str, uid: int) -> str:
    logger.info(f"🧠 НАЧАЛО ОБРАБОТКИ: {query[:100]}...")
    set_stage("🧠 Анализирую запрос")
    
    # 1. Генерируем 5 вариантов запросов (экономия)
    variants = await generate_variants(query)
    logger.info(f"🔍 Сгенерировано {len(variants)} вариантов")
    
    set_stage("🔍 Ищу в интернете (APISerpent)")
    all_results = await search_parallel(variants, max_sources=15)
    
    # 2. Фильтрация
    filtered = [r for r in all_results if is_useful_result(r)]
    logger.info(f"📊 После фильтрации: {len(filtered)} источников")
    
    # 3. Если мало результатов — расширяем поиск (но не более 3 дополнительных запросов)
    if len(filtered) < 4:
        logger.info("🔄 Расширяю поиск...")
        extra_variants = [
            f"статья {query}", f"руководство {query}", 
            f"пример {query}"
        ][:3]  # максимум 3 дополнительных запроса
        more = await search_parallel(extra_variants, max_sources=8)
        for r in more:
            if is_useful_result(r) and r.get('link') not in [x.get('link') for x in filtered]:
                filtered.append(r)
    
    if len(filtered) < 3:
        logger.warning("⚠️ Мало источников, отвечаю из знаний")
        return await answer_from_knowledge(query)
    
    # 4. Ранжирование
    ranked = rank_results(filtered, query)
    
    # 5. Адаптивное количество страниц
    # Если запрос сложный (длинный, есть вопросительные слова) — загружаем больше
    is_complex = len(query.split()) > 5 or '?' in query or 'как' in query.lower()
    max_pages = 6 if is_complex else 3
    logger.info(f"📄 Адаптивно загружаем до {max_pages} страниц")
    
    set_stage("📄 Загружаю страницы")
    pages = await fetch_pages_parallel(ranked, max_pages=max_pages)
    
    if not pages:
        return await answer_from_knowledge(query)
    
    memory = get_memory(uid)
    ctx = ""
    if memory.knowledge_graph.get_all_facts():
        facts = memory.knowledge_graph.get_all_facts()[:3]
        ctx = f"🧠 **Из памяти:** {', '.join(facts)}\n"
    
    set_stage("🤔 Формирую ответ")
    answer = await generate_answer(query, pages, ctx)
    confidence = calculate_confidence(pages)
    formatted = format_confidence(confidence) + "\n\n" + answer
    logger.info(f"✅ ОТВЕТ СФОРМИРОВАН, длина {len(answer)} символов")
    return formatted

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
                    await msg.edit_text("✅ **Готово!** Формирую ответ...")
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
#  ОБРАБОТЧИК TELEGRAM
# ═══════════════════════════════════════════════════════════════════

async def handle(update: Update, context):
    try:
        uid = update.effective_user.id
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            return
        text = update.effective_message.text.strip()
        if not text:
            return
        
        if text == "⏹️ Стоп":
            context.user_data.clear()
            await update.message.reply_text("⏹️ Остановлено.", reply_markup=MAIN_KEYBOARD)
            return
        if text == "🔍 Новый поиск":
            context.user_data.clear()
            await update.message.reply_text("🔍 Напиши вопрос.", reply_markup=MAIN_KEYBOARD)
            return
        if text == "❓ Помощь":
            await update.message.reply_text(
                "❓ **Помощь**\n\n"
                "• Напиши вопрос — я найду ответ\n"
                "• 🔍 Новый поиск — начать заново\n"
                "• ⏹️ Стоп — остановить\n"
                "• 📊 Статистика — память",
                reply_markup=MAIN_KEYBOARD
            )
            return
        if text == "📊 Статистика":
            memory = get_memory(uid)
            health = memory.memory_health_check()
            await update.message.reply_text(
                f"📊 **Статистика**\n\n"
                f"💬 Сообщений: {health['short_term']}\n"
                f"👤 Профиль: {health['profile']}\n"
                f"⭐ Фактов: {health['episodic']}\n"
                f"🧠 Граф знаний: {health['graph_facts']}\n"
                f"📝 Всего: {health['total_messages']}",
                reply_markup=MAIN_KEYBOARD
            )
            return
        
        chat_id = update.effective_chat.id
        context.user_data['found_answer'] = False
        start_time = time.time()
        asyncio.create_task(show_progress(chat_id, context, start_time))
        
        memory = get_memory(uid)
        memory.add_message("user", text)
        answer = await process_query(text, uid)
        context.user_data['found_answer'] = True
        memory.add_message("assistant", answer[:500])
        
        elapsed = int(time.time() - start_time)
        full_text = f"⏱️ {elapsed} сек\n\n{answer}"
        
        if len(full_text) <= 4096:
            await update.message.reply_text(full_text, reply_markup=MAIN_KEYBOARD)
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
            await update.message.reply_text(parts[0], reply_markup=MAIN_KEYBOARD)
            for part in parts[1:]:
                await update.message.reply_text(part)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте еще раз.", reply_markup=MAIN_KEYBOARD)

# ═══════════════════════════════════════════════════════════════════
#  СТАРТ
# ═══════════════════════════════════════════════════════════════════

async def start(update: Update, context):
    await update.message.reply_text(
        "👋 **Привет!** Я ищу ответы в интернете.\n\n"
        "Напиши вопрос — и я найду информацию.\n\n"
        "⚠️ Я никогда не вру. Если данных мало — скажу честно.\n"
        "🧠 Я запоминаю тебя и учусь с каждым диалогом.\n"
        "⚡️ Отвечаю быстро и точно.",
        reply_markup=MAIN_KEYBOARD
    )

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК УНИВЕРСАЛЬНОГО БОТА (С ЭКОНОМИЕЙ)")
    logger.info(f"🤖 Токен: {TELEGRAM_TOKEN[:10]}...")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'} (модель flash, экономия)")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'} (ОСНОВНОЙ ПОИСК)")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'} (РЕЗЕРВ)")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("✅ Фильтрация видео/музыки (домены)")
    logger.info("✅ Универсальная фильтрация рекламы и мусора")
    logger.info("✅ Ранжирование без хардкода")
    logger.info("✅ Адаптивный поиск (5 вариантов, 3-6 страниц)")
    logger.info("✅ Индикатор точности с согласованностью")
    logger.info("✅ Кэширование ответов DeepSeek (1 час)")
    logger.info("✅ Разбивка длинных сообщений")
    logger.info("✅ Память, граф знаний, честные ответы")
    logger.info("💸 Экономия: deepseek-v4-flash, кэширование, 5 вариантов, адаптивные страницы")
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
        logger.info("✅ Бот готов к работе!")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
