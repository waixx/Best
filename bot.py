# ═══════════════════════════════════════════════════════════════════
#  БОТ: BROWAIX — АГЕНТНАЯ АРХИТЕКТУРА (v14.0)
#  ПОЛНАЯ ВЕРСИЯ: ПЛАНИРОВЩИК + ИСПОЛНИТЕЛЬ + ОЦЕНЩИК + РЕФЛЕКТОР
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
import traceback
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple, Any, Union
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
#  СИСТЕМНАЯ ИНСТРУКЦИЯ (ЛИЧНОСТЬ)
# ═══════════════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """
Ты — **Джарвис**, персональный ИИ-ассистент. Твоя личность и правила:

1. **Абсолютная честность** — ты никогда не выдумываешь факты, цифры, даты. Если не знаешь — говори честно: «Я не знаю» или «В предоставленных данных нет информации». Никаких «возможно», «вероятно», «по моему мнению».

2. **Разделение источников** — ты чётко разделяешь информацию по происхождению:
   - 🌐 **Из интернета/API** — данные с указанием источника и даты.
   - 🧠 **Из знаний модели** — твои внутренние знания, только если они не противоречат интернет-данным, с пометкой «На основе моих знаний».
   - 📌 **Из памяти** — факты о пользователе.

3. **Актуальность** — ты всегда проверяешь дату данных. Если данные старше 30 дней, а запрос про текущее состояние — предупреждаешь.

4. **Структурированность** — ответы с маркерами (✅, 📊, 📋, 🌐), важное выделено жирным.

5. **Универсальность** — помогаешь в любых вопросах: поиск, сравнение, анализ, планирование, обучение, технические задачи.

6. **Персонализация** — используешь память о пользователе.

7. **Ответственность** — не даёшь вредных советов, добавляешь дисклеймеры при необходимости.

8. **Саморефлексия** — перед ответом кратко формулируешь, что понял из запроса.

9. **Источники** — всегда указываешь, откуда взяты данные.

10. **Ты — партнёр**, цель — сделать жизнь пользователя проще.
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
  "max_iterations": 3
}}

Правила:
- Если запрос простой (приветствие, беседа) — верни {{"action": "chat"}}.
- Для фактологических запросов добавляй подзадачи "search" с вариантами поисковых запросов.
- Если нужно сравнить — добавь подзадачи для каждого объекта.
- Если запрос требует вычислений — добавь "calculate".
- Если данные уже есть в контексте — не добавляй поиск.

Отвечай только JSON, без пояснений.
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
  "suggested_search": ["дополнительные запросы"]  // если данных недостаточно
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
  "feedback": "что нужно исправить, если не good",
  "improved_answer": "исправленный ответ"  // если is_good=false
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
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
CURRENCY_API_KEY = os.getenv("CURRENCY_API_KEY")

ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

PAGE_TIMEOUT = 5
SEARCH_RESULTS = 12
DEEPSEEK_MODEL_FLASH = "deepseek-v4-flash"
DEEPSEEK_MODEL_PRO = "deepseek-v4-pro"
CACHE_TTL = 600
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 30
MAX_TOKENS_OUTPUT = 8000
MAX_TOKENS_PLANNER = 600
MAX_ITERATIONS = 3
TARGET_CONFIDENCE = 90
EARLY_EXIT_CONFIDENCE = 80
MAX_PAGES_PER_ITERATION = 5
MAX_VARIANTS = 3
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
#  DEEPSEEK (БЕЗ СТРИМИНГА)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_answer_quality(answer: str, min_length: int = 200) -> Tuple[bool, str]:
    """Проверка качества ответа — теперь не блокирует честные фразы."""
    if not answer:
        return False, "Ответ пустой"
    if len(answer) < min_length:
        return False, f"Ответ слишком короткий ({len(answer)} символов, нужно {min_length})"
    
    # Запрещены только явные выдумки и субъективные оценки
    forbidden = [
        "по моему мнению", "я считаю", "я думаю", "на мой взгляд",
        "я предполагаю", "мне кажется"
    ]
    for phrase in forbidden:
        if phrase in answer.lower():
            return False, f"Обнаружена запрещённая фраза: '{phrase}'"
    
    # Проверка на дату/источник — не блокирует, только логирует
    date_pattern = r'\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b'
    has_date = bool(re.search(date_pattern, answer, re.I)) or bool(re.search(r'\d{4}-\d{2}-\d{2}', answer))
    has_source = "http" in answer or "источник" in answer.lower() or "из знаний" in answer.lower()
    if not has_date and not has_source:
        logger.info("ℹ️ Ответ не содержит даты или источника — допустимо")
    
    return True, "OK"

async def ask_deepseek(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = MAX_TOKENS_OUTPUT,
    use_pro: bool = True,
    system_override: Optional[str] = None
) -> str:
    """Универсальный вызов DeepSeek с возможностью переопределить системную инструкцию."""
    system = system_override if system_override else SYSTEM_INSTRUCTION
    key = cache_key(prompt + system)
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        is_valid, _ = check_answer_quality(cached, min_length=150)
        if is_valid:
            logger.info("♻️ Ответ DeepSeek из кэша")
            return cached
        else:
            del answer_cache[key]

    model = DEEPSEEK_MODEL_PRO if use_pro else DEEPSEEK_MODEL_FLASH
    logger.info(f"🧠 DeepSeek (полный ответ, {model})")

    for attempt in range(2):  # максимум 2 попытки
        try:
            session = await get_session()
            payload = {
                "model": model,
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
                                logger.info(f"✅ Ответ получен, длина {len(content)} символов")
                                return content
                            else:
                                logger.warning(f"⚠️ Ответ не прошёл проверку: {reason}")
                                if attempt < 1:
                                    continue
                                else:
                                    return f"⚠️ Качество ответа низкое: {reason}"
                    else:
                        logger.warning(f"⚠️ Неожиданный ответ DeepSeek: {data}")
                else:
                    logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: HTTP {r.status}")
                    if attempt == 1 and r.status == 429:
                        await asyncio.sleep(5)
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ DeepSeek таймаут попытка {attempt+1}")
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek ошибка попытка {attempt+1}: {e}")
        if attempt < 1:
            await asyncio.sleep(1 + attempt * 2)
    
    return "⚠️ Не удалось получить ответ от DeepSeek."

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ) — ПОЛНОСТЬЮ
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
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ПОИСК, ПАРСИНГ, ВАЛИДАЦИЯ)
# ═══════════════════════════════════════════════════════════════════

def validate_data(data: Dict, query: str, source_type: str = "search") -> Tuple[bool, float, str]:
    """Универсальная валидация данных (гибкая)."""
    score = 0
    reasons = []
    text = data.get('text', '') or data.get('snippet', '') or data.get('full_text', '')
    if not text:
        if source_type == 'api' and isinstance(data, dict):
            if 'weather' in query.lower() or 'погод' in query.lower():
                if 'temperature' in data or 'temp' in data:
                    score += 40
                else:
                    reasons.append("Нет температуры")
            elif 'курс' in query.lower() or 'currency' in query.lower():
                if 'rate' in data or 'price' in data:
                    score += 40
                else:
                    reasons.append("Нет курса")
            else:
                if len(data.keys()) > 1:
                    score += 30
                else:
                    reasons.append("Пустой API-ответ")
        else:
            reasons.append("Нет текста")
    else:
        if len(text) > 100:
            score += 25
        else:
            reasons.append("Слишком короткий текст")
            score -= 5

    spam_words = ['реклама', 'спонсор', 'купить', 'заказать', 'скидка', 'promo', 'advertisement']
    spam_count = sum(1 for w in spam_words if w in text.lower())
    if spam_count > 2:
        reasons.append("Обнаружена реклама")
        score -= spam_count * 3
    else:
        score += 10

    # Дата — не штрафуем
    date_patterns = [
        r'\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b',
        r'\d{4}-\d{2}-\d{2}',
        r'\d{2}\.\d{2}\.\d{4}',
        r'\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}'
    ]
    has_date = any(re.search(p, text) for p in date_patterns)
    if has_date:
        score += 15
    else:
        reasons.append("Нет даты (не критично)")

    if re.search(r'^#{1,3}\s+\w', text, re.M) or re.search(r'^\s*[-*•]\s+\w', text, re.M):
        score += 10
    if re.search(r'\b\d+\b', text):
        score += 10

    query_words = set(re.findall(r'\w+', query.lower()))
    text_words = set(re.findall(r'\w+', text.lower()))
    overlap = len(query_words & text_words)
    if overlap >= 3:
        score += 20
    elif overlap >= 1:
        score += 10
    else:
        reasons.append("Нет пересечения с запросом")
        score -= 5

    final_score = min(100, max(0, score))
    is_valid = final_score >= 45
    reason_str = "; ".join(reasons) if reasons else "OK"
    logger.info(f"Валидация: оценка {final_score}, валидно: {is_valid}, причины: {reason_str}")
    return is_valid, final_score, reason_str

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
            logger.info(f"📡 APISerpent статус: {r.status}")
            if r.status == 200:
                data = await r.json()
                results = []
                if "results" in data and isinstance(data["results"], dict):
                    organic = data["results"].get("organic", [])
                    if organic:
                        logger.info(f"✅ Найдено {len(organic)} результатов")
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", "") or item.get("name", ""),
                                    "snippet": item.get("snippet", "") or item.get("description", "") or item.get("text", ""),
                                    "link": item.get("url", "") or item.get("link", ""),
                                    "source": "organic"
                                })
                        return results
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
        logger.error(f"⏰ Таймаут APISerpent. Пробуем Bing...")
        try:
            params_fallback = {
                "q": query,
                "engine": "bing",
                "num": SEARCH_RESULTS,
                "country": "ru",
                "language": "ru",
            }
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

async def search_with_retry(query: str, retries=2) -> List[Dict]:
    norm = normalize_query(query)
    if norm in search_cache and (time.time() - search_cache[norm]['time']) < CACHE_TTL:
        logger.info(f"♻️ Из кэша: {query[:30]}...")
        return search_cache[norm]['data']
    for attempt in range(retries):
        try:
            results = await search_apiserpent(query)
            if results:
                search_cache[norm] = {'data': results, 'time': time.time()}
                return results
        except Exception as e:
            logger.warning(f"APISerpent попытка {attempt+1} неудачна: {e}")
            if attempt == retries-1:
                return []
            await asyncio.sleep(2 ** attempt)
    return []

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
    result = {
        'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None,
        'definitions': [], 'key_facts': [], 'metrics': [], 'tables': [],
        'full_text': '', 'json_data': []
    }
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
#  АГЕНТСКИЕ КОМПОНЕНТЫ
# ═══════════════════════════════════════════════════════════════════

async def call_planner(query: str, context: str) -> Dict:
    """Вызов Планировщика."""
    prompt = PLANNER_PROMPT.format(query=query, context=context[:2000])
    response = await ask_deepseek(prompt, temperature=0.2, max_tokens=MAX_TOKENS_PLANNER, use_pro=False)
    try:
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)
        else:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                response = json_match.group(0)
        plan = json.loads(response)
        if "action" in plan and plan["action"] == "chat":
            return {"action": "chat"}
        if "subtasks" not in plan:
            plan["subtasks"] = []
        if "max_iterations" not in plan:
            plan["max_iterations"] = 3
        return plan
    except Exception as e:
        logger.warning(f"⚠️ Ошибка парсинга плана: {e}. Ответ: {response[:200]}")
        return {"action": "chat"}  # fallback

async def execute_subtask(subtask: dict, query: str) -> dict:
    """Выполняет одну подзадачу."""
    action = subtask.get("action")
    params = subtask.get("params", {})
    
    if action == "search":
        search_query = params.get("query", query)
        results = await search_with_retry(search_query)
        return {"type": "search_results", "query": search_query, "results": results}
    
    elif action == "fetch":
        url = params.get("url")
        if not url:
            return {"type": "fetch_error", "error": "No URL"}
        page_data = await fetch_page(url, query)
        return {"type": "page_data", "url": url, "data": page_data}
    
    elif action == "analyze":
        # Анализ уже собранных данных (можно пропустить или использовать для извлечения фактов)
        data = params.get("data", [])
        analysis = await analyze_data(data, query)
        return {"type": "analysis", "result": analysis}
    
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

async def analyze_data(data: list, query: str) -> str:
    """Упрощённый анализ данных (может быть использован для извлечения ключевых фактов)."""
    if not data:
        return "Нет данных для анализа."
    text = json.dumps(data, ensure_ascii=False)[:2000]
    prompt = f"Проанализируй следующие данные и выдели ключевые факты, относящиеся к запросу '{query}':\n{text}"
    return await ask_deepseek(prompt, temperature=0.3, max_tokens=1000, use_pro=False)

async def call_evaluator(query: str, data_summary: str) -> Dict:
    """Вызов Оценщика."""
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
    """Вызов Рефлектора."""
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
#  ОСНОВНОЙ АГЕНТСКИЙ ЦИКЛ
# ═══════════════════════════════════════════════════════════════════

async def agent_loop(query: str, uid: int, update: Update = None) -> Tuple[str, List[Dict], float]:
    """
    Главный цикл агента.
    Возвращает (ответ, источники, уверенность).
    """
    logger.info(f"🛡️ АГЕНТ: запрос '{query[:50]}...'")
    memory = get_memory(uid)
    context = memory.get_full_context()
    
    # 1. Планирование
    plan = await call_planner(query, context)
    logger.info(f"📋 План: {json.dumps(plan, ensure_ascii=False)[:300]}")
    
    # Если это беседа — сразу генерируем ответ
    if plan.get("action") == "chat":
        chat_prompt = f"Пользователь спрашивает: {query}\nКонтекст: {context}\nОтветь как дружелюбный ассистент."
        answer = await ask_deepseek(chat_prompt, temperature=0.7, use_pro=False)
        memory.add_message("user", query)
        memory.add_message("assistant", answer)
        return answer, [], 100.0
    
    # 2. Цикл выполнения подзадач
    all_data = []          # список собранных результатов
    subtasks = plan.get("subtasks", [])
    max_iterations = plan.get("max_iterations", 3)
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        logger.info(f"🔄 Итерация {iteration}/{max_iterations}")
        
        # Выполняем все подзадачи
        for subtask in subtasks:
            result = await execute_subtask(subtask, query)
            all_data.append(result)
        
        # Собираем краткую выжимку для оценщика
        data_summary = ""
        for item in all_data:
            if item.get("type") == "search_results":
                results = item.get("results", [])
                data_summary += f"Поиск '{item.get('query')}': найдено {len(results)} результатов. "
            elif item.get("type") == "page_data":
                page = item.get("data", {})
                full_text = page.get("full_text", "")
                if full_text:
                    data_summary += f"Страница {item.get('url')}: {full_text[:500]}... "
            elif item.get("type") == "calculation":
                data_summary += f"Вычисление: {item.get('expression')} = {item.get('result')}. "
        
        # Оценка достаточности данных
        evaluation = await call_evaluator(query, data_summary[:3000])
        logger.info(f"📊 Оценка: sufficient={evaluation.get('is_sufficient')}, confidence={evaluation.get('confidence')}")
        
        if evaluation.get("is_sufficient", False) or evaluation.get("confidence", 0) >= TARGET_CONFIDENCE:
            break
        
        # Если недостаточно — генерируем новые подзадачи
        if iteration < max_iterations:
            suggested = evaluation.get("suggested_search", [])
            if suggested:
                for sq in suggested[:2]:
                    subtasks.append({"id": len(subtasks)+1, "action": "search", "params": {"query": sq}})
                logger.info(f"➕ Добавлены новые поисковые запросы: {suggested[:2]}")
            else:
                # Если нет предложений, просто добавляем общий поиск с переформулировкой
                subtasks.append({"id": len(subtasks)+1, "action": "search", "params": {"query": query + " подробно"}})
    
    # 3. Генерация финального ответа
    # Собираем все данные в читаемый вид
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
                sources_text += f"📄 Содержимое страницы:\n{full_text[:3000]}\n\n"
        elif item.get("type") == "calculation":
            sources_text += f"🧮 {item.get('expression')} = {item.get('result')}\n"
    
    if not sources_text:
        sources_text = "Не удалось собрать данные."
    
    answer_prompt = f"""
Вот данные, собранные по вашему запросу.

Вопрос: {query}

Данные:
{sources_text}

Контекст (память):
{context}

Твоя задача — дать чёткий, структурированный ответ, используя только эти данные.
Разделяй источники:
- 🌐 Из интернета/API
- 🧠 Из знаний модели (только если дополняешь, с пометкой)
- 📌 Из памяти (если есть информация о пользователе)

Если данных недостаточно — скажи честно.
Укажи дату ответа (сегодня {now().strftime('%d.%m.%Y')}).
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.2, use_pro=True)
    
    # 4. Рефлексия
    reflect = await call_reflector(query, answer)
    if not reflect.get("is_good", True):
        logger.info(f"🔄 Рефлексия: требуется улучшение. Причина: {reflect.get('feedback')}")
        improved = reflect.get("improved_answer", "")
        if improved:
            answer = improved
        else:
            # перегенерируем с учётом замечаний
            fix_prompt = f"Предыдущий ответ: {answer}\nЗамечания: {reflect.get('feedback')}\nИсправь ответ, устрани замечания."
            answer = await ask_deepseek(fix_prompt, temperature=0.2, use_pro=True)
    
    # 5. Сохраняем в память
    memory.add_message("user", query)
    memory.add_message("assistant", answer)
    
    # 6. Формируем список источников для кнопки
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
                    "title": f"Страница",
                    "link": url,
                    "type": "page"
                })
    
    # Уникальные источники по ссылке
    unique = {}
    for src in sources_for_button:
        if src["link"] and src["link"] not in unique:
            unique[src["link"]] = src
    sources_for_button = list(unique.values())[:10]
    
    # Уверенность
    avg_confidence = 0
    if all_data:
        # приблизительно
        conf = 60 + len(sources_for_button) * 3
        avg_confidence = min(100, conf)
    else:
        avg_confidence = 0
    
    return answer, sources_for_button, avg_confidence

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ И КОМАНДЫ (без изменений, но с вызовом agent_loop)
# ═══════════════════════════════════════════════════════════════════

def format_sources(sources: List[Dict]) -> str:
    if not sources:
        return "📎 **ИСТОЧНИКИ:**\n\nНет сохранённых источников."
    formatted = "📎 **ИСТОЧНИКИ:**\n\n"
    for idx, s in enumerate(sources[:10], 1):
        title = s.get('title', 'Источник')[:60]
        url = s.get('link', '')
        source_type = s.get('type', '')
        icon = {
            'search': '🔍',
            'page': '📄',
            'weather_api': '🌤️',
            'currency_api': '💱'
        }.get(source_type, '📎')
        formatted += f"{idx}. {icon} **{title}**\n"
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
    
    if context.user_data.get('mode') == 'chat':
        full_context = memory.get_full_context()
        chat_prompt = f"Пользователь спрашивает: {user_message}\nКонтекст: {full_context}\nОтветь как дружелюбный ассистент."
        answer = await ask_deepseek(chat_prompt, temperature=0.7, use_pro=False)
        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)
        await update.effective_message.reply_text(f"💬 {answer}", reply_markup=EXIT_CHAT_BUTTON)
        return
    
    if context.user_data.get('mode') == 'clarify':
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            context.user_data['mode'] = 'search'
            await update.effective_message.reply_text("⚠️ Нет активного запроса для уточнения.", reply_markup=ACTION_BUTTONS)
            return
        context.user_data['mode'] = 'search'
        clarification = user_message
        combined_query = f"{last_query} (уточнение: {clarification})"
        await update.effective_message.reply_text(f"📝 **Уточняю запрос...**\n\nИщу с учётом: *{clarification[:100]}*", parse_mode='Markdown')
        full_context = memory.get_full_context()
        answer, sources, confidence = await agent_loop(combined_query, user_id, update)
        memory.add_message('user', f"Уточнение: {clarification}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = combined_query
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources
        context.user_data['last_formatted_answer'] = answer
        await update.effective_message.reply_text(answer, reply_markup=ACTION_WITH_SOURCES_BUTTONS)
        return
    
    context.user_data['pending_text'] = user_message
    context.user_data['awaiting_input'] = True
    await update.effective_message.reply_text(
        f"📝 **Запрос принят:**\n\n_{user_message[:300]}_\n\nВыберите режим работы:",
        reply_markup=ACTION_BUTTONS,
        parse_mode='Markdown'
    )

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
        await query.edit_message_text("🤖 **Агент анализирует запрос и собирает данные...**")
        full_context = memory.get_full_context()
        answer, sources, confidence = await agent_loop(pending_text, user_id, update)
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = pending_text
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources
        context.user_data['last_formatted_answer'] = answer
        context.user_data['pending_text'] = ''
        await update.effective_message.reply_text(answer, reply_markup=ACTION_WITH_SOURCES_BUTTONS)
        if confidence > 0:
            await update.effective_message.reply_text(f"🎯 Уверенность: {int(confidence)}%")
    
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
        chat_prompt = f"Пользователь хочет поговорить: {pending_text}\nКонтекст: {full_context}\nОтветь как дружелюбный собеседник."
        answer = await ask_deepseek(chat_prompt, temperature=0.7, use_pro=False)
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        await update.effective_message.reply_text(f"💬 {answer}", reply_markup=EXIT_CHAT_BUTTON)
    
    elif action == "action_exit_chat":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False
        await query.edit_message_text("🔍 **Выход из режима беседы**\n\nТеперь я снова ищу информацию в интернете.", reply_markup=ACTION_BUTTONS)
    
    elif action == "show_sources":
        sources = context.user_data.get('last_sources', [])
        sources_formatted = format_sources(sources)
        await query.edit_message_text(sources_formatted, reply_markup=HIDE_SOURCES_BUTTON, parse_mode='Markdown')
    
    elif action == "hide_sources":
        last_answer = context.user_data.get('last_formatted_answer', '')
        if last_answer:
            await query.edit_message_text(last_answer, reply_markup=ACTION_WITH_SOURCES_BUTTONS)
        else:
            await query.edit_message_text("⚠️ Основной ответ не найден.", reply_markup=ACTION_BUTTONS)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return
    context.user_data.clear()
    context.user_data['mode'] = 'search'
    await update.effective_message.reply_text(
        "👋 **Привет! Я Джарвис — агентный ИИ-ассистент.**\n\n"
        "🔍 **Что я умею:**\n"
        "• Самостоятельно планировать поиск\n"
        "• Анализировать данные из нескольких источников\n"
        "• Проверять полноту информации и при необходимости уточнять\n"
        "• Чётко разделять 🌐 интернет, 🧠 знания, 📌 память\n"
        "• Никогда не врать\n\n"
        "**Просто напиши вопрос, и я сам решу, что делать.** 🤖",
        reply_markup=ACTION_BUTTONS,
        parse_mode='Markdown'
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    health = memory.memory_health_check()
    await update.effective_message.reply_text(
        f"📊 **Статистика памяти**\n\n"
        f"💬 Сообщений в краткосрочной памяти: {health['short_term']}\n"
        f"👤 Полей в профиле: {health['profile']}\n"
        f"⭐ Фактов в эпизодической памяти: {health['episodic']}\n"
        f"🧠 Фактов в графе знаний: {health['graph_facts']}\n"
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
            if os.path.exists(path):
                os.remove(path)
        except:
            pass
    context.user_data.clear()
    await update.effective_message.reply_text("🧹 **Память очищена!** Начинаем с чистого листа.", reply_markup=ACTION_BUTTONS)

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reminders = get_reminders(user_id)
    if not reminders:
        await update.effective_message.reply_text("📭 **Нет активных напоминаний**", reply_markup=ACTION_BUTTONS)
        return
    text = "📋 **Ваши напоминания:**\n\n"
    for idx, (rid, rtext, rdate) in enumerate(reminders, 1):
        text += f"{idx}. {rtext}\n"
        if rdate:
            text += f"   📅 {rdate}\n"
        text += "\n"
    await update.effective_message.reply_text(text, reply_markup=ACTION_BUTTONS)

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК BROWAIX v14.0 — АГЕНТНАЯ АРХИТЕКТУРА")
    logger.info("=" * 60)
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"   Weather API: {'✅' if WEATHER_API_KEY else '❌ (опционально)'}")
    logger.info(f"   Currency API: {'✅' if CURRENCY_API_KEY else '❌ (опционально)'}")
    logger.info(f"   Playwright REST: {'✅' if BROWSER_WS_ENDPOINT else '❌ (опционально)'}")
    logger.info("=" * 60)
    logger.info("⚡ ФУНКЦИОНАЛ:")
    logger.info(f"   • Агентский цикл (Планировщик + Исполнитель + Оценщик + Рефлектор): ✅")
    logger.info(f"   • Память 5 уровней: ✅")
    logger.info(f"   • Гибкая валидация: ✅")
    logger.info(f"   • Честные ответы без блокировок: ✅")
    logger.info(f"   • Без стриминга: ✅")
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
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ Бот готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()

# ═══════════════════════════════════════════════════════════════════
#  КОНЕЦ ПОЛНОГО КОДА v14.0
#  ═══════════════════════════════════════════════════════════════════
