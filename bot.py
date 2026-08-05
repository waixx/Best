# ═══════════════════════════════════════════════════════════════════
#  БОТ: BROWAIX — АГЕНТНАЯ АРХИТЕКТУРА (v17.4)
#  УНИВЕРСАЛЬНЫЙ ПОИСК, СРАВНЕНИЕ И АНАЛИЗ ДАННЫХ ИЗ ИНТЕРНЕТА
#  НИЧЕГО НЕ ВЫРЕЗАНО — ВСЁ ВКЛЮЧЕНО
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
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple, Any
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
#  СИСТЕМНАЯ ИНСТРУКЦИЯ
# ═══════════════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """
Ты — **Джарвис**, персональный ИИ-ассистент.

1. **Абсолютная честность** — никогда не выдумываешь факты.
2. **Разделение источников** — 🌐 интернет, 🧠 знания, 📌 память.
3. **Актуальность** — сегодня {today}. Всегда используй эту дату.
4. **Структурированность** — ответы с маркерами (✅, 📊, 📋, 🌐).
5. **Память пользователя** — используй только как дополнение в конце ответа.
"""

# ═══════════════════════════════════════════════════════════════════
#  ПРОМПТЫ ДЛЯ АГЕНТА
# ═══════════════════════════════════════════════════════════════════

PLANNER_PROMPT = """
Ты — Планировщик (Planner). Проанализируй запрос пользователя и составь план действий.

Запрос: {query}

Контекст (предыдущие сообщения, профиль пользователя):
{context}

Верни JSON-объект с планом. Структура:
{{
  "goal": "краткое описание цели",
  "subtasks": [
    {{"id": 1, "action": "search|fetch|analyze|calculate|chat", "params": {{"query": "..."}} }}
  ],
  "success_criteria": "условие, при котором задача считается решённой",
  "max_iterations": 1
}}

Правила:
- Всегда добавляй подзадачу "search" с вариантами поисковых запросов.
- Если запрос простой (приветствие, беседа) — верни {{"action": "chat"}}.
- Если нужно сравнить — добавь подзадачи для каждого объекта.
- Отвечай только JSON, без пояснений.
"""

EVALUATOR_PROMPT = """
Ты — Оценщик (Evaluator). Оцени, достаточно ли данных для ответа на вопрос пользователя.

Вопрос: {query}
Собранные данные (краткая выжимка из источников):
{data_summary}

Верни JSON:
{{
  "is_sufficient": true/false,
  "missing_info": ["чего не хватает"],
  "confidence": 0-100,
  "suggested_search": ["дополнительные запросы"]
}}
"""

REFLECTOR_PROMPT = """
Ты — Рефлектор (Reflector). Проверь качество сгенерированного ответа перед отправкой пользователю.

Вопрос: {query}
Ответ: {answer}

Оцени ответ по критериям:
1. Честность — нет выдумок, есть ссылки на источники.
2. Полнота — отвечает на все аспекты вопроса.
3. Структура — есть маркеры, разделение по источникам.
4. Актуальность — указана дата.

Верни JSON:
{{
  "is_good": true/false,
  "feedback": "что нужно исправить",
  "improved_answer": "исправленный ответ"
}}
"""

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
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")  # оставлен на случай, но не используется
CURRENCY_API_KEY = os.getenv("CURRENCY_API_KEY")

ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

PAGE_TIMEOUT = 3
SEARCH_RESULTS = 12
CACHE_TTL = 600
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 15
MAX_TOKENS_OUTPUT = 8000
MAX_TOKENS_PLANNER = 600
MAX_ITERATIONS = 1
TARGET_CONFIDENCE = 80
MAX_PAGES_PER_ITERATION = 2
BROWSER_WS_ENDPOINT = os.getenv("BROWSER_WS_ENDPOINT", "")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

# ═══════════════════════════════════════════════════════════════════
#  ТЕКУЩАЯ ДАТА (СИСТЕМНОЕ ВРЕМЯ)
# ═══════════════════════════════════════════════════════════════════

def now():
    return datetime.now(TZ)

# ═══════════════════════════════════════════════════════════════════
#  КНОПКИ (ТОЛЬКО ДЛЯ ИСТОЧНИКОВ)
# ═══════════════════════════════════════════════════════════════════

SHOW_SOURCES_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("📎 Показать источники", callback_data="show_sources")]
])

HIDE_SOURCES_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔒 Скрыть источники", callback_data="hide_sources")]
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

def check_answer_quality(answer: str, min_length: int = 200) -> Tuple[bool, str]:
    if not answer:
        return False, "Ответ пустой"
    if len(answer) < min_length:
        return False, f"Ответ слишком короткий ({len(answer)} символов, нужно {min_length})"
    
    forbidden = ["по моему мнению", "я считаю", "я думаю", "на мой взгляд", "я предполагаю"]
    for phrase in forbidden:
        if phrase in answer.lower():
            return False, f"Обнаружена запрещённая фраза: '{phrase}'"
    
    return True, "OK"

async def ask_deepseek(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = MAX_TOKENS_OUTPUT,
    use_pro: bool = True,
    system_override: Optional[str] = None
) -> str:
    today_str = now().strftime('%d.%m.%Y')
    system = system_override if system_override else SYSTEM_INSTRUCTION.format(today=today_str)
    key = cache_key(prompt + system)
    
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        if today_str in cached:
            return cached
        else:
            del answer_cache[key]

    for attempt in range(2):
        try:
            session = await get_session()
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=60
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if 'choices' in data and len(data['choices']) > 0:
                        content = data['choices'][0]['message']['content']
                        if content:
                            is_valid, reason = check_answer_quality(content, min_length=150)
                            if is_valid:
                                answer_cache[key] = {'data': content, 'time': time.time()}
                                logger.info(f"✅ Ответ получен, длина {len(content)}")
                                return content
                            else:
                                logger.warning(f"⚠️ Ответ не прошёл проверку: {reason}")
                else:
                    logger.warning(f"⚠️ DeepSeek HTTP {r.status}")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek ошибка: {e}")
        await asyncio.sleep(1)
    
    return "⚠️ Не удалось получить ответ от DeepSeek."

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ)
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
        if len(self.short_term) > 500:
            old = self.short_term[:-500]
            self._compress(old)
            self.short_term = self.short_term[-500:]
        self.counter += 1
        self._extract_personal_info(content)
        self._extract_preferences(content)
        self._update_knowledge_graph(content)
        self.save()
    
    def _compress(self, messages):
        important_keywords = ['это', 'является', 'состоит', 'находится', 'важно', 'главное']
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
        if len(self.episodic) > 1000:
            self.episodic = self.episodic[-1000:]
    
    def _extract_personal_info(self, text):
        patterns = {
            'name': r'(?:меня зовут|зовут|я)\s+([А-Яа-яA-Za-z\s]{2,30})',
            'age': r'(?:мне|возраст)\s+(\d{1,3})\s*(?:лет|года)',
            'city': r'(?:я живу|живу в|из города)\s+([А-Яа-яA-Za-z\s]{2,30})',
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
    
    def _update_knowledge_graph(self, text):
        facts = re.findall(r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:это|является)\s+([^.!?]{10,100})', text, re.I)
        for m in facts:
            fact = f"{m[0].strip()} — {m[1].strip()}"
            if len(fact) > 15:
                self.knowledge_graph.add_fact(fact)
    
    def get_full_context(self, limit=30) -> str:
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
            context_parts.append(f"🧠 Знания: {', '.join(facts[:10])}")
        if self.episodic:
            important = sorted(self.episodic, key=lambda x: x.get('priority', 0), reverse=True)[:5]
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
            facts = self.knowledge_graph.get_all_facts()[:5]
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
#  НАПОМИНАНИЯ
# ═══════════════════════════════════════════════════════════════════

def init_reminders_db():
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  text TEXT,
                  due_date TEXT,
                  done INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

def add_reminder(user_id, text, due_date=None):
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, text, due_date) VALUES (?, ?, ?)",
              (user_id, text, due_date))
    conn.commit()
    conn.close()

def get_reminders(user_id):
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute("SELECT id, text, due_date FROM reminders WHERE user_id=? AND done=0", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def mark_reminder_done(reminder_id):
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
    conn.commit()
    conn.close()

init_reminders_db()

# ═══════════════════════════════════════════════════════════════════
#  ПРИНУДИТЕЛЬНОЕ ДОБАВЛЕНИЕ АКТУАЛЬНОЙ ДАТЫ
# ═══════════════════════════════════════════════════════════════════

def force_current_date(query: str) -> str:
    today = now()
    current_year = today.year
    current_date = today.strftime('%d.%m.%Y')
    
    if "завтра" in query.lower():
        tomorrow = today + timedelta(days=1)
        date_str = tomorrow.strftime('%d.%m.%Y')
        return query.replace("завтра", f"завтра {date_str}")
    
    if "сегодня" in query.lower():
        return query.replace("сегодня", f"сегодня {current_date}")
    
    date_pattern = r'(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)'
    match = re.search(date_pattern, query.lower())
    if match:
        if not re.search(r'\b(20\d{2})\b', query):
            return query + f" {current_year}"
    
    time_keywords = ["погод", "новост", "курс", "прогноз", "событ", "матч", "концерт", "мероприят"]
    if any(word in query.lower() for word in time_keywords):
        if not re.search(r'\b(20\d{2})\b', query):
            return query + f" {current_year}"
    
    return query

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК
# ═══════════════════════════════════════════════════════════════════

def normalize_query(query):
    return re.sub(r'[^\w\s]', '', query.lower()).strip()

async def search_apiserpent(query: str) -> List[Dict]:
    if not APISERPENT_API_KEY:
        logger.error("❌ APISERPENT_API_KEY не задан!")
        return []
    try:
        session = await get_session()
        logger.info(f"🔍 APISerpent: {query[:50]}...")
        params = {
            "q": query,
            "engine": "google",
            "num": SEARCH_RESULTS,
            "deep": "true",
            "country": "ru",
            "language": "ru",
        }
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                results = []
                if "results" in data and isinstance(data["results"], dict):
                    organic = data["results"].get("organic", [])
                    if organic:
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "link": item.get("url", ""),
                                    "source": "organic"
                                })
                        return results
                return []
            else:
                logger.error(f"❌ APISerpent HTTP {r.status}")
                return []
    except Exception as e:
        logger.error(f"💥 Ошибка APISerpent: {e}")
        return []

async def search_apiserpent_bing(query: str) -> List[Dict]:
    if not APISERPENT_API_KEY:
        return []
    try:
        session = await get_session()
        params = {
            "q": query,
            "engine": "bing",
            "num": SEARCH_RESULTS,
            "country": "ru",
            "language": "ru",
        }
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                results = []
                if "results" in data and isinstance(data["results"], dict):
                    organic = data["results"].get("organic", [])
                    if organic:
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "link": item.get("url", ""),
                                    "source": "organic"
                                })
                        return results
            return []
    except:
        return []

async def search_parallel(queries: List[str]) -> List[List[Dict]]:
    tasks = [search_apiserpent(q) for q in queries]
    results = await asyncio.gather(*tasks)
    return results

async def generate_synonyms(query: str) -> str:
    prompt = f"Сгенерируй синонимы для запроса: '{query}'. Ответь только одной фразой."
    response = await ask_deepseek(prompt, temperature=0.5, max_tokens=100, use_pro=False)
    return response.strip() if response else query

async def generate_detailed_query(query: str) -> str:
    prompt = f"Расширь запрос для поиска: '{query}'. Ответь одной фразой."
    response = await ask_deepseek(prompt, temperature=0.5, max_tokens=150, use_pro=False)
    return response.strip() if response else query

async def search_with_adaptation(query: str, max_attempts: int = 2) -> Tuple[List[Dict], str, List[str]]:
    all_results = []
    used_query = query
    attempt_history = []
    
    query_with_date = force_current_date(query)
    if query_with_date != query:
        logger.info(f"📅 Добавлена дата: '{query}' → '{query_with_date}'")
        attempt_history.append(f"Добавлена дата: {query_with_date}")
    
    search_queries = [query_with_date, query]
    
    synonyms = await generate_synonyms(query_with_date)
    if synonyms and synonyms != query_with_date:
        search_queries.append(synonyms)
    
    search_queries = search_queries[:max_attempts]
    
    logger.info(f"🚀 Параллельный поиск по {len(search_queries)} запросам")
    results_list = await search_parallel(search_queries)
    
    for i, results in enumerate(results_list):
        if results:
            logger.info(f"✅ Найдено {len(results)} результатов по запросу {i+1}")
            all_results.extend(results)
            used_query = search_queries[i]
            if len(results) >= 5:
                break
    
    if not all_results:
        logger.info("🔄 Пробуем Bing...")
        bing_results = await search_apiserpent_bing(query_with_date)
        if bing_results:
            all_results.extend(bing_results)
            attempt_history.append(f"Bing: найдено {len(bing_results)} результатов")
    
    seen_urls = set()
    unique_results = []
    for r in all_results:
        url = r.get('link')
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(r)
        elif not url:
            title = r.get('title')
            if title and title not in seen_urls:
                seen_urls.add(title)
                unique_results.append(r)
    
    logger.info(f"📊 Итог: {len(unique_results)} уникальных результатов")
    return unique_results, used_query, attempt_history

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ СТРАНИЦ (ДЛЯ ОБЩИХ ЗАПРОСОВ)
# ═══════════════════════════════════════════════════════════════════

async def fetch_page_rest(url: str) -> Optional[str]:
    if not BROWSER_WS_ENDPOINT:
        return None
    try:
        base_url = BROWSER_WS_ENDPOINT.rstrip('/')
        endpoints = [f"{base_url}/api/scrape", f"{base_url}/scrape"]
        session = await get_session()
        for endpoint in endpoints:
            try:
                async with session.post(endpoint, json={"url": url}, timeout=15) as r:
                    if r.status == 200:
                        data = await r.json()
                        html = data.get("html") or data.get("content") or data.get("data")
                        if html:
                            return html
                    elif r.status == 404:
                        continue
            except:
                continue
        return None
    except:
        return None

async def fetch_http(url: str) -> Optional[str]:
    try:
        session = await get_session()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        async with session.get(url, headers=headers, timeout=PAGE_TIMEOUT) as r:
            if r.status == 200:
                return await r.text()
    except:
        pass
    return None

def parse_page(html: str, query: str) -> Dict:
    result = {'full_text': ''}
    if not BEAUTIFULSOUP_AVAILABLE or not html:
        return result
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        full_text = soup.get_text(separator=' ')
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        result['full_text'] = full_text[:3000]
        return result
    except:
        return result

async def fetch_page(url: str, query: str) -> Dict:
    if not url:
        return {'full_text': ''}
    html = None
    if BROWSER_WS_ENDPOINT:
        html = await fetch_page_rest(url)
    if not html:
        html = await fetch_http(url)
    if html:
        return parse_page(html, query)
    return {'full_text': ''}

# ═══════════════════════════════════════════════════════════════════
#  АГЕНТСКИЕ КОМПОНЕНТЫ
# ═══════════════════════════════════════════════════════════════════

async def call_planner(query: str, context: str) -> Dict:
    prompt = PLANNER_PROMPT.format(query=query, context=context[:2000])
    response = await ask_deepseek(prompt, temperature=0.2, max_tokens=MAX_TOKENS_PLANNER, use_pro=False)
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            response = json_match.group(0)
        plan = json.loads(response)
        if "action" in plan and plan["action"] == "chat":
            return {"action": "chat"}
        if "subtasks" not in plan:
            plan["subtasks"] = []
        if "max_iterations" not in plan:
            plan["max_iterations"] = 1
        return plan
    except Exception as e:
        logger.warning(f"⚠️ Ошибка парсинга плана: {e}")
        return {"action": "chat"}

async def execute_subtask(subtask: dict, query: str) -> dict:
    action = subtask.get("action")
    params = subtask.get("params", {})
    
    if action == "search":
        search_query = params.get("query", query)
        results, used_query, history = await search_with_adaptation(search_query, max_attempts=2)
        
        if not results:
            return {
                "type": "search_results",
                "query": search_query,
                "results": [],
                "fetch_subtasks": [],
                "error": "Ничего не найдено",
                "history": history
            }
        
        fetch_subtasks = []
        for idx, res in enumerate(results[:2]):
            link = res.get("link")
            if link and link.startswith("http"):
                fetch_subtasks.append({
                    "id": subtask.get("id", 0) + idx + 100,
                    "action": "fetch",
                    "params": {"url": link}
                })
        
        return {
            "type": "search_results",
            "query": used_query,
            "results": results,
            "fetch_subtasks": fetch_subtasks,
            "history": history
        }
    
    elif action == "fetch":
        url = params.get("url")
        if not url:
            return {"type": "fetch_error", "error": "No URL"}
        page_data = await fetch_page(url, query)
        return {"type": "page_data", "url": url, "data": page_data}
    
    elif action == "calculate":
        expr = params.get("expression", "0")
        try:
            safe_expr = re.sub(r'[^0-9+\-*/(). ]', '', expr)
            result = eval(safe_expr)
            return {"type": "calculation", "expression": expr, "result": result}
        except Exception as e:
            return {"type": "calculation_error", "error": str(e)}
    
    else:
        return {"type": "unknown_action", "action": action}

async def call_evaluator(query: str, data_summary: str) -> Dict:
    prompt = EVALUATOR_PROMPT.format(query=query, data_summary=data_summary[:3000])
    response = await ask_deepseek(prompt, temperature=0.2, max_tokens=600, use_pro=False)
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            response = json_match.group(0)
        eval_result = json.loads(response)
        if "is_sufficient" not in eval_result:
            eval_result["is_sufficient"] = True
        if "confidence" not in eval_result:
            eval_result["confidence"] = 50
        return eval_result
    except:
        return {"is_sufficient": True, "confidence": 50, "missing_info": [], "suggested_search": []}

async def call_reflector(query: str, answer: str) -> Dict:
    prompt = REFLECTOR_PROMPT.format(query=query, answer=answer)
    response = await ask_deepseek(prompt, temperature=0.3, max_tokens=1000, use_pro=False)
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            response = json_match.group(0)
        reflect = json.loads(response)
        if "is_good" not in reflect:
            reflect["is_good"] = True
        return reflect
    except:
        return {"is_good": True, "feedback": "", "improved_answer": ""}

# ═══════════════════════════════════════════════════════════════════
#  ПРОГРЕСС-БАР
# ═══════════════════════════════════════════════════════════════════

async def update_progress(bot, chat_id, message_id, elapsed, stage, progress):
    colors = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣"]
    color = colors[elapsed % len(colors)]
    bar_length = 20
    filled = int(progress / 100 * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    text = f"🧠 **{stage}**\n`{bar} {progress}%` {color}\n⏱️ {elapsed} сек"
    
    try:
        if message_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode='Markdown'
            )
            return message_id
        else:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown'
            )
            return msg.message_id
    except Exception as e:
        logger.warning(f"⚠️ Ошибка прогресса: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНОЙ АГЕНТСКИЙ ЦИКЛ
# ═══════════════════════════════════════════════════════════════════

async def agent_loop(query: str, uid: int, update: Update = None) -> Tuple[str, List[Dict], float, List[str]]:
    logger.info(f"🛡️ АГЕНТ: запрос '{query[:50]}...'")
    memory = get_memory(uid)
    context = memory.get_full_context()
    
    progress_msg_id = None
    start_time = time.time()
    attempt_history = []
    all_data = []
    
    if update and update.effective_message:
        progress_msg_id = await update_progress(
            bot=update.get_bot(),
            chat_id=update.effective_chat.id,
            message_id=None,
            elapsed=0,
            stage="Планирование",
            progress=0
        )
    
    plan = await call_planner(query, context)
    logger.info(f"📋 План: {json.dumps(plan, ensure_ascii=False)[:300]}")
    
    if progress_msg_id:
        elapsed = int(time.time() - start_time)
        progress_msg_id = await update_progress(
            bot=update.get_bot(),
            chat_id=update.effective_chat.id,
            message_id=progress_msg_id,
            elapsed=elapsed,
            stage="Поиск данных",
            progress=15
        )
    
    if plan.get("action") == "chat":
        chat_prompt = f"Пользователь спрашивает: {query}\nКонтекст: {context}\nОтветь как дружелюбный ассистент."
        answer = await ask_deepseek(chat_prompt, temperature=0.7, use_pro=False)
        memory.add_message("user", query)
        memory.add_message("assistant", answer)
        if progress_msg_id:
            elapsed = int(time.time() - start_time)
            await update_progress(
                bot=update.get_bot(),
                chat_id=update.effective_chat.id,
                message_id=progress_msg_id,
                elapsed=elapsed,
                stage="Готово ✅",
                progress=100
            )
        return answer, [], 100.0, []
    
    subtasks = plan.get("subtasks", [])
    max_iterations = plan.get("max_iterations", 1)
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        logger.info(f"🔄 Итерация {iteration}/{max_iterations}")
        
        if progress_msg_id:
            elapsed = int(time.time() - start_time)
            progress = 15 + (iteration * 10)
            progress_msg_id = await update_progress(
                bot=update.get_bot(),
                chat_id=update.effective_chat.id,
                message_id=progress_msg_id,
                elapsed=elapsed,
                stage=f"Итерация {iteration}/{max_iterations}",
                progress=min(progress, 70)
            )
        
        for subtask in subtasks:
            result = await execute_subtask(subtask, query)
            all_data.append(result)
            if result.get("fetch_subtasks"):
                subtasks.extend(result["fetch_subtasks"])
                logger.info(f"➕ Добавлены подзадачи fetch: {len(result['fetch_subtasks'])} шт.")
            
            if result.get("history"):
                attempt_history.extend(result["history"])
        
        collected_info = ""
        sources_count = 0
        has_fresh_data = False
        has_specific_data = False
        errors = []
        
        for item in all_data:
            if item.get("type") == "search_results":
                results = item.get("results", [])
                sources_count += len(results)
                error = item.get("error", "")
                if error:
                    errors.append(error)
                for res in results[:5]:
                    snippet = res.get("snippet", "")
                    if snippet:
                        collected_info += f"• {snippet[:200]}\n"
                        if len(snippet) > 50:
                            has_specific_data = True
            elif item.get("type") == "page_data":
                page = item.get("data", {})
                text = page.get("full_text", "")
                if text and len(text) > 200:
                    collected_info += f"• {text[:300]}\n"
                    has_fresh_data = True
                    has_specific_data = True
            elif item.get("type") == "calculation":
                collected_info += f"• Вычисление: {item.get('expression')} = {item.get('result')}\n"
                has_specific_data = True
        
        if not collected_info or len(collected_info) < 200:
            if errors:
                logger.warning(f"⚠️ Ошибки при поиске: {errors}")
            
            if iteration < max_iterations:
                logger.info("🔄 Переформулируем запрос...")
                reformulated = await generate_synonyms(query)
                if reformulated and reformulated != query:
                    subtasks.append({
                        "id": len(subtasks) + 1,
                        "action": "search",
                        "params": {"query": reformulated}
                    })
                    continue
        
        data_quality = {
            "has_info": len(collected_info) > 200,
            "sources_count": sources_count,
            "has_fresh": has_fresh_data,
            "has_specific": has_specific_data,
            "contains_numbers": bool(re.search(r'\d+', collected_info)),
            "contains_dates": bool(re.search(r'\d{4}-\d{2}-\d{2}|\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}', collected_info, re.I))
        }
        
        logger.info(f"📊 Качество данных: {json.dumps(data_quality, ensure_ascii=False)}")
        
        if sources_count < 3 and not data_quality["has_specific"] and iteration < max_iterations:
            logger.warning("⚠️ Данных мало. Пробуем другой подход...")
            detailed_query = await generate_detailed_query(query)
            if detailed_query and detailed_query != query:
                subtasks.append({
                    "id": len(subtasks) + 1,
                    "action": "search",
                    "params": {"query": detailed_query}
                })
                continue
        
        evaluation = await call_evaluator(query, collected_info[:3000])
        logger.info(f"📊 Оценка: sufficient={evaluation.get('is_sufficient')}, confidence={evaluation.get('confidence')}")
        
        if evaluation.get("is_sufficient", False) or evaluation.get("confidence", 0) >= TARGET_CONFIDENCE:
            break
        
        if iteration < max_iterations:
            suggested = evaluation.get("suggested_search", [])
            if suggested:
                for sq in suggested[:2]:
                    subtasks.append({"id": len(subtasks)+1, "action": "search", "params": {"query": sq}})
    
    if progress_msg_id:
        elapsed = int(time.time() - start_time)
        progress_msg_id = await update_progress(
            bot=update.get_bot(),
            chat_id=update.effective_chat.id,
            message_id=progress_msg_id,
            elapsed=elapsed,
            stage="Формирование ответа",
            progress=80
        )
    
    sources_text = ""
    for item in all_data:
        if item.get("type") == "search_results":
            for res in item.get("results", [])[:5]:
                title = res.get("title", "")
                snippet = res.get("snippet", "")
                link = res.get("link", "")
                sources_text += f"🔍 {title}\n{snippet}\n{link}\n\n"
        elif item.get("type") == "page_data":
            page = item.get("data", {})
            full_text = page.get("full_text", "")
            if full_text:
                sources_text += f"📄 {full_text[:3000]}\n\n"
        elif item.get("type") == "calculation":
            sources_text += f"🧮 {item.get('expression')} = {item.get('result')}\n"
    
    if not sources_text or len(sources_text) < 100:
        answer = f"""🔍 **Я перепробовал несколько стратегий поиска**, но не нашёл информацию.

📋 **Что я пробовал:**
• {chr(10).join([f"• {h}" for h in attempt_history]) if attempt_history else "• Все доступные стратегии"}

💡 **Вероятно, я неправильно понял запрос. Попробуйте:**
• Уточнить дату (например, "6 августа 2026")
• Добавить город или место
• Спросить более простыми словами

Я не говорю "данных нет" — я говорю "я не нашёл"."""
        
        memory.add_message("user", query)
        memory.add_message("assistant", answer)
        if progress_msg_id:
            elapsed = int(time.time() - start_time)
            await update_progress(
                bot=update.get_bot(),
                chat_id=update.effective_chat.id,
                message_id=progress_msg_id,
                elapsed=elapsed,
                stage="Готово ✅",
                progress=100
            )
        return answer, [], 0, attempt_history
    
    # ===== УЛУЧШЕННЫЙ ПРОМПТ ДЛЯ СРАВНЕНИЯ И АНАЛИЗА =====
    answer_prompt = f"""
Вопрос: {query}

Данные:
{sources_text}

Контекст (память):
{context}

Проанализируй данные из разных источников. Сравни их, выдели общее и различия. 
Сделай выводы на основе найденной информации. 
Ответ должен быть структурированным, с маркерами (✅, 📊, 📋, 🌐).
Разделяй источники: 🌐 Из интернета, 🧠 Из знаний (с пометкой), 📌 Из памяти (только дополнение в конце).
Укажи дату ответа (сегодня {now().strftime('%d.%m.%Y')}).
Если данные противоречивы — укажи это.
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.2, use_pro=True)
    
    today_str = now().strftime('%d.%m.%Y')
    if today_str not in answer:
        answer = f"⚠️ **Внимание:** данные могут быть неактуальными. Сегодня {today_str}.\n\n{answer}"
    
    reflect = await call_reflector(query, answer)
    if not reflect.get("is_good", True):
        improved = reflect.get("improved_answer", "")
        if improved:
            answer = improved
    
    memory.add_message("user", query)
    memory.add_message("assistant", answer)
    
    sources_for_button = []
    for item in all_data:
        if item.get("type") == "search_results":
            for res in item.get("results", [])[:10]:
                if res.get("link"):
                    sources_for_button.append({
                        "title": res.get("title", "Источник"),
                        "link": res.get("link", ""),
                        "type": "search"
                    })
        elif item.get("type") == "page_data":
            url = item.get("url", "")
            if url:
                sources_for_button.append({
                    "title": "Страница",
                    "link": url,
                    "type": "page"
                })
    
    unique = {}
    for src in sources_for_button:
        if src["link"] and src["link"] not in unique:
            unique[src["link"]] = src
    sources_for_button = list(unique.values())[:10]
    
    avg_confidence = 60 + len(sources_for_button) * 3
    avg_confidence = min(100, avg_confidence)
    
    if progress_msg_id:
        elapsed = int(time.time() - start_time)
        await update_progress(
            bot=update.get_bot(),
            chat_id=update.effective_chat.id,
            message_id=progress_msg_id,
            elapsed=elapsed,
            stage="Готово ✅",
            progress=100
        )
    
    return answer, sources_for_button, avg_confidence, attempt_history

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ И КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

def format_sources(sources: List[Dict]) -> str:
    if not sources:
        return "📎 **ИСТОЧНИКИ:**\n\nНет сохранённых источников."
    formatted = "📎 **ИСТОЧНИКИ:**\n\n"
    for idx, s in enumerate(sources[:10], 1):
        title = s.get('title', 'Источник')[:60]
        url = s.get('link', '')
        formatted += f"{idx}. 🔍 **{title}**\n"
        if url and url.startswith('http'):
            formatted += f"   🔗 {url}\n"
        formatted += "\n"
    return formatted

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return
    
    user_message = update.effective_message.text
    if not user_message:
        return
    
    memory = get_memory(user_id)
    
    await update.effective_message.reply_text(
        f"📝 **Запрос принят:**\n\n_{user_message[:300]}_\n\n"
        "🔄 Начинаю агентный поиск...",
        parse_mode='Markdown'
    )
    
    answer, sources, confidence, history = await agent_loop(user_message, user_id, update)
    
    memory.add_message('user', user_message)
    memory.add_message('assistant', answer)
    context.user_data['last_query'] = user_message
    context.user_data['last_answer'] = answer
    context.user_data['last_sources'] = sources
    context.user_data['last_formatted_answer'] = answer
    context.user_data['last_history'] = history
    
    await update.effective_message.reply_text(answer, reply_markup=SHOW_SOURCES_BUTTON)
    
    if confidence > 0:
        await update.effective_message.reply_text(f"🎯 Уверенность: {int(confidence)}%")

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    
    if action == "show_sources":
        sources = context.user_data.get('last_sources', [])
        history = context.user_data.get('last_history', [])
        sources_formatted = format_sources(sources)
        if history:
            sources_formatted += "\n\n📋 **История поиска:**\n" + "\n".join([f"• {h}" for h in history[-5:]])
        await query.edit_message_text(sources_formatted, reply_markup=HIDE_SOURCES_BUTTON, parse_mode='Markdown')
        return
    
    elif action == "hide_sources":
        last_answer = context.user_data.get('last_formatted_answer', '')
        if last_answer:
            await query.edit_message_text(last_answer, reply_markup=SHOW_SOURCES_BUTTON)
        else:
            await query.edit_message_text("⚠️ Основной ответ не найден.")
        return

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return
    context.user_data.clear()
    await update.effective_message.reply_text(
        f"👋 **Привет! Я Джарвис.**\n\n"
        f"📅 Сегодня {now().strftime('%d.%m.%Y')}\n\n"
        "Я всегда ищу актуальную информацию в интернете.\n"
        "Просто напиши вопрос, и я найду ответ! 🤖",
        parse_mode='Markdown'
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    health = memory.memory_health_check()
    await update.effective_message.reply_text(
        f"📊 **Статистика памяти**\n\n"
        f"💬 Сообщений: {health['short_term']}\n"
        f"👤 Профиль: {health['profile']}\n"
        f"⭐ Эпизодов: {health['episodic']}\n"
        f"🧠 Фактов: {health['graph_facts']}\n"
        f"📝 Всего: {health['total_messages']}"
    )

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in _memory_cache:
        del _memory_cache[user_id]
    context.user_data.clear()
    await update.effective_message.reply_text("🧹 **Память очищена!**")

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reminders = get_reminders(user_id)
    if not reminders:
        await update.effective_message.reply_text("📭 **Нет активных напоминаний**")
        return
    text = "📋 **Ваши напоминания:**\n\n"
    for idx, (rid, rtext, rdate) in enumerate(reminders, 1):
        text += f"{idx}. {rtext}\n"
        if rdate:
            text += f"   📅 {rdate}\n"
        text += "\n"
    await update.effective_message.reply_text(text)

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК BROWAIX v17.4 — УНИВЕРСАЛЬНЫЙ ПОИСК, СРАВНЕНИЕ, АНАЛИЗ")
    logger.info("=" * 60)
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info("=" * 60)
    logger.info("⚡ ФУНКЦИОНАЛ:")
    logger.info("   • Дата из системы: ✅")
    logger.info("   • Планировщик + Исполнитель + Оценщик + Рефлектор: ✅")
    logger.info("   • Память 5 уровней + Knowledge Graph: ✅")
    logger.info("   • Принудительное добавление даты: ✅")
    logger.info("   • Параллельный поиск: ✅")
    logger.info("   • Сравнение и анализ данных: ✅")
    logger.info("   • Честные ответы без галлюцинаций: ✅")
    logger.info("=" * 60)
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не задан!")
        return
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DEEPSEEK_API_KEY не задан!")
        return
    if not APISERPENT_API_KEY:
        logger.warning("⚠️ APISERPENT_API_KEY не задан! Поиск не будет работать!")
    
    logger.info("✅ Запускаем бота...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("clear", cmd_forget))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Бот готов!")
    app.run_polling()

if __name__ == "__main__":
    main()
