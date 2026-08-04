# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ИСПРАВЛЕННАЯ ЛОГИКА РЕЖИМОВ
#  ТЕКСТ → КНОПКИ → ВЫБОР РЕЖИМА → ДЕЙСТВИЕ
#  ПАМЯТЬ + ГРАФ ЗНАНИЙ + ИТЕРАТИВНЫЙ ПОИСК
#  НИЧЕГО НЕ ВЫРЕЗАНО
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

PAGE_TIMEOUT = 20
SEARCH_RESULTS = 40
DEEPSEEK_MODEL = "deepseek-v4-flash"
CACHE_TTL = 3600
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 15
MAX_TOKENS_OUTPUT = 6000
MAX_TOKENS_VARIANTS = 500
MAX_ITERATIONS = 5
TARGET_CONFIDENCE = 90
MAX_PAGES_PER_ITERATION = 15

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

def now():
    return datetime.now(TZ)

# ═══════════════════════════════════════════════════════════════════
#  КНОПКИ
# ═══════════════════════════════════════════════════════════════════

ACTION_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔍 Поиск", callback_data="action_search"),
        InlineKeyboardButton("📝 Уточнить", callback_data="action_clarify"),
    ],
    [
        InlineKeyboardButton("💬 Беседа", callback_data="action_chat"),
    ]
])

# Кнопка выхода из беседы (всегда показывается в режиме беседы)
EXIT_CHAT_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔍 Выйти в поиск", callback_data="action_exit_chat")]
])

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
#  DEEPSEEK (с кэшированием)
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
#  ПАМЯТЬ (5 УРОВНЕЙ + ГРАФ ЗНАНИЙ) — ПОЛНОСТЬЮ
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
    
    def get_full_context(self, limit=15) -> str:
        """Возвращает полный контекст для уточнений"""
        context_parts = []
        
        if self.profile:
            profile_text = f"👤 Пользователь: {', '.join([f'{k}: {v}' for k, v in self.profile.items()])}"
            context_parts.append(profile_text)
        
        if self.short_term:
            recent = self.short_term[-10:]
            for msg in recent:
                role = "Пользователь" if msg.get('role') == 'user' else "Ассистент"
                context_parts.append(f"{role}: {msg.get('content', '')[:200]}")
        
        facts = self.knowledge_graph.get_all_facts()
        if facts:
            context_parts.append(f"🧠 Знания: {', '.join(facts[:5])}")
        
        if self.episodic:
            important = sorted(self.episodic, key=lambda x: x.get('priority', 0), reverse=True)[:3]
            for mem in important:
                context_parts.append(f"📌 Важно: {mem.get('content', '')}")
        
        return "\n".join(context_parts)
    
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
#  ТАЙМЕР
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        message = await context.bot.send_message(
            chat_id,
            "🌐 Ищу информацию...\n\n⏱️ 0 сек"
        )
        elapsed = 0
        while elapsed < 180:
            await asyncio.sleep(2)
            if context.user_data.get('found_answer'):
                try:
                    await message.edit_text("✅ Информация найдена! Формирую ответ...")
                except Exception:
                    pass
                break
            elapsed = int(time.time() - start_time)
            try:
                await message.edit_text(f"🌐 Ищу информацию...\n\n⏱️ {elapsed} сек")
            except Exception:
                message = await context.bot.send_message(chat_id, f"🌐 Ищу информацию... ⏱️ {elapsed} сек")
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")
    return message

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
            params={"q": query, "engine": "google", "num": SEARCH_RESULTS},
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                results = []
                for key in ['organic_results', 'organic']:
                    items = data.get(key, [])
                    if items:
                        for x in items:
                            results.append({
                                "title": x.get("title", ""),
                                "snippet": x.get("snippet", ""),
                                "link": x.get("link", ""),
                            })
                        break
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

async def search_parallel(variants: List[str]) -> List[Dict]:
    if not variants:
        return []
    tasks = [search_with_cache(v) for v in variants[:5]]
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
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)
                html = await page.content()
                return html
            except Exception:
                return None
            finally:
                await page.close()
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════
#  УНИВЕРСАЛЬНЫЙ ПАРСИНГ
# ═══════════════════════════════════════════════════════════════════

def is_good_text(text: str) -> bool:
    if len(text) < 20:
        return False
    if re.match(r'^[\d\s.,;:!?()\-]+$', text):
        return False
    garbage = re.findall(r'[\.\/\\\:\#\@\$\%\^\&\*\(\)\=\+\{\}\[\]]', text)
    if len(garbage) / max(1, len(text)) > 0.15:
        return False
    return True

def extract_items_from_text(text: str, query: str) -> Dict:
    result = {'title': None, 'description': None, 'year': None, 'rating': None, 'price': None}
    
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    lines = [l for l in lines if is_good_text(l)]
    if lines:
        result['title'] = max(lines, key=len)[:150]
        if len(result['title']) < 10 and lines:
            result['title'] = lines[0][:150]
    
    year_match = re.search(r'\b(19[0-9]{2}|20[0-9]{2})\b', text)
    if year_match:
        result['year'] = year_match.group(1)
    
    rating_match = re.search(r'\b(\d+\.\d{1,2})\b', text)
    if rating_match:
        try:
            val = float(rating_match.group(1))
            if 0 <= val <= 10:
                result['rating'] = rating_match.group(1)
        except:
            pass
    
    price_match = re.search(r'(\d+[\s,]?\d*)\s*(?:руб|\$|€|₽)', text, re.I)
    if price_match:
        result['price'] = price_match.group(0)
    
    if result['title'] and result['title'] in text:
        parts = text.split(result['title'], 1)
        if len(parts) > 1:
            sentences = re.split(r'[.!?]', parts[1])
            for s in sentences:
                s = s.strip()
                if is_good_text(s) and len(s) > 20:
                    result['description'] = s[:300]
                    break
    
    return result

def parse_page(html: str, query: str) -> Dict:
    result = {
        'text': '',
        'lists': [],
        'headings': [],
        'items': [],
        'date': None,
        'definitions': [],
        'key_facts': []
    }
    
    if not BEAUTIFULSOUP_AVAILABLE:
        return result
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'noscript']):
            tag.decompose()
        
        text = soup.get_text(separator=' ')
        text = re.sub(r'\s+', ' ', text).strip()
        result['text'] = text[:8000]
        
        for tag in soup.find_all(['h1', 'h2', 'h3']):
            h = tag.get_text().strip()
            if h:
                result['headings'].append(h[:200])
        
        for tag in soup.find_all(['ul', 'ol']):
            items = []
            for li in tag.find_all('li'):
                li_text = li.get_text().strip()
                if li_text and len(li_text) > 5:
                    items.append(li_text[:200])
            if items:
                result['lists'].append(items)
        
        definitions = re.findall(r'([А-Яа-яA-Za-z][^.!?]{5,60})\s+(?:—|–|-|это|является)\s+([^.!?]{5,100})', text, re.I)
        for d in definitions:
            result['definitions'].append(f"{d[0].strip()} — {d[1].strip()}")
        result['definitions'] = result['definitions'][:8]
        
        key_phrases = re.findall(
            r'(?:совет|рекомендация|шаг|этап|важно|главное|ключевой|основной)[^.!?]{10,80}[.!?]',
            text, re.IGNORECASE
        )
        result['key_facts'] = key_phrases[:10]
        
        all_blocks = []
        for tag in soup.find_all(['div', 'li', 'article', 'section', 'p', 'span']):
            block_text = tag.get_text(separator=' ').strip()
            if len(block_text) > 30:
                all_blocks.append(block_text)
        
        items = []
        for block in all_blocks[:40]:
            if is_good_text(block):
                extracted = extract_items_from_text(block, query)
                if extracted['title'] and len(extracted['title']) > 3:
                    items.append({
                        'title': extracted['title'][:200],
                        'description': extracted.get('description', '')[:500],
                        'year': extracted.get('year'),
                        'rating': extracted.get('rating'),
                        'price': extracted.get('price'),
                    })
        
        seen = set()
        unique_items = []
        for item in items:
            key = item['title'].lower().strip()
            if key not in seen and len(key) > 3:
                seen.add(key)
                unique_items.append(item)
        
        result['items'] = unique_items[:50]
        
        date_elem = soup.find('time')
        if date_elem and date_elem.get('datetime'):
            result['date'] = date_elem['datetime']
        
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ Ошибка парсинга: {e}")
    
    return result

async def fetch_page(url: str, query: str) -> Dict:
    html = await fetch_with_browserless(url)
    if html:
        return parse_page(html, query)
    try:
        session = await get_session()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        async with session.get(url, headers=headers, timeout=PAGE_TIMEOUT) as r:
            if r.status == 200:
                html = await r.text()
                return parse_page(html, query)
    except Exception as e:
        logger.warning(f"⚠️ HTTP ошибка: {e}")
    return {'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None, 'definitions': [], 'key_facts': []}

async def fetch_pages(links: List[str], query: str) -> List[Dict]:
    if not links:
        return []
    tasks = [fetch_page(link, query) for link in links[:MAX_PAGES_PER_ITERATION]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r.get('text') or r.get('items')]

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════

async def generate_variants(query: str) -> List[str]:
    variants = [query]
    try:
        prompt = f"""
Сгенерируй 5 разных вариантов поискового запроса для вопроса:
{query}

Ответь ТОЛЬКО списком, каждый вариант с новой строки, без нумерации.
"""
        result = await ask_deepseek(prompt, temperature=0.4, max_tokens=MAX_TOKENS_VARIANTS)
        if result:
            for line in result.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    clean = re.sub(r'^[\d\s.)-]+', '', line).strip()
                    if clean and len(clean) > 5:
                        variants.append(clean)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка генерации: {e}")
    return list(dict.fromkeys(variants))[:5]

async def generate_refined_variants(query: str, items: List[Dict]) -> List[str]:
    variants = [query]
    keywords = set()
    for item in items[:10]:
        title = item.get('title', '')
        if title:
            words = title.split()[:2]
            keywords.update(words)
    if keywords:
        keyword_str = ' '.join(list(keywords)[:3])
        variants.append(f"{keyword_str} {query}")
    return list(dict.fromkeys(variants))[:5]

# ═══════════════════════════════════════════════════════════════════
#  РАСЧЁТ УВЕРЕННОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(items: List[Dict]) -> float:
    if not items:
        return 0.0
    
    unique_count = len(set([item.get('title', '') for item in items]))
    unique_score = min(100, unique_count * 5)
    
    rating_count = sum(1 for item in items if item.get('rating'))
    rating_score = min(100, rating_count * 10)
    
    year_count = sum(1 for item in items if item.get('year'))
    year_score = min(100, year_count * 5)
    
    desc_count = sum(1 for item in items if item.get('description') and len(item['description']) > 20)
    desc_score = min(100, desc_count * 5)
    
    total_score = (
        unique_score * 0.35 +
        rating_score * 0.25 +
        year_score * 0.20 +
        desc_score * 0.20
    )
    
    return min(100, total_score)

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ ВИДЕО/МУЗЫКИ
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict) -> bool:
    url = result.get('link', '').lower()
    title = result.get('title', '').lower()
    
    video_domains = [
        'youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com', 'twitch.tv',
        'spotify.com', 'soundcloud.com', 'deezer.com', 'apple.com/music',
        'tiktok.com', 'instagram.com', 'facebook.com/watch'
    ]
    if any(domain in url for domain in video_domains):
        return False
    
    media_markers = ['видео', 'смотреть', 'слушать', 'песня', 'клип', 'трек', 'mp3']
    if any(m in title for m in media_markers):
        return False
    
    return True

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ
# ═══════════════════════════════════════════════════════════════════

def format_confidence(confidence: float, items_count: int) -> str:
    overall = int(confidence)
    icon = "🟢" if overall >= 80 else "🟡" if overall >= 60 else "🟠" if overall >= 40 else "🔴"
    level = "Высокая" if overall >= 80 else "Средняя" if overall >= 60 else "Низкая" if overall >= 40 else "Очень низкая"
    
    uniqueness = min(100, items_count * 5)
    
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **ТОЧНОСТЬ: {overall}%** {icon} ({level})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **ДЕТАЛИ:**
   • Уникальность: {min(100, uniqueness)}%
   • Найдено элементов: {items_count}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(query: str, uid: int, context_prompt: str = "") -> Tuple[str, List[Dict], float]:
    logger.info(f"🛡️ ЗАПРОС: {query[:50]}")
    
    all_items = []
    all_results = []
    confidence = 0.0
    iteration = 0
    variants = await generate_variants(query)
    search_variants = variants[:3]
    
    while confidence < TARGET_CONFIDENCE and iteration < MAX_ITERATIONS:
        iteration += 1
        logger.info(f"🔍 Итерация {iteration}")
        
        results = await search_parallel(search_variants)
        if not results:
            break
        
        results = [r for r in results if is_useful_result(r)]
        
        all_results.extend(results)
        links = [r.get('link', '') for r in results if r.get('link')]
        pages = await fetch_pages(links, query)
        
        items = []
        for page in pages:
            if page.get('items'):
                items.extend(page['items'])
        
        all_items.extend(items)
        confidence = calculate_confidence(all_items)
        logger.info(f"📊 Уверенность: {confidence:.1f}% ({len(all_items)} элементов)")
        
        if confidence < TARGET_CONFIDENCE:
            new_variants = await generate_refined_variants(query, all_items)
            search_variants = new_variants[:3]
    
    if not all_items:
        memory = get_memory(uid)
        context = memory.get_context(limit=5)
        context_text = '\n'.join([m.get('content', '') for m in context])
        
        fallback_prompt = f"""
⚠️ **В ИНТЕРНЕТЕ НИЧЕГО НЕ НАЙДЕНО**

Вопрос: {query}
Контекст: {context_text}

Ответь честно: "В интернете ничего не найдено."
"""
        answer = await ask_deepseek(fallback_prompt, temperature=0.3)
        return answer, [], 0.0
    
    sorted_items = sorted(
        all_items,
        key=lambda x: (
            0 if x.get('rating') else 1,
            0 if x.get('year') else 2
        )
    )[:30]
    
    items_text = ""
    for idx, item in enumerate(sorted_items[:30], 1):
        year = f" ({item.get('year')})" if item.get('year') else ""
        rating = f" ★ {item.get('rating')}" if item.get('rating') else ""
        price = f" {item.get('price')}" if item.get('price') else ""
        desc = f" — {item.get('description')[:100]}" if item.get('description') else ""
        items_text += f"{idx}. {item.get('title')}{year}{rating}{price}{desc}\n"
    
    answer_prompt = f"""
⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА!**

Найдено {len(sorted_items)} элементов. Уверенность: {confidence:.1f}%

{items_text}

{context_prompt}

⚠️ **ПРАВИЛА:**
1. Используй ТОЛЬКО данные из интернета
2. Если данных мало — добавь блок "🧠 Дополнено из знаний"
3. НЕЛЬЗЯ смешивать знания с интернетом
4. НЕЛЬЗЯ говорить "нет доступа"
5. Структурируй ответ

⚠️ **ФОРМАТ:**
📊 **Из интернета:** (перечисли найденное)
🧠 **Дополнено из знаний:** (если нужно)
✅ **Вывод:**

Вопрос: {query}
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.3, max_tokens=MAX_TOKENS_OUTPUT)
    
    return answer, all_results, confidence

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ
# ═══════════════════════════════════════════════════════════════════

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    user_id = update.effective_user.id
    memory = get_memory(user_id)

    # ════════════════════════════════════════════════════
    # 🔍 ПОИСК — берём последний текст, после которого появились кнопки
    # ════════════════════════════════════════════════════
    if action == "action_search":
        pending_text = context.user_data.get('pending_text', '')
        if not pending_text:
            await query.edit_message_text(
                "⚠️ Сначала напишите вопрос в чат.",
                reply_markup=ACTION_BUTTONS
            )
            return

        # Убираем ожидание, чтобы новые сообщения не путали
        context.user_data['awaiting_input'] = False

        await query.edit_message_text("🔍 Начинаю поиск...")

        start_time = time.time()
        context.user_data['found_answer'] = False

        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )

        # Получаем контекст из памяти
        context_text = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(pending_text, user_id, context_text)

        context.user_data['found_answer'] = True
        await progress_task

        elapsed = int(time.time() - start_time)
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = pending_text
        context.user_data['last_answer'] = answer
        context.user_data['pending_text'] = ''  # Очищаем после обработки

        confidence_text = format_confidence(confidence, len(sources))

        await update.effective_message.reply_text(
            f"⏱️ {elapsed} сек\n\n{confidence_text}\n\n{answer}",
            reply_markup=ACTION_BUTTONS
        )

    # ════════════════════════════════════════════════════
    # 📝 УТОЧНИТЬ — используем последний запрос + контекст
    # ════════════════════════════════════════════════════
    elif action == "action_clarify":
        pending_text = context.user_data.get('pending_text', '')
        last_query = context.user_data.get('last_query', '')

        if not pending_text:
            await query.edit_message_text(
                "⚠️ Сначала напишите уточнение в чат.",
                reply_markup=ACTION_BUTTONS
            )
            return

        if not last_query:
            await query.edit_message_text(
                "⚠️ Нет активного запроса для уточнения.\nСначала выполните поиск.",
                reply_markup=ACTION_BUTTONS
            )
            return

        context.user_data['awaiting_input'] = False

        await query.edit_message_text(
            f"📝 **Обрабатываю уточнение...**\n\n"
            f"Предыдущий запрос: {last_query[:200]}\n"
            f"Уточнение: {pending_text}"
        )

        start_time = time.time()
        context.user_data['found_answer'] = False

        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )

        # Полный контекст диалога
        full_context = memory.get_full_context()
        clarified_query = f"{last_query} (уточнение: {pending_text})"

        answer, sources, confidence = await search_and_answer(clarified_query, user_id, full_context)

        context.user_data['found_answer'] = True
        await progress_task

        elapsed = int(time.time() - start_time)
        memory.add_message('user', f"Уточнение: {pending_text}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = clarified_query
        context.user_data['last_answer'] = answer
        context.user_data['pending_text'] = ''  # Очищаем

        confidence_text = format_confidence(confidence, len(sources))

        await update.effective_message.reply_text(
            f"⏱️ {elapsed} сек\n\n{confidence_text}\n\n{answer}",
            reply_markup=ACTION_BUTTONS
        )

    # ════════════════════════════════════════════════════
    # 💬 БЕСЕДА — режим без интернета
    # ════════════════════════════════════════════════════
    elif action == "action_chat":
        pending_text = context.user_data.get('pending_text', '')
        if not pending_text:
            await query.edit_message_text(
                "⚠️ Сначала напишите сообщение в чат.",
                reply_markup=ACTION_BUTTONS
            )
            return

        context.user_data['mode'] = 'chat'
        context.user_data['awaiting_input'] = False
        context.user_data['pending_text'] = ''  # Очищаем

        # Отвечаем на последний текст из знаний
        full_context = memory.get_full_context()

        chat_prompt = f"""
💬 **Ты — дружелюбный, умный и креативный собеседник.**

Отвечай естественно, тепло, с юмором, но по делу.
Используй ТОЛЬКО свои знания и контекст диалога.
НЕ используй интернет.

Контекст диалога:
{full_context}

Сообщение пользователя: {pending_text}

Ответь кратко, но содержательно.
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT)
        if not answer:
            answer = "😊 Я здесь! Чем могу помочь?"

        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)

        await query.edit_message_text(
            f"💬 **Режим беседы (без интернета)**\n\n{answer}",
            reply_markup=EXIT_CHAT_BUTTON
        )

    # ════════════════════════════════════════════════════
    # 🔙 ВЫХОД ИЗ БЕСЕДЫ
    # ════════════════════════════════════════════════════
    elif action == "action_exit_chat":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False

        await query.edit_message_text(
            "🔍 **Выход из режима беседы**\n\n"
            "Теперь я снова ищу информацию в интернете.\n"
            "Напишите новый вопрос, и я предложу режимы.",
            reply_markup=ACTION_BUTTONS
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        return

    user_message = update.effective_message.text
    if not user_message:
        return

    # ══════════════════════════════════════════════════════
    # Если бот в режиме беседы — отвечаем без интернета
    # ══════════════════════════════════════════════════════
    if context.user_data.get('mode') == 'chat':
        memory = get_memory(user_id)
        full_context = memory.get_full_context()

        chat_prompt = f"""
💬 **Ты — дружелюбный, умный и креативный собеседник.**

Отвечай естественно, тепло, с юмором, но по делу.
Используй ТОЛЬКО свои знания и контекст диалога.
НЕ используй интернет.

Контекст диалога:
{full_context}

Сообщение пользователя: {user_message}

Ответь кратко, но содержательно.
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT)
        if not answer:
            answer = "😊 Я здесь! Чем могу помочь?"

        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)

        await update.effective_message.reply_text(
            f"💬 {answer}",
            reply_markup=EXIT_CHAT_BUTTON
        )
        return

    # ══════════════════════════════════════════════════════
    # Обычный режим: сохраняем текст и показываем кнопки
    # ══════════════════════════════════════════════════════
    context.user_data['pending_text'] = user_message
    context.user_data['awaiting_input'] = True

    await update.effective_message.reply_text(
        f"📝 **Запрос принят:**\n\n"
        f"_{user_message[:300]}_\n\n"
        f"Выберите режим работы:",
        reply_markup=ACTION_BUTTONS
    )


# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data['mode'] = 'search'
    await update.effective_message.reply_text(
        "👋 **Привет! Я поисковый ассистент.**\n\n"
        "🔍 Ищу информацию в интернете\n"
        "📊 Показываю источники и уверенность\n"
        "⚠️ **НИКОГДА НЕ ВРУ**\n"
        "🧠 Запоминаю тебя и учусь\n\n"
        "**Как работает:**\n"
        "1️⃣ Напиши вопрос в чат\n"
        "2️⃣ Выбери действие:\n"
        "   • 🔍 Поиск — найти информацию в интернете\n"
        "   • 📝 Уточнить — уточнить предыдущий запрос\n"
        "   • 💬 Беседа — общаться без интернета\n\n"
        "Попробуй!",
        reply_markup=ACTION_BUTTONS
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    health = memory.memory_health_check()
    await update.effective_message.reply_text(
        f"📊 **Статистика**\n\n"
        f"💬 Сообщений: {health['short_term']}\n"
        f"👤 Профиль: {health['profile']} полей\n"
        f"⭐ Фактов в памяти: {health['episodic']}\n"
        f"🧠 Граф знаний: {health['graph_facts']} фактов\n"
        f"📝 Всего сообщений: {health['total_messages']}",
        reply_markup=ACTION_BUTTONS
    )

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in _memory_cache:
        del _memory_cache[user_id]
    for path in [memory_path(user_id), profile_path(user_id), episodic_path(user_id), 
                 learning_path(user_id), counter_path(user_id), graph_path(user_id)]:
        try:
            os.remove(path)
        except:
            pass
    context.user_data.clear()
    await update.effective_message.reply_text(
        "🧹 **Всё забыто!**\n\n"
        "Память очищена. Начинаем с чистого листа.",
        reply_markup=ACTION_BUTTONS
    )


# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК ИСПРАВЛЕННОЙ ВЕРСИИ")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("✅ Память 5 уровней + граф знаний")
    logger.info("✅ Итеративный поиск")
    logger.info("✅ Фильтрация видео/музыки")
    logger.info("✅ Индикатор точности")
    logger.info("✅ Режимы: ТЕКСТ → КНОПКИ → ПОИСК/УТОЧНЕНИЕ/БЕСЕДА")
    logger.info("✅ Беседа с кнопкой выхода")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("forget", cmd_forget))

    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Бот готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
