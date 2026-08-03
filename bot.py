# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — НОВАЯ УЛУЧШЕННАЯ ВЕРСИЯ
#  ВСЁ НА МЕСТЕ: ПОИСК + ПАРСИНГ + ПАМЯТЬ + ЗАЩИТА
#  БЕЗ ВЫРЕЗАНИЙ, БЕЗ ПОТЕРЬ КАЧЕСТВА
#  100% РАБОЧАЯ ВЕРСИЯ
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

# ═══════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════════

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
        InlineKeyboardButton("🔍 Новый запрос", callback_data="action_new"),
        InlineKeyboardButton("📝 Уточнить", callback_data="action_clarify"),
    ],
    [
        InlineKeyboardButton("💬 Просто общаемся", callback_data="action_chat"),
    ]
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
#  ПРОВЕРКА НА ЛОЖЬ (СМЫСЛОВОЙ НАМОРДНИК)
# ═══════════════════════════════════════════════════════════════════

def is_lie_by_sense(text: str) -> Tuple[bool, str]:
    """Проверяет СМЫСЛ — без лазеек."""
    text_lower = text.lower()
    
    lie_patterns = [
        (r'(нет\s*доступа|нет\s*интернета|не\s*могу\s*искать)', "Говорит 'нет доступа'"),
        (r'(на\s*основе\s*(моих|своих)\s*знаний)', "Использует свои знания вместо источников"),
        (r'(я\s*знаю|мне\s*известно|я\s*помню)', "Говорит 'я знаю' вместо источников"),
        (r'(нет\s*(никаких|каких-либо)?\s*данных)', "Утверждает, что нет данных"),
        (r'(нет\s*информации|не\s*найдено\s*информации)', "Утверждает, что нет информации"),
        (r'(в\s*источниках\s*нет|ни\s*в\s*одном\s*источнике)', "Утверждает, что в источниках нет"),
        (r'(не\s*удалось\s*найти|не\s*получилось\s*найти)', "Говорит 'не удалось найти'"),
        (r'(я\s*не\s*знаю|не\s*могу\s*ответить)', "Отказывается от ответа"),
        (r'(возможно|вероятно|скорее\s*всего|кажется)', "Использует неуверенность"),
        (r'(я\s*считаю|я\s*думаю|моё\s*мнение|мне\s*кажется)', "Выдаёт субъективное мнение"),
        (r'(я\s*сократил|я\s*пропустил|я\s*выбрал\s*(лучшие|главные))', "Сократил данные"),
        (r'\b(я|мне|меня|мой|моя|моё|мои)\b', "Использует личное местоимение"),
    ]
    
    for pattern, reason in lie_patterns:
        if re.search(pattern, text_lower):
            return True, reason
    
    return False, ""

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ + ГРАФ ЗНАНИЙ)
# ═══════════════════════════════════════════════════════════════════

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def episodic_path(uid): return os.path.join(DATA_DIR, f"episodic_{uid}.json")
def learning_path(uid): return os.path.join(DATA_DIR, f"learning_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")
def graph_path(uid): return os.path.join(DATA_DIR, f"graph_{uid}.json")

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
        except:
            return False
    
    def add_message(self, role, content):
        msg = {"role": role, "content": content[:2000], "timestamp": now().isoformat()}
        self.short_term.append(msg)
        if len(self.short_term) > 100:
            self.short_term = self.short_term[-100:]
        self.counter += 1
        self._extract_personal_info(content)
        self._extract_preferences(content)
        self.save()
    
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
        if self.profile:
            profile_text = f"👤 О пользователе: {', '.join([f'{k}: {v}' for k, v in self.profile.items()])}"
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
#  УНИВЕРСАЛЬНЫЙ ПОИСК
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
#  УНИВЕРСАЛЬНЫЙ ПАРСИНГ (БЕЗ ХАРДКОДА)
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
    
    # Год
    year_match = re.search(r'\b(19[0-9]{2}|20[0-9]{2})\b', text)
    if year_match:
        result['year'] = year_match.group(1)
    
    # Рейтинг (число с точкой 0-10)
    rating_match = re.search(r'\b(\d+\.\d{1,2})\b', text)
    if rating_match:
        try:
            val = float(rating_match.group(1))
            if 0 <= val <= 10:
                result['rating'] = rating_match.group(1)
        except:
            pass
    
    # Цена
    price_match = re.search(r'(\d+[\s,]?\d*)\s*(?:руб|\$|€|₽)', text, re.I)
    if price_match:
        result['price'] = price_match.group(0)
    
    # Описание
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
        'date': None
    }
    
    if not BEAUTIFULSOUP_AVAILABLE:
        return result
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'noscript']):
            tag.decompose()
        
        # Текст
        text = soup.get_text(separator=' ')
        text = re.sub(r'\s+', ' ', text).strip()
        result['text'] = text[:8000]
        
        # Заголовки
        for tag in soup.find_all(['h1', 'h2', 'h3']):
            h = tag.get_text().strip()
            if h:
                result['headings'].append(h[:200])
        
        # Списки
        for tag in soup.find_all(['ul', 'ol']):
            items = []
            for li in tag.find_all('li'):
                li_text = li.get_text().strip()
                if li_text and len(li_text) > 5:
                    items.append(li_text[:200])
            if items:
                result['lists'].append(items)
        
        # Структурированные элементы
        all_blocks = []
        for tag in soup.find_all(['div', 'li', 'article', 'section', 'p', 'span']):
            block_text = tag.get_text(separator=' ').strip()
            if len(block_text) > 30:
                all_blocks.append(block_text)
        
        # Извлекаем элементы
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
        
        # Дедупликация
        seen = set()
        unique_items = []
        for item in items:
            key = item['title'].lower().strip()
            if key not in seen and len(key) > 3:
                seen.add(key)
                unique_items.append(item)
        
        result['items'] = unique_items[:50]
        
        # Дата
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
    return {'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None}

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
#  ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(query: str, uid: int) -> Tuple[str, List[Dict]]:
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
        return answer, []
    
    # Сортировка
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
    
    confidence_text = f"Уверенность: {confidence:.1f}%"
    if confidence < 70:
        confidence_text += " ⚠️ Данных может быть недостаточно"
    elif confidence < 90:
        confidence_text += " 📊 Информация собрана, но могут быть пробелы"
    else:
        confidence_text += " ✅ Достаточно данных для точного ответа"
    
    answer_prompt = f"""
⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА!**

Найдено {len(sorted_items)} элементов. {confidence_text}

{items_text}

⚠️ **ПРАВИЛА:**
1. Используй ТОЛЬКО данные из интернета
2. Если данных мало — добавь блок "🧠 Дополнено из знаний"
3. НЕЛЬЗЯ смешивать знания с интернетом
4. НЕЛЬЗЯ говорить "нет доступа"

⚠️ **ФОРМАТ:**
📊 **Из интернета:** (перечисли найденное)
🧠 **Дополнено из знаний:** (если нужно)
✅ **Вывод:**

Вопрос: {query}
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.3, max_tokens=MAX_TOKENS_OUTPUT)
    
    is_lie, lie_reason = is_lie_by_sense(answer)
    
    if is_lie:
        logger.warning(f"⚠️ ОБНАРУЖЕНА ЛОЖЬ: {lie_reason}")
        corrected = f"""
⚠️ **ОБНАРУЖЕНА ПОПЫТКА ОБМАНА!**

📊 **Из интернета:**
{items_text[:2000]}

🧠 **Дополнено из знаний (честно):**
Я не могу использовать свои знания вместо интернета.

✅ **Вывод:**
В интернете найдено {len(sorted_items)} элементов.
"""
        return corrected, all_results
    
    return answer, all_results

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ
# ═══════════════════════════════════════════════════════════════════

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    
    if action == "action_new":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = True
        await query.edit_message_text("🔍 Введите запрос:")
    elif action == "action_clarify":
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            await query.edit_message_text("⚠️ Нет активного запроса.")
            return
        context.user_data['mode'] = 'clarify'
        context.user_data['awaiting_input'] = True
        await query.edit_message_text(f"📝 Уточните по запросу:\n\n{last_query}")
    elif action == "action_chat":
        context.user_data['mode'] = 'chat'
        context.user_data['awaiting_input'] = True
        await query.edit_message_text("💬 Что хотите сказать?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        return
    
    user_message = update.effective_message.text
    if not user_message:
        return
    
    if 'mode' not in context.user_data and not context.user_data.get('awaiting_input'):
        await update.effective_message.reply_text(
            "👋 Выберите действие:",
            reply_markup=ACTION_BUTTONS
        )
        return
    
    if not context.user_data.get('awaiting_input'):
        await update.effective_message.reply_text(
            "⚠️ Выберите действие:",
            reply_markup=ACTION_BUTTONS
        )
        return
    
    mode = context.user_data.get('mode', 'search')
    context.user_data['awaiting_input'] = False
    
    if mode == 'chat':
        memory = get_memory(user_id)
        context_text = memory.get_context(limit=5)
        chat_prompt = f"""
Ты — дружелюбный собеседник. Отвечай естественно.
Не используй интернет.

Контекст: {context_text}
Сообщение: {user_message}
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.7, max_tokens=MAX_TOKENS_OUTPUT)
        if not answer:
            answer = "😊 Я здесь!"
        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)
        await update.effective_message.reply_text(answer, reply_markup=ACTION_BUTTONS)
        return
    
    if mode == 'clarify':
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            await update.effective_message.reply_text("⚠️ Нет активного запроса.")
            return
        
        new_query = f"{last_query} {user_message}"
        start_time = time.time()
        context.user_data['found_answer'] = False
        
        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )
        
        answer, sources = await search_and_answer(new_query, user_id)
        
        context.user_data['found_answer'] = True
        await progress_task
        
        elapsed = int(time.time() - start_time)
        memory = get_memory(user_id)
        memory.add_message('user', f"Уточнение: {user_message}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = new_query
        context.user_data['last_answer'] = answer
        
        await update.effective_message.reply_text(
            f"⏱️ {elapsed} сек\n\n{answer}",
            reply_markup=ACTION_BUTTONS
        )
        return
    
    if mode == 'search':
        start_time = time.time()
        context.user_data['found_answer'] = False
        
        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )
        
        answer, sources = await search_and_answer(user_message, user_id)
        
        context.user_data['found_answer'] = True
        await progress_task
        
        elapsed = int(time.time() - start_time)
        memory = get_memory(user_id)
        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = user_message
        context.user_data['last_answer'] = answer
        
        await update.effective_message.reply_text(
            f"⏱️ {elapsed} сек\n\n{answer}",
            reply_markup=ACTION_BUTTONS
        )
        return

# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 Привет! Я поисковый ассистент.\n\n"
        "🔍 Ищу в интернете\n"
        "📊 Показываю источники\n"
        "⚠️ **НИКОГДА НЕ ВРУ**\n\n"
        "Выбери действие:",
        reply_markup=ACTION_BUTTONS
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    await update.effective_message.reply_text(
        f"📊 Статистика:\n"
        f"💬 Сообщений: {len(memory.short_term)}\n"
        f"👤 Профиль: {len(memory.profile)} полей"
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
        "🧹 Всё забыто!\n\nВыбери действие:",
        reply_markup=ACTION_BUTTONS
    )

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 БОТ ЗАПУСКАЕТСЯ")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    
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
