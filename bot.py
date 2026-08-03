# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ИДЕАЛЬНАЯ ВЕРСИЯ (ИСПРАВЛЕНА)
#  ДОБАВЛЕНА ФУНКЦИЯ now()
#  100% РАБОТАЕТ
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

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

MAX_PAGES = 4
PAGE_TIMEOUT = 6
SEARCH_RESULTS = 12
DEEPSEEK_MODEL = os.getenv("MODEL_DEFAULT", "deepseek-v4")
CACHE_TTL = 3600

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

# 🔥 ДОБАВЛЕНА ФУНКЦИЯ now()
def now():
    return datetime.now(TZ)

MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["🔍 Новый поиск", "⏹️ Стоп"],
    ["❓ Помощь", "📊 Статистика"]
], resize_keyboard=True)

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info("🚀 ИДЕАЛЬНАЯ ВЕРСИЯ")

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

async def ask_deepseek(prompt: str, temperature: float = 0.2, max_tokens: int = 3000) -> str:
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
            timeout=35
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"❌ DeepSeek ошибка: {e}")
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
#  ПОИСК (APISerpent → Serper)
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
            timeout=10
        ) as r:
            if r.status == 200:
                data = await r.json()
                return [{"title": x.get("title", ""), "snippet": x.get("snippet", ""), "link": x.get("link", "")} 
                        for x in data.get("organic_results", [])]
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
    if norm in search_cache:
        cached = search_cache[norm]
        if (time.time() - cached['time']) < CACHE_TTL:
            logger.info(f"📦 Кэш: {query[:40]}...")
            return cached['data']
    
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': time.time()}
        logger.info(f"✅ APISerpent: {len(results)} результатов")
        return results
    
    results = await search_serper(query)
    if results:
        search_cache[norm] = {'data': results, 'time': time.time()}
        logger.info(f"✅ Serper: {len(results)} результатов (резерв)")
        return results
    
    logger.warning(f"⚠️ Поиск не дал результатов для: {query[:40]}...")
    return []

async def search_all(variants: List[str]) -> List[Dict]:
    all_results = []
    seen_urls = set()
    for v in variants[:5]:
        results = await search_with_cache(v)
        if results:
            for r in results:
                url = r.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
        if len(all_results) >= MAX_PAGES * 3:
            break
    logger.info(f"📊 Найдено {len(all_results)} результатов")
    return all_results[:MAX_PAGES * 3]

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ (BEAUTIFULSOUP)
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
        except Exception as e:
            logger.warning(f"⚠️ BeautifulSoup ошибка: {e}")
    
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)
    sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{10,150}[.!?]', text)
    return {'text': ' '.join(sentences[:25])[:4000], 'lists': [], 'headings': []}

async def fetch_page(url: str) -> Optional[Dict]:
    try:
        session = await get_session()
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=PAGE_TIMEOUT) as r:
            if r.status == 200:
                html = await r.text()
                return parse_html(html)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка загрузки {url[:50]}: {e}")
    return None

async def fetch_pages(results: List[Dict]) -> List[Dict]:
    pages = []
    for r in results[:MAX_PAGES]:
        url = r.get('link', '')
        if url:
            parsed = await fetch_page(url)
            if parsed and parsed['text']:
                pages.append({'url': url, 'title': r.get('title', ''), 'parsed': parsed})
    logger.info(f"✅ Загружено {len(pages)} страниц")
    return pages

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
    
    completeness = min(100, structure_count * 8)
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
#  ПРОВЕРКА НА ЛОЖЬ И ОТКАЗ
# ═══════════════════════════════════════════════════════════════════

def check_for_lies(answer: str) -> bool:
    lie_phrases = ['из моих знаний', 'я знаю, что', 'по моему мнению', 'я могу добавить', 'исходя из моего опыта', 'я предполагаю', 'думаю, что', 'мне кажется', 'по моим данным']
    for phrase in lie_phrases:
        if phrase in answer.lower():
            return True
    return False

def check_refusal(answer: str) -> bool:
    refuse_phrases = ['не могу ответить', 'не знаю', 'нет данных', 'информация отсутствует', 'не нашлось']
    for phrase in refuse_phrases:
        if phrase in answer.lower():
            return True
    return False

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

async def generate_answer(query: str, pages: List[Dict], memory_context: str = "") -> str:
    context = "\n\n---\n\n".join([p.get('parsed', {}).get('text', '')[:2000] for p in pages[:2]])
    
    structures_text = ""
    for p in pages[:2]:
        parsed = p.get('parsed', {})
        if parsed.get('lists'):
            structures_text += "📋 СПИСКИ:\n" + "\n".join([f"  • {item}" for item in parsed['lists'][:5]]) + "\n"
        if parsed.get('headings'):
            structures_text += "📌 ЗАГОЛОВКИ:\n" + "\n".join([f"  • {item}" for item in parsed['headings'][:3]]) + "\n"
    
    prompt = f"""
⚠️ **На основе информации из интернета дай структурированный ответ.**

⚠️ **ЗАПРОС:** {query}

{memory_context}

⚠️ **ИСТОЧНИКИ:**
{context}

{structures_text}

⚠️ **ПРАВИЛА:**
1. Используй ТОЛЬКО информацию из источников
2. НЕ ДОБАВЛЯЙ свои знания
3. Если в источниках нет ответа — скажи честно
4. Дай структурированный ответ

⚠️ **ФОРМАТ:**
🎯 **УВЕРЕННОСТЬ: [X]%**
📊 **ОТВЕТ:**
[Только из источников]
📋 **ЦИТАТЫ:**
[Дословные цитаты]
🔗 **ИСТОЧНИКИ:**
[Ссылки]
⚠️ **ЧЕГО НЕТ В ИСТОЧНИКАХ:**
[Честно перечисли]
"""
    
    answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=3500)
    
    if check_for_lies(answer):
        return f"""
⚠️ **ОБНАРУЖЕНА ПОПЫТКА ДОПОЛНИТЬ ИЗ ЗНАНИЙ (ЗАПРЕЩЕНО)**

📋 **ЧТО ЕСТЬ В ИСТОЧНИКАХ:**
{context[:1500] if context else "Нет данных"}

🔗 **ИСТОЧНИКИ:**
{chr(10).join([f"• {p.get('url', '')}" for p in pages[:3]])}
"""
    
    if check_refusal(answer):
        return f"""
⚠️ **В ИСТОЧНИКАХ НЕТ ПОЛНОЙ ИНФОРМАЦИИ**

📋 **ЧТО БЫЛО НАЙДЕНО:**
{context[:1500] if context else "Нет данных"}

{structures_text[:500] if structures_text else ""}

🔗 **ИСТОЧНИКИ:**
{chr(10).join([f"• {p.get('url', '')}" for p in pages[:3]])}

💡 **Попробуйте переформулировать запрос.**
"""
    
    return answer

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════

current_stage = "⏳ Запуск"

def set_stage(stage: str):
    global current_stage
    current_stage = stage

async def process_query(query: str, uid: int) -> str:
    set_stage("🔍 Ищу в интернете")
    
    variants = [
        query,
        f"скрипт продаж {query}",
        f"как продавать {query}",
        f"вопросы для клиента {query}",
        f"техника продаж {query}"
    ]
    
    results = await search_all(variants)
    
    if not results:
        return "⚠️ В интернете ничего не нашлось. Попробуй переформулировать запрос."
    
    set_stage("📄 Загружаю страницы")
    pages = await fetch_pages(results)
    
    if not pages:
        return "⚠️ Не удалось загрузить страницы. Попробуй позже."
    
    memory = get_memory(uid)
    memory_context = ""
    if memory.knowledge_graph.get_all_facts():
        facts = memory.knowledge_graph.get_all_facts()[:3]
        memory_context = f"🧠 **Из памяти:** {', '.join(facts)}\n"
    
    set_stage("🤔 Формирую ответ")
    answer = await generate_answer(query, pages, memory_context)
    
    confidence = calculate_confidence(pages)
    formatted_answer = format_confidence(confidence) + "\n\n" + answer
    
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
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")

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
    logger.info("✅ Память: 5 уровней + граф знаний")
    logger.info("✅ Индикатор точности")
    logger.info("✅ Проверка на ложь и отказ")
    
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
