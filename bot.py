# ═══════════════════════════════════════════════════════════════════
#  ИНСТРУКЦИЯ ДЛЯ РАЗРАБОТЧИКА (ЧТО ЭТОТ БОТ УМЕЕТ)
#  ЭТОТ СПИСОК — ГЛАВНЫЙ ДОКУМЕНТ. НЕ УДАЛЯТЬ!
# ═══════════════════════════════════════════════════════════════════

"""
🤖 БОТ: BROWAIX — УНИВЕРСАЛЬНЫЙ ПОИСКОВЫЙ АССИСТЕНТ (v10.0)
- БЕЗ СТРИМИНГА (стабильно)
- ПЛАНИРОВЩИК ЗАПРОСОВ (интеллектуальная маршрутизация)
- ПРИОРИТЕТ СВЕЖИХ ДАННЫХ (tbs=qdr:m)
- ЦЕПОЧКА РАССУЖДЕНИЙ
- ЧЕСТНОСТЬ: если данных нет — "не знаю"
- ЛИЧНЫЙ АССИСТЕНТ (напоминания, вычисления)
- БЫСТРО (кэширование, параллельные запросы)
"""

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
import sqlite3
from datetime import datetime, timedelta
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
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

# Таймауты и лимиты
PAGE_TIMEOUT = 5
SEARCH_RESULTS = 12
DEEPSEEK_MODEL_FLASH = "deepseek-v4-flash"
DEEPSEEK_MODEL_PRO = "deepseek-v4-pro"
CACHE_TTL = 600                # 10 минут
ANSWER_CACHE_TTL = 3600        # 1 час
APISERPENT_TIMEOUT = 20
MAX_TOKENS_OUTPUT = 6000
MAX_TOKENS_PLANNER = 400
MAX_ITERATIONS = 2
TARGET_CONFIDENCE = 90
EARLY_EXIT_CONFIDENCE = 80
MAX_PAGES_PER_ITERATION = 5
MAX_VARIANTS = 5
BROWSER_TIMEOUT = 5

BROWSER_WS_ENDPOINT = os.getenv("BROWSER_WS_ENDPOINT", "")

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

EXIT_CHAT_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔙 Выйти из беседы", callback_data="action_exit_chat")]
])

SHOW_SOURCES_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("📎 Показать источники", callback_data="show_sources")]
])

HIDE_SOURCES_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔒 Скрыть источники", callback_data="hide_sources")]
])

ACTION_WITH_SOURCES_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔍 Поиск", callback_data="action_search"),
        InlineKeyboardButton("📝 Уточнить", callback_data="action_clarify"),
    ],
    [
        InlineKeyboardButton("💬 Беседа", callback_data="action_chat"),
        InlineKeyboardButton("📎 Показать источники", callback_data="show_sources"),
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
#  DEEPSEEK (БЕЗ СТРИМИНГА, С ПРОВЕРКОЙ АКТУАЛЬНОСТИ)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_answer_quality(answer: str, min_length: int = 400) -> Tuple[bool, str]:
    if not answer:
        return False, "Ответ пустой"
    if len(answer) < min_length:
        return False, f"Ответ слишком короткий ({len(answer)} символов, нужно {min_length})"
    
    # Запрещённые фразы, указывающие на выдумку или неуверенность
    forbidden = [
        "нет доступа", "не могу найти", "нет интернета",
        "я не могу", "нет информации", "не знаю", "не удалось",
        "по моему мнению", "я считаю", "я думаю", "на мой взгляд",
        "возможно", "вероятно", "скорее всего",
        "примерно", "около", "приблизительно",
        "как мне кажется", "наверное"
    ]
    for phrase in forbidden:
        if phrase in answer.lower():
            return False, f"Обнаружена запрещённая фраза: '{phrase}'"
    
    # Проверка на наличие даты или явного указания источника
    date_pattern = r'\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b'
    if not re.search(date_pattern, answer, re.I) and not re.search(r'\d{4}-\d{2}-\d{2}', answer):
        # Если нет даты, проверяем наличие ссылки или упоминания источника
        if not ("http" in answer or "источник" in answer.lower() or "source" in answer.lower()):
            return False, "В ответе отсутствует указание на дату или источник"
    
    # Проверка на наличие разделов "из интернета" или "из знаний"
    if not ("🌐" in answer or "🧠" in answer or "✅" in answer):
        return False, "Ответ не структурирован (нет маркеров)"
    
    return True, "OK"

async def ask_deepseek(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = MAX_TOKENS_OUTPUT,
    use_pro: bool = True,
    system_prompt: str = None
) -> str:
    """Получение полного ответа от DeepSeek (без стриминга)."""
    # Если системный промпт не задан, используем универсальный (без жёстких запретов)
    if system_prompt is None:
        system_prompt = """
Ты — **Джарвис**, персональный ИИ-ассистент. Ты умён, аналитичен и всегда стремишься дать максимально полезный ответ.

Твои принципы:
1. **Приоритет свежих данных** — если вопрос требует актуальной информации, ты используешь только те данные, которые были найдены в интернете в текущем сеансе. Ты не полагаешься на свою внутреннюю память, если она может быть устаревшей.
2. **Честность** — если данных нет или ты не уверен, ты говоришь: "Я не знаю" или "В найденных данных нет информации".
3. **Глубокий анализ** — ты не просто выдаёшь факты, а структурируешь их, сравниваешь, делаешь выводы.
4. **Ясность** — твои ответы чёткие, с маркерами (🌐 — из интернета, 🧠 — из знаний, ✅ — вывод).
5. **Актуальность** — ты всегда указываешь дату источника или дату, к которой относятся данные. Если данные могут быть устаревшими, ты предупреждаешь об этом.

Ты работаешь как личный помощник, помогаешь в планировании, анализе, поиске информации.
"""
    
    key = cache_key(prompt + system_prompt)
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        is_valid, _ = check_answer_quality(cached, min_length=200)
        if is_valid:
            logger.info("♻️ Ответ DeepSeek из кэша")
            return cached
        else:
            del answer_cache[key]

    model = DEEPSEEK_MODEL_PRO if use_pro else DEEPSEEK_MODEL_FLASH
    logger.info(f"🧠 DeepSeek (полный ответ, {model})")
    logger.debug(f"📝 Промпт (первые 300): {prompt[:300]}...")

    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
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
                            # Проверка качества
                            is_valid, reason = check_answer_quality(content, min_length=300)
                            if is_valid:
                                answer_cache[key] = {'data': content, 'time': time.time()}
                                logger.info(f"✅ Ответ получен, длина {len(content)} символов")
                                return content
                            else:
                                logger.warning(f"⚠️ Ответ не прошёл проверку: {reason}. Повторная попытка...")
                                if attempt < 2:
                                    temperature = 0.3
                                    continue
                                else:
                                    return f"⚠️ Не удалось сгенерировать качественный ответ. Причина: {reason}"
                    else:
                        logger.warning(f"⚠️ Неожиданный ответ DeepSeek: {data}")
                else:
                    logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: HTTP {r.status}")
                    if attempt == 2 and r.status == 429:
                        await asyncio.sleep(5)
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ DeepSeek таймаут попытка {attempt+1}")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek ошибка попытка {attempt+1}: {e}")
        if attempt < 2:
            await asyncio.sleep(1 + attempt * 2)
    
    return "⚠️ Не удалось получить ответ от DeepSeek."

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ) — С УВЕЛИЧЕННЫМИ ЛИМИТАМИ
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
        if len(self.short_term) > 200:
            old = self.short_term[:-200]
            self._compress(old)
            self.short_term = self.short_term[-200:]
        self.counter += 1
        self._extract_personal_info(content)
        self._extract_preferences(content)
        self._update_knowledge_graph(content)
        self.save()
    
    def _compress(self, messages):
        important_keywords = ['это', 'является', 'состоит', 'находится', 'важно', 'главное', 'ключевой']
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
        if len(self.episodic) > 500:
            self.episodic = self.episodic[-500:]
    
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
#  НАПОМИНАНИЯ (SQLite)
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
#  ИНТЕЛЛЕКТУАЛЬНЫЙ ПЛАНИРОВЩИК ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════

async def plan_query(query: str, memory: SuperMemory) -> Dict[str, Any]:
    """
    Анализирует запрос, определяет тип, сущности, нужен ли поиск.
    Возвращает структурированный план.
    """
    context = memory.get_full_context()
    planner_prompt = f"""
Ты — планировщик запросов. Проанализируй запрос пользователя и определи, что нужно сделать.

Контекст (предыдущие сообщения, профиль):
{context}

Запрос: {query}

Ответь строго в формате JSON:
{{
  "type": "fact|comparison|instruction|opinion|calculation|reminder|other",
  "entities": ["сущность1", "сущность2"],
  "aspects": ["аспект1", "аспект2"] (если сравнение),
  "needs_search": true/false,
  "search_queries": ["вариант поиска 1", "вариант 2"] (если needs_search=true, до 3),
  "requires_calculation": true/false,
  "requires_reminder": true/false,
  "is_personal": true/false (если вопрос касается личных данных пользователя)
}}

Правила:
- Если запрос требует актуальной информации (новости, погода, курсы, события, факты, которые могут измениться) → needs_search=true.
- Если запрос личный (как дела, напомни, что я говорил) → needs_search=false.
- Если запрос требует вычислений (сколько будет 2+2) → requires_calculation=true.
- Если пользователь просит напомнить о чём-то → requires_reminder=true.

Отвечай только JSON, без пояснений.
"""
    response = await ask_deepseek(planner_prompt, temperature=0.1, max_tokens=MAX_TOKENS_PLANNER, use_pro=False)
    try:
        # Извлекаем JSON
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)
        else:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                response = json_match.group(0)
        plan = json.loads(response)
        # Заполняем дефолты
        for key in ['type', 'needs_search', 'requires_calculation', 'requires_reminder', 'is_personal']:
            if key not in plan:
                plan[key] = False if key != 'type' else 'other'
        if 'search_queries' not in plan or not plan['search_queries']:
            plan['search_queries'] = [query]
        return plan
    except Exception as e:
        logger.warning(f"⚠️ Ошибка парсинга плана: {e}. Ответ: {response[:200]}")
        # fallback
        return {
            "type": "other",
            "entities": [],
            "aspects": [],
            "needs_search": True,
            "search_queries": [query],
            "requires_calculation": False,
            "requires_reminder": False,
            "is_personal": False
        }

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК (APISerpent) — С ПАРАМЕТРОМ tbs=qdr:m ДЛЯ СВЕЖЕСТИ
# ═══════════════════════════════════════════════════════════════════

def normalize_query(query):
    return re.sub(r'[^\w\s]', '', query.lower()).strip()

async def search_apiserpent(query: str, fresh: bool = True) -> List[Dict]:
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
        # Добавляем параметр для свежих результатов (последний месяц)
        if fresh:
            params["tbs"] = "qdr:m"  # last month
            logger.info("📅 Поиск только за последний месяц (tbs=qdr:m)")
        
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            logger.info(f"📡 APISerpent статус: {r.status}")
            if r.status == 200:
                data = await r.json()
                results = []
                if "results" in data and isinstance(data["results"], dict):
                    organic = data["results"].get("organic", [])
                    if organic:
                        logger.info(f"✅ Найдено {len(organic)} результатов в results.organic")
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", "") or item.get("name", ""),
                                    "snippet": item.get("snippet", "") or item.get("description", "") or item.get("text", ""),
                                    "link": item.get("url", "") or item.get("link", ""),
                                    "source": "organic"
                                })
                        return results
                # Поиск в блоках
                for block in ["ai_overview", "featured_snippet", "people_also_ask"]:
                    if block in data.get("results", {}):
                        block_data = data["results"][block]
                        if isinstance(block_data, dict):
                            snippet = block_data.get("snippet") or block_data.get("answer") or ""
                            if snippet:
                                results.append({
                                    "title": block_data.get("title", block),
                                    "snippet": snippet,
                                    "link": block_data.get("url", ""),
                                    "source": block
                                })
                                return results
                return []
            else:
                logger.error(f"❌ APISerpent HTTP {r.status}")
                return []
    except asyncio.TimeoutError:
        logger.error(f"⏰ Таймаут APISerpent (Google). Пробуем Bing...")
        try:
            params_fallback = {
                "q": query,
                "engine": "bing",
                "num": SEARCH_RESULTS,
                "country": "ru",
                "language": "ru",
            }
            if fresh:
                params_fallback["tbs"] = "qdr:m"
            async with session.get(
                "https://apiserpent.com/api/search",
                params=params_fallback,
                headers={"X-API-Key": APISERPENT_API_KEY},
                timeout=15
            ) as r2:
                if r2.status == 200:
                    data = await r2.json()
                    results = []
                    if "results" in data and isinstance(data["results"], dict):
                        organic = data["results"].get("organic", [])
                        if organic:
                            for item in organic:
                                if isinstance(item, dict):
                                    results.append({
                                        "title": item.get("title", "") or item.get("name", ""),
                                        "snippet": item.get("snippet", "") or item.get("description", "") or item.get("text", ""),
                                        "link": item.get("url", "") or item.get("link", ""),
                                        "source": "organic"
                                    })
                            return results
                else:
                    logger.error(f"❌ APISerpent (Bing) HTTP {r2.status}")
        except Exception as e2:
            logger.error(f"💥 Ошибка Bing fallback: {e2}")
        return []
    except Exception as e:
        logger.error(f"💥 Ошибка APISerpent: {e}")
        return []

async def search_with_retry(query: str, fresh: bool = True, retries=2) -> List[Dict]:
    norm = normalize_query(query)
    cache_key_full = f"{norm}_{fresh}"
    if cache_key_full in search_cache and (time.time() - search_cache[cache_key_full]['time']) < CACHE_TTL:
        logger.info(f"♻️ Из кэша: {query[:30]}...")
        return search_cache[cache_key_full]['data']
    for attempt in range(retries):
        try:
            results = await search_apiserpent(query, fresh=fresh)
            if results:
                search_cache[cache_key_full] = {'data': results, 'time': time.time()}
                return results
        except Exception as e:
            logger.warning(f"APISerpent попытка {attempt+1} неудачна: {e}")
            if attempt == retries-1:
                return []
            await asyncio.sleep(2 ** attempt)
    return []

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ И ЗАГРУЗКА СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict, query: str) -> bool:
    title = result.get('title', '').lower()
    snippet = result.get('snippet', '').lower()
    url = result.get('link', '').lower()
    spam_words = ['реклама', 'advertisement', 'sponsored', 'promoted']
    if any(w in title or w in snippet for w in spam_words):
        return False
    video_domains = ['youtube.com', 'youtu.be', 'vimeo.com', 'twitch.tv', 'tiktok.com']
    if any(d in url for d in video_domains):
        return False
    if len(snippet) < 50 and not re.search(r'\d', snippet):
        return False
    return True

async def fetch_page_rest(url: str) -> Optional[str]:
    if not BROWSER_WS_ENDPOINT:
        return None
    try:
        base_url = BROWSER_WS_ENDPOINT.rstrip('/')
        endpoints = [f"{base_url}/api/scrape", f"{base_url}/scrape", f"{base_url}/v1/scrape"]
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
                    else:
                        break
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
    result = {'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None,
              'definitions': [], 'key_facts': [], 'metrics': [], 'tables': [],
              'full_text': '', 'json_data': []}
    if not BEAUTIFULSOUP_AVAILABLE or not html:
        return result
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'noscript', 'meta', 'link']):
            tag.decompose()
        full_text = soup.get_text(separator=' ')
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        result['full_text'] = full_text
        result['text'] = full_text[:4000]
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            h = tag.get_text().strip()
            if h and len(h) > 3:
                result['headings'].append(h[:300])
        result['headings'] = result['headings'][:10]
        for tag in soup.find_all(['ul', 'ol']):
            items = []
            for li in tag.find_all('li'):
                li_text = li.get_text().strip()
                if li_text and len(li_text) > 5:
                    items.append(li_text[:500])
            if items:
                result['lists'].append(items)
        result['lists'] = result['lists'][:10]
        for table in soup.find_all('table'):
            table_text = []
            for row in table.find_all('tr'):
                row_text = []
                for cell in row.find_all(['td', 'th']):
                    cell_text = cell.get_text().strip()
                    if cell_text:
                        row_text.append(cell_text[:200])
                if row_text:
                    table_text.append(' | '.join(row_text))
            if table_text:
                result['tables'].append('\n'.join(table_text))
        result['tables'] = result['tables'][:5]
        for script in soup.find_all('script', type=['application/ld+json', 'application/json']):
            try:
                if script.string:
                    data = json.loads(script.string)
                    if isinstance(data, (dict, list)):
                        result['json_data'].append(json.dumps(data, ensure_ascii=False)[:1000])
            except:
                pass
        metric_patterns = [
            r'([-+]?\d{1,4}\s*[°C℃]?)',
            r'([-+]?\d{1,4}\s*м/с|км/ч|mph)',
            r'(\d{3,4}\s*мм рт\. ст\.|гПа|мбар|hPa)',
            r'(\d{1,3}\s*мм|дюйм|in|%)',
            r'(\d{1,4}\s*г|кг|т|lb|oz)',
            r'(\d{1,4}\s*руб|\$|€|₽|USD|EUR)',
            r'(\d{1,4}\s*шт|ед|чел|%|млн|млрд)',
        ]
        metrics = set()
        for pattern in metric_patterns:
            matches = re.findall(pattern, full_text, re.I)
            for m in matches:
                if isinstance(m, tuple):
                    m = ' '.join(m)
                if len(m) > 2:
                    metrics.add(m.strip())
        result['metrics'] = list(metrics)[:30]
        fact_patterns = [
            r'(\d{4})\s*год[ау]?',
            r'(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)',
        ]
        facts = set()
        for pattern in fact_patterns:
            matches = re.findall(pattern, full_text, re.I)
            for m in matches:
                if isinstance(m, tuple):
                    fact = ' '.join(m)
                else:
                    fact = m
                if fact and len(fact) > 3:
                    facts.add(fact)
        result['key_facts'] = list(facts)[:20]
        return result
    except Exception as e:
        logger.debug(f"⚠️ Ошибка парсинга: {e}")
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

async def fetch_pages(links: List[str], query: str) -> List[Dict]:
    if not links:
        return []
    tasks = [fetch_page(link, query) for link in links[:MAX_PAGES_PER_ITERATION]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r.get('full_text') and len(r.get('full_text')) > 200]

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════

async def generate_variants(query: str) -> List[str]:
    variants = [query]
    if MAX_VARIANTS <= 1:
        return variants
    try:
        prompt = f"Сгенерируй {MAX_VARIANTS} разных вариантов поискового запроса для:\n{query}\nОтветь списком, каждый с новой строки. Варианты должны быть разнообразными: синонимы, перефразировки, уточнения."
        response = await ask_deepseek(prompt, temperature=0.3, max_tokens=MAX_TOKENS_PLANNER, use_pro=False)
        if response:
            for line in response.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    clean = re.sub(r'^[\d\s.)-]+', '', line).strip()
                    if clean and len(clean) > 5:
                        variants.append(clean)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка генерации: {e}")
    return list(dict.fromkeys(variants))[:MAX_VARIANTS]

# ═══════════════════════════════════════════════════════════════════
#  РАСЧЁТ УВЕРЕННОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(pages: List[Dict], items_count: int, all_items: List[Dict]) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'consensus': 0}
    if not pages and items_count == 0:
        return confidence
    if pages:
        reliable_sources = 0
        for p in pages[:3]:
            url = p.get('url', '')
            if any(d in url for d in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
                reliable_sources += 1
            elif any(d in url for d in ['.com', '.org', '.net', '.ru']):
                reliable_sources += 0.5
        confidence['source_reliability'] = min(100, (reliable_sources / max(len(pages[:3]), 1)) * 100)
        structure_count = 0
        for p in pages:
            structure_count += len(p.get('lists', [])) + len(p.get('headings', []))
        confidence['data_completeness'] = min(100, structure_count * 10)
    else:
        confidence['source_reliability'] = 0
        confidence['data_completeness'] = 0
    metric_bonus = 0
    date_bonus = 0
    for item in all_items:
        snippet = item.get('snippet', '')
        if re.search(r'\d{1,4}\s*[°C℃%$€₽]', snippet):
            metric_bonus += 1
        if re.search(r'\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)', snippet, re.I):
            date_bonus += 1
    metric_bonus = min(30, metric_bonus * 3)
    date_bonus = min(20, date_bonus * 5)
    data_richness_bonus = min(50, metric_bonus + date_bonus)
    confidence['recency'] = 50
    confidence['consensus'] = 50
    base_overall = int(
        confidence['source_reliability'] * 0.25 +
        confidence['data_completeness'] * 0.20 +
        confidence['recency'] * 0.15 +
        confidence['consensus'] * 0.10
    )
    confidence['overall'] = min(100, base_overall + data_richness_bonus)
    return confidence

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА (С ПЛАНИРОВЩИКОМ, БЕЗ СТРИМИНГА)
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(
    query: str,
    uid: int,
    context_prompt: str = "",
    update: Update = None
) -> Tuple[str, List[Dict], float]:
    logger.info(f"🛡️ ЗАПРОС: {query[:50]}")
    time_start = time.time()
    memory = get_memory(uid)

    # 1. Планирование
    plan = await plan_query(query, memory)
    logger.info(f"📋 План: {json.dumps(plan, ensure_ascii=False)}")

    # 2. Если это напоминание
    if plan.get('requires_reminder'):
        reminder_text = query
        add_reminder(uid, reminder_text)
        answer = f"✅ Напоминание сохранено: «{reminder_text}»"
        memory.add_message('user', query)
        memory.add_message('assistant', answer)
        return answer, [], 100.0

    # 3. Если вычисление
    if plan.get('requires_calculation'):
        try:
            # Разрешаем только арифметику
            safe_expr = re.sub(r'[^0-9+\-*/(). ]', '', query)
            result = eval(safe_expr)
            answer = f"🧮 **Результат вычисления:**\n\n{query} = {result}"
            memory.add_message('user', query)
            memory.add_message('assistant', answer)
            return answer, [], 100.0
        except:
            pass

    # 4. Поиск, если нужен
    all_items = []
    all_results = []
    confidence = 0.0
    pages = []

    if plan.get('needs_search', True):
        search_queries = plan.get('search_queries', [query])
        if not search_queries:
            search_queries = [query]
        # Генерируем дополнительные варианты
        if len(search_queries) < 3:
            extra = await generate_variants(query)
            search_queries.extend(extra)
        search_queries = list(dict.fromkeys(search_queries))[:MAX_VARIANTS]

        # Параллельный поиск с приоритетом свежести
        tasks = [search_with_retry(q, fresh=True) for q in search_queries]
        results_list = await asyncio.gather(*tasks)
        seen_urls = set()
        for rlist in results_list:
            for r in rlist:
                if not is_useful_result(r, query):
                    continue
                url = r.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
                elif not url:
                    title = r.get('title', '')
                    if title and title not in seen_urls:
                        seen_urls.add(title)
                        all_results.append(r)

        logger.info(f"📊 Всего уникальных релевантных: {len(all_results)}")

        # Загрузка страниц
        if all_results:
            links = [r.get('link', '') for r in all_results if r.get('link')]
            pages = await fetch_pages(links, query)
            # Извлечение данных из страниц
            for page in pages:
                full_text = page.get('full_text', '')
                if full_text:
                    all_items.append({
                        'title': '📄 Полный текст страницы',
                        'snippet': full_text[:3000],
                        'source': 'page_full_text'
                    })
                for jdata in page.get('json_data', []):
                    if jdata:
                        all_items.append({
                            'title': '📊 Структурированные данные',
                            'snippet': jdata[:1000],
                            'source': 'json_ld'
                        })
                for h in page.get('headings', []):
                    if h:
                        all_items.append({'title': '📌 Заголовок', 'snippet': h, 'source': 'heading'})
                for lst in page.get('lists', []):
                    for item in lst:
                        if item and len(item) > 10:
                            all_items.append({'title': '📋 Список', 'snippet': item[:300], 'source': 'list'})
                for table in page.get('tables', []):
                    if table:
                        all_items.append({'title': '📊 Таблица', 'snippet': table[:500], 'source': 'table'})
                for metric in page.get('metrics', []):
                    if metric:
                        all_items.append({'title': '🔢 Метрика', 'snippet': metric, 'source': 'metric'})
                for fact in page.get('key_facts', []):
                    if fact:
                        all_items.append({'title': '📅 Факт', 'snippet': fact, 'source': 'fact'})

        # Расчёт уверенности
        confidence_data = calculate_confidence(pages, len(all_items), all_items)
        confidence = confidence_data.get('overall', 0)
        logger.info(f"📊 Уверенность: {confidence:.1f}% (всего элементов: {len(all_items)})")

    # 5. Формирование ответа с цепочкой рассуждений
    if not all_items and not plan.get('needs_search', False):
        # Если поиск не нужен и данных нет, отвечаем из памяти/знаний
        context = memory.get_full_context()
        prompt = f"""
Пользователь спрашивает: {query}

Контекст диалога и знания:
{context}

Ответь на вопрос, используя только информацию из контекста. Если в контексте нет ответа — скажи "Я не знаю".
Будь вежлив и структурирован.
"""
        answer = await ask_deepseek(prompt, temperature=0.3, use_pro=False)
        memory.add_message('user', query)
        memory.add_message('assistant', answer)
        return answer, [], 100.0

    # 6. Генерация ответа с использованием найденных данных
    if all_items:
        # Собираем текст для промпта
        items_text = ""
        for idx, item in enumerate(all_items[:50], 1):
            title = item.get('title', '')
            snippet = item.get('snippet', '')
            source = item.get('source', '')
            if snippet and snippet != title:
                items_text += f"{idx}. [{source}] {title}: {snippet}\n\n"
            elif title:
                items_text += f"{idx}. [{source}] {title}\n\n"

        # Если данных много, обрезаем до разумного
        if len(items_text) > 8000:
            items_text = items_text[:8000] + "...\n(данные обрезаны для сохранения контекста)"

        # Промпт с цепочкой рассуждений
        answer_prompt = f"""
Ты — Джарвис, аналитический ассистент. Пользователь задал вопрос, и ты получил данные из интернета (свежие, за последний месяц).

**Вопрос пользователя:** {query}

**Данные из интернета:**
{items_text}

**Контекст диалога:**
{context_prompt if context_prompt else "Нет контекста"}

**Твоя задача:**
1. Проанализируй данные и выдели ключевую информацию, прямо относящуюся к вопросу.
2. Если данных достаточно — дай чёткий, структурированный ответ с маркерами:
   - 🌐 **Из интернета** — факты, цифры, даты с указанием источника (если есть).
   - ✅ **Вывод** — твоя интерпретация, сравнение, рекомендация.
3. Если данных недостаточно — честно скажи: "В найденных данных нет полной информации по вашему вопросу. Вот что удалось найти..."
4. Обязательно укажи дату, к которой относятся данные (если она есть в источнике), или дату вашего ответа.
5. Если данные могут быть устаревшими (например, старше 1 месяца) — предупреди об этом.
6. **НЕ ИСПОЛЬЗУЙ свои внутренние знания** — только данные из предоставленного списка.
7. Если данных нет вообще — скажи "Я не знаю" и предложи уточнить запрос.

**Формат ответа:**
Начни с краткого анализа (1-2 предложения), затем структурируй ответ с помощью маркеров 🌐, ✅.
Обязательно разделяй информацию из разных источников.

Напиши ответ.
"""
        answer = await ask_deepseek(answer_prompt, temperature=0.2, use_pro=True)
    else:
        # Если поиск был, но данных нет
        answer = f"🌐 **Из интернета**\n\nК сожалению, по вашему запросу «{query}» не удалось найти свежих данных (за последний месяц).\n\n✅ **Вывод**\nПопробуйте уточнить запрос или изменить формулировку."

    # 7. Проверка актуальности ответа (повторная генерация, если нет даты)
    is_valid, reason = check_answer_quality(answer, min_length=300)
    if not is_valid:
        logger.warning(f"⚠️ Ответ не прошёл проверку: {reason}. Повторная генерация...")
        retry_prompt = f"""
Предыдущий ответ не содержал указания на дату или источник. Пожалуйста, переформулируй ответ, обязательно включив дату источника или дату своего ответа.

Вопрос: {query}
Данные: {items_text[:3000]}

Требования:
- Чётко раздели 🌐 из интернета и ✅ вывод.
- Укажи дату, когда были опубликованы данные (если есть) или дату твоего ответа.
- Если данных нет — скажи "не знаю".
"""
        answer = await ask_deepseek(retry_prompt, temperature=0.2, use_pro=True)

    # 8. Сохраняем в память
    memory.add_message('user', query)
    memory.add_message('assistant', answer)

    total_time = time.time() - time_start
    logger.info(f"⏱️ ОБЩЕЕ ВРЕМЯ: {total_time:.2f} сек")
    return answer, all_results, confidence

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ (БЕЗ СТРИМИНГА)
# ═══════════════════════════════════════════════════════════════════

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    user_id = update.effective_user.id
    memory = get_memory(user_id)

    if action == "action_search":
        pending_text = context.user_data.get('pending_text', '')
        if not pending_text:
            await query.edit_message_text("⚠️ Сначала напишите вопрос в чат.", reply_markup=ACTION_BUTTONS)
            return
        context.user_data['awaiting_input'] = False
        await query.edit_message_text("🔍 Начинаю поиск и анализ данных...")
        context_text = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(
            pending_text, user_id, context_text, update
        )
        context.user_data['last_query'] = pending_text
        context.user_data['last_answer'] = answer
        context.user_data['pending_text'] = ''
        context.user_data['last_sources'] = sources[:10]
        context.user_data['last_formatted_answer'] = answer
        # Отправляем ответ
        await update.effective_message.reply_text(
            answer,
            reply_markup=ACTION_WITH_SOURCES_BUTTONS,
            parse_mode='Markdown'
        )

    elif action == "action_clarify":
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            await query.edit_message_text("⚠️ Нет активного запроса для уточнения.", reply_markup=ACTION_BUTTONS)
            return
        context.user_data['mode'] = 'clarify'
        context.user_data['awaiting_input'] = True
        context.user_data['pending_text'] = ''
        await query.edit_message_text(
            f"📝 **Уточните запрос**\n\nПредыдущий запрос: *{last_query[:200]}*\n\nНапишите ваше уточнение:",
            parse_mode='Markdown'
        )

    elif action == "action_chat":
        pending_text = context.user_data.get('pending_text', '')
        if not pending_text:
            await query.edit_message_text("⚠️ Сначала напишите сообщение в чат.", reply_markup=ACTION_BUTTONS)
            return
        context.user_data['mode'] = 'chat'
        context.user_data['awaiting_input'] = False
        context.user_data['pending_text'] = ''
        full_context = memory.get_full_context()
        chat_prompt = f"""
Пользователь хочет просто пообщаться: {pending_text}

Контекст: {full_context}

Ответь естественно, дружелюбно, но если вопрос фактологический — скажи, что не знаешь, и предложи поискать.
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.7, max_tokens=2000, use_pro=False)
        await update.effective_message.reply_text(
            f"💬 {answer}",
            reply_markup=EXIT_CHAT_BUTTON
        )
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)

    elif action == "action_exit_chat":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False
        await query.edit_message_text(
            "🔍 **Выход из режима беседы**\n\nТеперь я снова ищу информацию в интернете.",
            reply_markup=ACTION_BUTTONS
        )

    elif action == "show_sources":
        sources = context.user_data.get('last_sources', [])
        if not sources:
            await query.edit_message_text("📎 **ИСТОЧНИКИ:**\n\nНет сохранённых источников.", reply_markup=HIDE_SOURCES_BUTTON)
            return
        sources_formatted = "📎 **ИСТОЧНИКИ:**\n\n"
        for idx, s in enumerate(sources[:10], 1):
            title = s.get('title', 'Источник')[:60]
            url = s.get('link', '')
            sources_formatted += f"{idx}. **{title}**\n"
            if url:
                sources_formatted += f"   🔗 {url}\n"
            sources_formatted += "\n"
        await query.edit_message_text(sources_formatted, reply_markup=HIDE_SOURCES_BUTTON, parse_mode='Markdown')

    elif action == "hide_sources":
        last_answer = context.user_data.get('last_formatted_answer', '')
        if last_answer:
            await query.edit_message_text(last_answer, reply_markup=ACTION_WITH_SOURCES_BUTTONS)
        else:
            await query.edit_message_text("⚠️ Основной ответ не найден.", reply_markup=ACTION_BUTTONS)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        return
    user_message = update.effective_message.text
    if not user_message:
        return
    memory = get_memory(user_id)

    if context.user_data.get('mode') == 'chat':
        full_context = memory.get_full_context()
        chat_prompt = f"""
Пользователь: {user_message}

Контекст: {full_context}

Ответь естественно, дружелюбно. Если вопрос требует фактов — скажи, что не знаешь, и предложи поискать.
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.7, max_tokens=2000, use_pro=False)
        await update.effective_message.reply_text(
            f"💬 {answer}",
            reply_markup=EXIT_CHAT_BUTTON
        )
        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)
        return

    if context.user_data.get('mode') == 'clarify':
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            context.user_data['mode'] = 'search'
            await update.effective_message.reply_text("⚠️ Нет активного запроса для уточнения.", reply_markup=ACTION_BUTTONS)
            return
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False
        clarification = user_message
        combined_query = f"{last_query} (уточнение: {clarification})"
        await update.effective_message.reply_text(
            f"📝 **Уточняю запрос...**\n\nИщу с учётом уточнения: *{clarification[:100]}*",
            parse_mode='Markdown'
        )
        context_text = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(
            combined_query, user_id, context_text, update
        )
        memory.add_message('user', f"Уточнение: {clarification}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = combined_query
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources[:10]
        context.user_data['last_formatted_answer'] = answer
        await update.effective_message.reply_text(
            answer,
            reply_markup=ACTION_WITH_SOURCES_BUTTONS,
            parse_mode='Markdown'
        )
        return

    # Обычный режим — сохраняем текст и предлагаем выбор
    context.user_data['pending_text'] = user_message
    context.user_data['awaiting_input'] = True
    await update.effective_message.reply_text(
        f"📝 **Запрос принят:**\n\n_{user_message[:300]}_\n\nВыберите режим работы:",
        reply_markup=ACTION_BUTTONS
    )


# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data['mode'] = 'search'
    await update.effective_message.reply_text(
        "👋 **Привет! Я Джарвис — твой личный ассистент.**\n\n"
        "🔍 Я ищу только свежие данные из интернета (за последний месяц)\n"
        "📊 Анализирую, сравниваю, делаю выводы\n"
        "⚠️ Если не знаю — честно скажу «не знаю»\n"
        "🧠 Запоминаю тебя и учусь\n\n"
        "**Как работает:**\n"
        "1️⃣ Напиши вопрос в чат\n"
        "2️⃣ Выбери действие:\n"
        "   • 🔍 Поиск — найти информацию в интернете (только свежее)\n"
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
        f"💬 Сообщений в памяти: {health['short_term']}\n"
        f"👤 Профиль: {health['profile']} полей\n"
        f"⭐ Фактов в эпизодической памяти: {health['episodic']}\n"
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
        "🧹 **Всё забыто!**\n\nПамять очищена. Начинаем с чистого листа.",
        reply_markup=ACTION_BUTTONS
    )


# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК BROWAIX v10.0 (УМНЫЙ АССИСТЕНТ, БЕЗ СТРИМИНГА)")
    logger.info("=" * 60)
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"   Playwright REST: {'✅' if BROWSER_WS_ENDPOINT else '❌'}")
    logger.info("=" * 60)
    logger.info("⚡ ПАРАМЕТРЫ:")
    logger.info(f"   • Модели: Flash для планирования, Pro для ответа")
    logger.info(f"   • Поиск только за последний месяц (tbs=qdr:m)")
    logger.info(f"   • Цепочка рассуждений включена")
    logger.info(f"   • Проверка актуальности ответа")
    logger.info(f"   • Макс. токенов ответа: {MAX_TOKENS_OUTPUT}")
    logger.info(f"   • Стриминг: ОТКЛЮЧЁН (стабильно)")
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
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ Бот готов!")
    app.run_polling()

if __name__ == "__main__":
    main()
