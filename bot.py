# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — АБСОЛЮТНО УНИВЕРСАЛЬНАЯ ВЕРСИЯ
#  Принудительный парсинг без хардкода
#  Всё на месте: память, граф знаний, таймер, кнопки, защита
#  Ничего не вырезано
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

PAGE_TIMEOUT = 12
SEARCH_RESULTS = 40
DEEPSEEK_MODEL = "deepseek-v4-flash"
CACHE_TTL = 3600
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 15
MAX_TOKENS_OUTPUT = 6000
MAX_TOKENS_VARIANTS = 500
MAX_ITERATIONS = 5
TARGET_CONFIDENCE = 90
MAX_PAGES_PER_ITERATION = 12

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
#  ПРОВЕРКА НА ЛОЖЬ
# ═══════════════════════════════════════════════════════════════════

def is_lie_by_sense(text: str) -> Tuple[bool, str]:
    text_lower = text.lower()
    lie_patterns = [
        (r'(нет|отсутствуют|не найдено|ничего не|не обнаружено)\s*(данных|информации|результатов)', "Отрицает наличие данных"),
        (r'(не могу|не получается|не удаётся|невозможно|не в состоянии)', "Говорит 'не могу'"),
        (r'(возможно|вероятно|скорее всего|наверное|похоже|кажется)', "Использует неуверенность"),
        (r'(я считаю|я думаю|я полагаю|моё мнение|мне кажется|по моему мнению)', "Выдаёт субъективное мнение"),
        (r'(я не знаю|не могу ответить|спросите позже|уточните запрос|не могу сказать)', "Отказывается от ответа"),
        (r'(рекомендую обратиться|лучше проверить|обратитесь к специалисту|проконсультируйтесь)', "Перекладывает ответственность"),
        (r'(слишком много|перегружен|много информации|большой объём)', "Жалуется на объём"),
        (r'(к сожалению|извините|прошу прощения)', "Начинает с отговорки"),
        (r'(зависит от|в зависимости от|ситуативно|контекстуально)', "Уходит от ответа"),
        (r'(нет\s*доступа|нет\s*интернета|не\s*могу\s*искать)', "Говорит 'нет доступа'"),
        (r'(на\s*основе\s*(моих|своих)\s*знаний|база\s*знаний|внутренние\s*данные)', "Использует свои знания вместо источников"),
        (r'(в\s*источниках\s*нет|источники\s*не\s*содержат|ни\s*в\s*одном\s*источнике)', "Утверждает, что в источниках нет"),
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
#  ТАЙМЕР
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        message = await context.bot.send_message(
            chat_id,
            "🌐 Ищу информацию в интернете...\n\n⏱️ 0 сек"
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
                for key in ['organic_results', 'organic', 'results']:
                    items = data.get(key, [])
                    if items:
                        for x in items:
                            results.append({
                                "title": x.get("title", ""),
                                "snippet": x.get("snippet", ""),
                                "link": x.get("link", ""),
                                "source": key
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

async def search_parallel(variants: List[str], max_sources: int = 30) -> List[Dict]:
    if not variants:
        return []
    logger.info(f"🔍 Поиск по {len(variants)} вариантам")
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
                if len(all_results) >= max_sources:
                    break
        if len(all_results) >= max_sources:
            break
    logger.info(f"📊 Найдено {len(all_results)} результатов")
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
#  УНИВЕРСАЛЬНЫЙ ПРИНУДИТЕЛЬНЫЙ ПАРСИНГ (БЕЗ ХАРДКОДА)
# ═══════════════════════════════════════════════════════════════════

def extract_structured_items(html: str) -> List[Dict]:
    """
    УНИВЕРСАЛЬНЫЙ ПРИНУДИТЕЛЬНЫЙ ПАРСИНГ
    Ищет ВСЕ числа, годы, названия в кавычках
    Без хардкода, без классов, без доменов
    """
    items = []
    
    if not BEAUTIFULSOUP_AVAILABLE:
        return items
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Удаляем мусор
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'noscript']):
            tag.decompose()
        
        # 2. Берём ВСЕ блоки с текстом
        all_blocks = []
        for tag in soup.find_all(['div', 'li', 'article', 'section', 'p', 'span', 'h1', 'h2', 'h3', 'h4', 'strong', 'b', 'td']):
            text = tag.get_text(separator=' ').strip()
            if len(text) > 20:
                all_blocks.append({
                    'tag': tag,
                    'text': text,
                    'html': str(tag)[:500]
                })
        
        # 3. Паттерны для поиска (УНИВЕРСАЛЬНЫЕ, без хардкода)
        rating_pattern = re.compile(r'\b(\d+\.\d{1,2})\b')
        year_pattern = re.compile(r'\b(19[0-9]{2}|20[0-9]{2})\b')
        title_pattern = re.compile(r'[«"]([^«"»]{3,50})[»"]|[А-Я][а-я]+\s+[А-Я][а-я]+|[A-Z][a-z]+\s+[A-Z][a-z]+')
        
        # 4. Находим блоки с рейтингами, годами, названиями
        rating_blocks = [b for b in all_blocks if rating_pattern.search(b['text'])]
        year_blocks = [b for b in all_blocks if year_pattern.search(b['text'])]
        title_blocks = [b for b in all_blocks if title_pattern.search(b['text'])]
        
        # 5. Объединяем все блоки (принудительно берём ВСЁ)
        combined_blocks = list(set(rating_blocks + year_blocks + title_blocks + all_blocks[:50]))
        
        # 6. Извлекаем данные из КАЖДОГО блока
        for block in combined_blocks[:60]:
            text = block['text']
            text = re.sub(r'\s+', ' ', text).strip()
            
            if len(text) < 20:
                continue
            
            # Извлекаем рейтинг (любое число с точкой)
            rating = None
            rating_match = rating_pattern.search(text)
            if rating_match:
                rating = rating_match.group(1)
            
            # Извлекаем год (любые 4 цифры)
            year = None
            year_match = year_pattern.search(text)
            if year_match:
                year = year_match.group(1)
            
            # Извлекаем название (в кавычках или два слова с заглавной)
            title = None
            title_match = title_pattern.search(text)
            if title_match:
                title = title_match.group(1) or title_match.group(2) or title_match.group(3)
                if title:
                    title = title.strip()
            
            # Если название не нашли — берём первую строку
            if not title:
                lines = text.split('. ')
                title = lines[0][:100] if lines else text[:100]
                title = re.sub(r'^[\d\s.)-]+', '', title).strip()
            
            # Пропускаем явный мусор
            if re.search(r'(реклама|промокод|скидка|подпишись|купить|заказать|рассылка|политика|cookie|регистрация|войти)', title, re.I):
                continue
            
            if len(title) < 3:
                continue
            
            # Извлекаем описание (всё, что после названия)
            desc = ""
            if title and title in text:
                parts = text.split(title, 1)
                if len(parts) > 1:
                    desc = parts[1][:300]
            
            items.append({
                'title': title[:200],
                'description': desc[:500],
                'year': year,
                'rating': rating,
                'source_text': text[:300]
            })
        
        # 7. Убираем дубликаты по названию
        seen = set()
        unique_items = []
        for item in items:
            title_lower = item['title'].lower()
            if title_lower not in seen and len(title_lower) > 3:
                seen.add(title_lower)
                unique_items.append(item)
        
        # 8. Сортируем: сначала с рейтингом, потом с годом
        unique_items.sort(key=lambda x: (0 if x.get('rating') else 1, 0 if x.get('year') else 2))
        
        return unique_items[:50]
        
    except Exception as e:
        logger.warning(f"⚠️ Ошибка принудительного парсинга: {e}")
    
    return items

def parse_html(html: str) -> Dict:
    result = {
        'text': '',
        'lists': [],
        'headings': [],
        'date': None,
        'tables': [],
        'definitions': [],
        'key_facts': [],
        'items': []
    }
    
    # Извлекаем структурированные элементы
    structured_items = extract_structured_items(html)
    if structured_items:
        result['items'] = structured_items
    
    if BEAUTIFULSOUP_AVAILABLE:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            text = soup.get_text(separator=' ')
            text = re.sub(r'\s+', ' ', text).strip()
            result['text'] = text[:8000]
            
            for heading in soup.find_all(['h1', 'h2', 'h3']):
                result['headings'].append(heading.get_text().strip())
            
            for ul in soup.find_all('ul'):
                items = [li.get_text().strip() for li in ul.find_all('li') if li.get_text().strip()]
                if items:
                    result['lists'].append(items)
            
            for ol in soup.find_all('ol'):
                items = [li.get_text().strip() for li in ol.find_all('li') if li.get_text().strip()]
                if items:
                    result['lists'].append(items)
            
            for table in soup.find_all('table'):
                rows = []
                for tr in table.find_all('tr'):
                    cells = [td.get_text().strip() for td in tr.find_all(['td', 'th'])]
                    if cells:
                        rows.append(cells)
                if rows:
                    result['tables'].append(rows)
            
            definitions = soup.find_all(['p', 'div'])
            for d in definitions:
                txt = d.get_text().strip()
                if re.search(r'—|–|-|это|является', txt) and len(txt) < 300:
                    result['definitions'].append(txt[:200])
            
            date_elem = soup.find('time')
            if date_elem and date_elem.get('datetime'):
                result['date'] = date_elem['datetime']
            if not result['date']:
                meta_date = soup.find('meta', {'property': 'article:published_time'})
                if meta_date and meta_date.get('content'):
                    result['date'] = meta_date['content']
            if not result['date']:
                date_match = re.search(r'\b\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}\b', text)
                if date_match:
                    result['date'] = date_match.group()
            
            return result
        except Exception as e:
            logger.warning(f"⚠️ BeautifulSoup ошибка: {e}")
    
    if not result['text']:
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        result['text'] = text[:8000]
        date_match = re.search(r'\b\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}\b', text)
        if date_match:
            result['date'] = date_match.group()
    
    return result

async def fetch_page_with_fallback(url: str) -> Dict:
    html = await fetch_with_browserless(url)
    if html:
        return parse_html(html)
    try:
        session = await get_session()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        async with session.get(url, headers=headers, timeout=PAGE_TIMEOUT) as r:
            if r.status == 200:
                html = await r.text()
                return parse_html(html)
    except Exception as e:
        logger.warning(f"⚠️ HTTP ошибка para {url}: {e}")
    return {'text': '', 'lists': [], 'headings': [], 'date': None, 'tables': [], 'definitions': [], 'key_facts': [], 'items': []}

async def fetch_multiple_pages(links: List[str], max_pages: int = MAX_PAGES_PER_ITERATION) -> List[Dict]:
    if not links:
        return []
    tasks = [fetch_page_with_fallback(link) for link in links[:max_pages]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r.get('text') or r.get('items')]

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════

async def generate_query_variants(user_message: str) -> List[str]:
    variants = [user_message]
    try:
        prompt = f"""
Сгенерируй 5 разных вариантов поискового запроса для этого вопроса.
Разные формулировки, синонимы, структуры.
Вопрос: {user_message}

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
        logger.warning(f"⚠️ Ошибка генерации вариантов: {e}")
    return list(dict.fromkeys(variants))[:5]

async def generate_refined_variants(user_message: str, items: List[Dict]) -> List[str]:
    variants = [user_message]
    
    keywords = set()
    for item in items[:10]:
        title = item.get('title', '')
        if title:
            words = title.split()[:2]
            keywords.update(words)
    
    if keywords:
        keyword_str = ' '.join(list(keywords)[:3])
        variants.append(f"{keyword_str} {user_message}")
        variants.append(f"лучшие {keyword_str}")
        variants.append(f"рейтинг {keyword_str}")
    
    current_year = now().year
    for year in range(current_year - 5, current_year + 1):
        variants.append(f"{user_message} {year}")
    
    return list(dict.fromkeys(variants))[:5]

# ═══════════════════════════════════════════════════════════════════
#  РАСЧЁТ УВЕРЕННОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(items: List[Dict], target_years: int = 5) -> float:
    if not items:
        return 0.0
    
    unique_count = len(set([item.get('title', '') for item in items]))
    unique_score = min(100, unique_count * 4)  # 25 элементов = 100%
    
    rating_count = sum(1 for item in items if item.get('rating'))
    rating_score = min(100, rating_count * 10)  # 10 рейтингов = 100%
    
    desc_count = sum(1 for item in items if item.get('description') and len(item['description']) > 20)
    desc_score = min(100, desc_count * 4)       # 25 описаний = 100%
    
    current_year = now().year
    recent_count = sum(1 for item in items if item.get('year') and current_year - int(item['year']) <= target_years)
    recent_score = min(100, recent_count * 5)   # 20 актуальных = 100%
    
    total_score = (
        unique_score * 0.30 +
        rating_score * 0.25 +
        desc_score * 0.20 +
        recent_score * 0.15 +
        min(100, len(items) * 2) * 0.10
    )
    
    return min(100, total_score)

# ═══════════════════════════════════════════════════════════════════
#  ДИНАМИЧЕСКИЙ ПОИСК ДО 90%
# ═══════════════════════════════════════════════════════════════════

async def search_until_confidence(
    user_message: str, 
    uid: int, 
    target_confidence: float = TARGET_CONFIDENCE,
    max_iterations: int = MAX_ITERATIONS
) -> Tuple[List[Dict], List[Dict], float]:
    
    all_items = []
    all_sources = []
    all_results = []
    iteration = 0
    confidence = 0.0
    
    variants = await generate_query_variants(user_message)
    search_variants = variants[:3]
    
    while confidence < target_confidence and iteration < max_iterations:
        iteration += 1
        logger.info(f"🔍 Итерация {iteration}: поиск по {len(search_variants)} вариантам")
        
        results = await search_parallel(search_variants, max_sources=30)
        if not results:
            logger.warning("⚠️ Поиск не дал результатов")
            break
        
        all_results.extend(results)
        
        links = [r.get('link', '') for r in results if r.get('link')]
        pages = await fetch_multiple_pages(links, max_pages=MAX_PAGES_PER_ITERATION)
        all_sources.extend(pages)
        
        items = []
        for page in pages:
            if page.get('items'):
                for item in page['items']:
                    items.append(item)
        
        all_items.extend(items)
        
        confidence = calculate_confidence(all_items)
        logger.info(f"📊 Уверенность: {confidence:.1f}% ({len(all_items)} элементов)")
        
        if confidence < target_confidence:
            new_variants = await generate_refined_variants(user_message, all_items)
            search_variants = new_variants[:3]
    
    return all_items, all_results, confidence

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(user_message: str, uid: int) -> Tuple[str, List[Dict]]:
    logger.info(f"🛡️ ЗАПРОС: {user_message[:50]}")
    
    items, search_results, confidence = await search_until_confidence(user_message, uid)
    
    if not items:
        memory = get_memory(uid)
        context = memory.get_context(limit=5)
        context_text = '\n'.join([m.get('content', '') for m in context])
        fallback_prompt = f"""
⚠️ **ТЫ НЕ МОЖЕШЬ СКАЗАТЬ "НЕТ ДОСТУПА"!**

В интернете ничего не найдено.
Ответь на основе своих знаний.
Если не знаешь — скажи честно.

Контекст: {context_text}
Вопрос: {user_message}
"""
        answer = await ask_deepseek(fallback_prompt, temperature=0.3)
        return answer, []
    
    memory = get_memory(uid)
    context = memory.get_context(limit=10)
    context_text = '\n'.join([m.get('content', '') for m in context])
    
    # Сортируем: сначала с рейтингом, потом по актуальности
    current_year = now().year
    sorted_items = sorted(
        items,
        key=lambda x: (
            0 if x.get('rating') else 1,
            abs(current_year - int(x.get('year', current_year))) if x.get('year') else 999
        )
    )[:30]
    
    items_text = ""
    for idx, item in enumerate(sorted_items[:30], 1):
        year = f" ({item.get('year')})" if item.get('year') else ""
        rating = f" ★ {item.get('rating')}" if item.get('rating') else ""
        desc = f" — {item.get('description')[:100]}" if item.get('description') else ""
        items_text += f"{idx}. {item.get('title')}{year}{rating}{desc}\n"
    
    confidence_text = f"Уверенность: {confidence:.1f}%"
    if confidence < 70:
        confidence_text += " ⚠️ Данных может быть недостаточно"
    elif confidence < 90:
        confidence_text += " 📊 Информация собрана, но могут быть пробелы"
    else:
        confidence_text += " ✅ Достаточно данных для точного ответа"
    
    answer_prompt = f"""
⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА!**

Найдено {len(sorted_items)} элементов. Уверенность: {confidence:.1f}%

{items_text}

⚠️ **ЗАПРЕЩЕНО (ЭТО ЛОЖЬ!):**
1. **НЕЛЬЗЯ** говорить "нет доступа к интернету"
2. **НЕЛЬЗЯ** говорить "на основе моих знаний"
3. **НЕЛЬЗЯ** говорить "в источниках нет"
4. **НЕЛЬЗЯ** игнорировать источники
5. **НЕЛЬЗЯ** придумывать свой ответ

⚠️ **ЕСЛИ ТЫ НАРУШИШЬ ХОТЯ БЫ ОДИН ЗАПРЕТ — ТЫ СОВРЁШЬ.**

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **Уверенность:** {confidence:.1f}%
📊 **Из источников:** (перечисли найденные элементы)
📊 **Дополнено из знаний:** (только если нужно, с 🧠)
✅ **Вывод:** (объективный итог)

Вопрос: {user_message}

Контекст о пользователе:
{context_text}
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.3, max_tokens=MAX_TOKENS_OUTPUT)
    
    is_lie, lie_reason = is_lie_by_sense(answer)
    
    if is_lie:
        logger.warning(f"⚠️ ОБНАРУЖЕНА ЛОЖЬ: {lie_reason}")
        answer_prompt += f"\n\n⚠️ ТЫ НАРУШИЛ ПРАВИЛА! {lie_reason}. ОТВЕТЬ ЗАНОВО, ТОЛЬКО ИЗ ДАННЫХ!"
        answer = await ask_deepseek(answer_prompt, temperature=0.3, max_tokens=MAX_TOKENS_OUTPUT)
        
        if is_lie_by_sense(answer)[0]:
            logger.warning("⚠️ Повторная ложь! Возвращаем честный отказ.")
            answer = f"""
⚠️ **Я НЕ МОГУ СОВРАТЬ.**

Найдено {len(sorted_items)} элементов в интернете:

{items_text[:2000]}

Уверенность: {confidence:.1f}%

Если нужен более полный ответ, попробуйте переформулировать запрос.
"""
    
    return answer, search_results

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ
# ═══════════════════════════════════════════════════════════════════

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    action = query.data
    
    if action == "action_new":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = True
        await query.edit_message_text("🔍 Введите ваш новый запрос:")
    elif action == "action_clarify":
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            await query.edit_message_text("⚠️ Нет активного запроса.")
            return
        context.user_data['mode'] = 'clarify'
        context.user_data['awaiting_input'] = True
        await query.edit_message_text(
            f"📝 Уточните по запросу:\n\n*Запрос:* {last_query}\n\nНапишите уточнение:"
        )
    elif action == "action_chat":
        context.user_data['mode'] = 'chat'
        context.user_data['awaiting_input'] = True
        await query.edit_message_text("💬 Что вы хотите сказать?")

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
            "⚠️ Пожалуйста, выберите действие:",
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
            answer = "😊 Я здесь! Что ещё хочешь обсудить?"
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
        
        sources_text = ""
        if sources:
            sources_text = "\n\n📚 **Источники:**\n"
            for i, s in enumerate(sources[:5], 1):
                link = s.get('link', '')
                title = s.get('title', '')
                if link:
                    sources_text += f"{i}. [{title}]({link})\n"
            if len(sources) > 5:
                sources_text += f"\n_и ещё {len(sources)-5} источников_"
        
        await update.effective_message.reply_text(
            f"⏱️ {elapsed} сек\n\n{answer}\n{sources_text}",
            reply_markup=ACTION_BUTTONS,
            disable_web_page_preview=True
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
        
        sources_text = ""
        if sources:
            sources_text = "\n\n📚 **Источники:**\n"
            for i, s in enumerate(sources[:5], 1):
                link = s.get('link', '')
                title = s.get('title', '')
                if link:
                    sources_text += f"{i}. [{title}]({link})\n"
            if len(sources) > 5:
                sources_text += f"\n_и ещё {len(sources)-5} источников_"
        
        await update.effective_message.reply_text(
            f"⏱️ {elapsed} сек\n\n{answer}\n{sources_text}",
            reply_markup=ACTION_BUTTONS,
            disable_web_page_preview=True
        )
        return

# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    await update.effective_message.reply_text(
        "👋 Привет! Я поисковый ассистент.\n\n"
        "🔍 Ищу в интернете до 90% уверенности\n"
        "📊 Показываю источники и рейтинги\n"
        "⚠️ **НИКОГДА НЕ ВРУ**\n\n"
        "Выбери действие:",
        reply_markup=ACTION_BUTTONS
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    health = memory.memory_health_check()
    text = "📊 **Статистика памяти:**\n\n"
    text += f"💬 Сообщений: {health['short_term']}\n"
    text += f"👤 Профиль: {health['profile']} полей\n"
    text += f"⭐ Важных фактов: {health['episodic']}\n"
    text += f"💡 Предпочтений: {health['preferences']}\n"
    text += f"🧠 Фактов в графе знаний: {health['graph_facts']}\n"
    text += f"📝 Всего сообщений: {health['total_messages']}"
    await update.effective_message.reply_text(text)

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
    logger.info("🚀 БОТ ЗАПУСКАЕТСЯ (УНИВЕРСАЛЬНАЯ ВЕРСИЯ)")
    logger.info("🎯 Целевая уверенность: 90%")
    logger.info("📄 Страниц за итерацию: 12")
    logger.info("🔍 Парсинг: принудительный, без хардкода")
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
