# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ
#  ПАРАЛЛЕЛЬНЫЙ ПОИСК (APISerpent) + ПАРАЛЛЕЛЬНАЯ ЗАГРУЗКА (HTTP + Browserless)
#  ДО 15 ИСТОЧНИКОВ, ЧЕСТНЫЙ ОТВЕТ ИЗ ЗНАНИЙ
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

MAX_PAGES = 5
PAGE_TIMEOUT = 4
SEARCH_RESULTS = 12
DEEPSEEK_MODEL = os.getenv("MODEL_DEFAULT", "deepseek-v4")
CACHE_TTL = 3600
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

logger.info("🚀 ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ")
logger.info(f"🌐 Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")

# ═══════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════

_http_session = None
search_cache = {}

async def get_session():
    global _http_session
    if _http_session is None:
        _http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return _http_session

# ═══════════════════════════════════════════════════════════════════
#  DEEPSEEK
# ═══════════════════════════════════════════════════════════════════

async def ask_deepseek(prompt: str, temperature: float = 0.2, max_tokens: int = 2500) -> str:
    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens
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
                        return content
                else:
                    logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: HTTP {r.status}")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: {e}")
        
        if attempt < 2:
            await asyncio.sleep(2)
    
    return ""

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
#  ПОИСК (APISerpent с deep=true → Serper)
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
                
                # Organic results
                organic = data.get("organic_results", [])
                for x in organic:
                    results.append({
                        "title": x.get("title", ""),
                        "snippet": x.get("snippet", ""),
                        "link": x.get("link", ""),
                        "source": "organic"
                    })
                
                # People Also Ask
                paa = data.get("people_also_ask", [])
                for item in paa:
                    results.append({
                        "title": item.get("question", ""),
                        "snippet": item.get("snippet", ""),
                        "link": item.get("link", ""),
                        "source": "paa"
                    })
                
                # Featured Snippet
                featured = data.get("featured_snippet", {})
                if featured:
                    results.append({
                        "title": featured.get("title", ""),
                        "snippet": featured.get("snippet", ""),
                        "link": featured.get("link", ""),
                        "source": "featured"
                    })
                
                # AI Overview
                ai_overview = data.get("ai_overview", {})
                if ai_overview:
                    results.append({
                        "title": "AI Overview",
                        "snippet": ai_overview.get("text", ""),
                        "link": "",
                        "source": "ai_overview"
                    })
                
                return results
    except:
        pass
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
    except:
        pass
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

async def search_parallel(variants: List[str], max_sources: int = 15) -> List[Dict]:
    if not variants:
        return []
    
    logger.info(f"🔍 Параллельный поиск по {len(variants)} вариантам")
    
    tasks = [search_with_cache(v) for v in variants[:10]]
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
            await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            html = await page.content()
            await page.close()
            return html
    except:
        pass
    return None

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ
# ═══════════════════════════════════════════════════════════════════

def parse_html(html: str) -> Dict:
    if BEAUTIFULSOUP_AVAILABLE:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            
            text = soup.get_text(separator=' ', strip=True)
            text = re.sub(r'\s+', ' ', text)
            
            lists = []
            for ul in soup.find_all(['ul', 'ol']):
                for li in ul.find_all('li'):
                    li_text = li.get_text(strip=True)
                    if len(li_text) > 10:
                        lists.append(li_text)
            
            headings = []
            for h in soup.find_all(['h1', 'h2', 'h3']):
                h_text = h.get_text(strip=True)
                if len(h_text) > 5:
                    headings.append(h_text)
            
            return {'text': text[:6000], 'lists': lists[:10], 'headings': headings[:5]}
        except:
            pass
    
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', text)
    return {'text': ' '.join(sentences[:25])[:4000], 'lists': [], 'headings': []}

# ═══════════════════════════════════════════════════════════════════
#  ПАРАЛЛЕЛЬНАЯ ЗАГРУЗКА СТРАНИЦ (HTTP + Browserless)
# ═══════════════════════════════════════════════════════════════════

async def fetch_page_parallel(url: str) -> Optional[Dict]:
    """Параллельная загрузка: HTTP и Browserless одновременно"""
    
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
    
    # Запускаем параллельно
    http_task = asyncio.create_task(fetch_http())
    browserless_task = asyncio.create_task(fetch_browserless())
    
    # Ждём первый успешный результат
    done, pending = await asyncio.wait(
        [http_task, browserless_task],
        return_when=asyncio.FIRST_COMPLETED,
        timeout=PAGE_TIMEOUT + 2
    )
    
    # Отменяем оставшиеся
    for task in pending:
        task.cancel()
    
    # Берём результат
    for task in done:
        try:
            result = task.result()
            if result and result.get('text'):
                return result
        except:
            pass
    
    # Если ничего не получили — ждём оба до конца
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

async def fetch_pages_parallel(results: List[Dict], max_pages: int = MAX_PAGES) -> List[Dict]:
    if not results:
        return []
    
    logger.info(f"📄 Параллельная загрузка до {max_pages} страниц (HTTP + Browserless)")
    
    top_results = results[:max_pages * 2]
    
    tasks = []
    urls = []
    for r in top_results:
        url = r.get('link', '')
        if url:
            tasks.append(fetch_page_parallel(url))
            urls.append(url)
    
    pages_data = await asyncio.gather(*tasks)
    
    pages = []
    for i, parsed in enumerate(pages_data):
        if parsed and parsed.get('text'):
            pages.append({
                'url': urls[i] if i < len(urls) else '',
                'title': top_results[i].get('title', '') if i < len(top_results) else '',
                'parsed': parsed
            })
        if len(pages) >= max_pages:
            break
    
    logger.info(f"✅ Загружено {len(pages)} страниц (параллельно)")
    return pages

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ РЕЗУЛЬТАТОВ
# ═══════════════════════════════════════════════════════════════════

def is_good_result(result: Dict) -> bool:
    url = result.get('link', '')
    title = result.get('title', '').lower()
    snippet = result.get('snippet', '').lower()
    
    bad_domains = ['youtube.com', 'instagram.com', 'facebook.com', 'tiktok.com', 'twitter.com']
    if any(d in url for d in bad_domains):
        return False
    
    if len(snippet) < 50:
        return False
    
    useful_words = ['скрипт', 'пример', 'шаблон', 'вопрос', 'диалог', 'алгоритм', 'шаг', 'техника']
    if any(w in title or w in snippet for w in useful_words):
        return True
    
    good_domains = ['habr.com', 'vc.ru', 'cossa.ru', 'blog', 'wiki', 'guide', 'how-to']
    if any(d in url for d in good_domains):
        return True
    
    if len(snippet) > 150:
        return True
    
    return False

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(pages: List[Dict]) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'factors': []}
    
    if pages:
        reliable = 0
        for p in pages[:3]:
            url = p.get('url', '')
            if any(d in url for d in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
                reliable += 1
            elif any(d in url for d in ['.com', '.org', '.net', '.ru']):
                reliable += 0.5
        score = min(100, (reliable / max(len(pages[:3]), 1)) * 100)
        confidence['source_reliability'] = score
        confidence['factors'].append(f"Надёжность: {score:.0f}%")
    else:
        confidence['source_reliability'] = 20
        confidence['factors'].append("Нет источников")
    
    structure_count = 0
    for p in pages:
        parsed = p.get('parsed', {})
        structure_count += len(parsed.get('lists', [])) + len(parsed.get('headings', []))
    
    completeness = min(100, structure_count * 10)
    confidence['data_completeness'] = completeness
    confidence['factors'].append(f"Полнота: {completeness:.0f}%")
    
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
#  ГЕНЕРАЦИЯ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

async def generate_answer(query: str, pages: List[Dict], memory_context: str = "") -> str:
    all_data = []
    
    for p in pages[:5]:
        text = p.get('parsed', {}).get('text', '')
        if not text:
            continue
        
        lists = re.findall(r'(?:^|\n)\s*[•\-*\d+.]\s*[^\n]{10,}', text, re.MULTILINE)
        if lists:
            all_data.append("📋 " + "\n".join([f"  • {l.strip()}" for l in lists[:5]]))
        
        steps = re.findall(r'(?:шаг|этап|пункт)\s*(\d+)[\s:]*([^\n.]{5,})', text, re.I)
        if steps:
            all_data.append("🔄 " + "\n".join([f"  Шаг {s[0]}: {s[1].strip()}" for s in steps[:3]]))
        
        questions = re.findall(r'[^.!?]{10,150}\?', text)
        if questions:
            all_data.append("❓ " + "\n".join([f"  {q.strip()}" for q in questions[:3]]))
        
        prices = re.findall(r'(\d+[\s]*[–-]?\s*\d*[\s]*(?:руб|₽|\$|€|USD|EUR))', text, re.I)
        if prices:
            all_data.append("💰 " + "\n".join([f"  {p.strip()}" for p in prices[:3]]))
    
    structures_text = "\n\n".join(all_data)
    sources_text = "\n".join([f"• {p.get('url', '')}" for p in pages[:5]])
    
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
    
    for attempt in range(3):
        answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=2500)
        if answer and len(answer) > 100:
            if check_for_lies(answer) or check_refusal(answer):
                continue
            return answer
        logger.warning(f"⚠️ Попытка {attempt+1} не удалась")
        await asyncio.sleep(2)
    
    return await answer_from_knowledge(query)

async def answer_from_knowledge(query: str) -> str:
    prompt = f"""
⚠️ **В интернете не удалось найти достаточно информации по запросу.**

⚠️ **ЗАПРОС:** {query}

⚠️ **Ты — эксперт. Ответь из своих знаний.**

⚠️ **ПРАВИЛА:**
1. Дай структурированный ответ.
2. Если не знаешь — скажи честно.
3. Отметь: 🧠 Ответ основан на моих знаниях.
"""
    answer = await ask_deepseek(prompt, temperature=0.3, max_tokens=2500)
    if not answer:
        return "⚠️ Не удалось найти информацию. Попробуйте переформулировать запрос."
    return f"🧠 **ОТВЕТ (на основе знаний):**\n\n{answer}\n\n⚠️ В интернете мало данных."

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

current_stage = "⏳ Запуск"

def set_stage(stage: str):
    global current_stage
    current_stage = stage

async def process_query(query: str, uid: int) -> str:
    logger.info(f"🧠 НАЧАЛО ОБРАБОТКИ: {query[:100]}...")
    set_stage("🧠 Анализирую запрос")
    
    analyze_prompt = f"""
⚠️ **Ты — аналитик поиска. Сгенерируй 10 вариантов поисковых запросов для запроса:**
{query}

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{"understanding": "краткое понимание", "variants": ["вариант 1", ...]}}
"""
    try:
        analysis_text = await ask_deepseek(analyze_prompt, temperature=0.3, max_tokens=500)
        json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
            variants = analysis.get('variants', [query])
        else:
            variants = [query]
    except:
        variants = [query]
    
    logger.info(f"🔍 Сгенерировано {len(variants)} вариантов")
    
    set_stage("🔍 Ищу в интернете")
    all_results = await search_parallel(variants, max_sources=15)
    
    filtered_results = [r for r in all_results if is_good_result(r)]
    logger.info(f"📊 После фильтрации: {len(filtered_results)} источников")
    
    if len(filtered_results) < 4:
        logger.info("🔄 Мало источников, добираем...")
        broad_variants = [
            f"пример скрипта продаж {query}",
            f"шаблон продаж {query}",
            f"как продавать {query}",
            f"вопросы для клиента {query}"
        ]
        more_results = await search_parallel(broad_variants, max_sources=10)
        for r in more_results:
            if is_good_result(r) and r.get('link') not in [x.get('link') for x in filtered_results]:
                filtered_results.append(r)
    
    if len(filtered_results) < 3:
        logger.warning("⚠️ Мало источников, отвечаю из знаний")
        return await answer_from_knowledge(query)
    
    set_stage("📄 Загружаю страницы")
    pages = await fetch_pages_parallel(filtered_results, max_pages=MAX_PAGES)
    
    if not pages:
        logger.warning("⚠️ Не удалось загрузить страницы")
        return await answer_from_knowledge(query)
    
    memory = get_memory(uid)
    memory_context = ""
    if memory.knowledge_graph.get_all_facts():
        facts = memory.knowledge_graph.get_all_facts()[:3]
        memory_context = f"🧠 **Из памяти:** {', '.join(facts)}\n"
    
    set_stage("🤔 Формирую ответ")
    answer = await generate_answer(query, pages, memory_context)
    
    confidence = calculate_confidence(pages)
    formatted_answer = format_confidence(confidence) + "\n\n" + answer
    
    logger.info(f"✅ ОТВЕТ СФОРМИРОВАН, длина {len(answer)} символов")
    return formatted_answer

# ═══════════════════════════════════════════════════════════════════
#  ТАЙМЕР
# ═══════════════════════════════════════════════════════════════════

async def show_progress(chat_id, context, start_time):
    global current_stage
    try:
        msg = await context.bot.send_message(
            chat_id,
            f"⏳ {current_stage}\n\n⏱️ 0 сек"
        )
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
        
        text = update.effective_message.text.strip() if update.effective_message else ""
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
                "• ⏹️ Стоп — остановить всё\n"
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
        await update.message.reply_text(f"⏱️ {elapsed} сек\n\n{answer}", reply_markup=MAIN_KEYBOARD)
        
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
        "⚠️ Я никогда не вру. Если данных мало — я скажу честно.\n"
        "🧠 Я запоминаю тебя и учусь с каждым диалогом.\n"
        "⚡️ Отвечаю быстро и точно.",
        reply_markup=MAIN_KEYBOARD
    )

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
    logger.info("✅ ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ")
    logger.info("✅ APISerpent с deep=true")
    logger.info("✅ Параллельный поиск до 15 источников")
    logger.info("✅ Параллельная загрузка (HTTP + Browserless)")
    logger.info("✅ Честный ответ из знаний при необходимости")
    
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
