# ═══════════════════════════════════════════════════════════════════
#  БОТ: BROWAIX — УМНЫЙ АССИСТЕНТ С РАЗДЕЛЕНИЕМ ИСТОЧНИКОВ
#  Версия 13.0 — ПОЛНАЯ: ИНСТРУКЦИЯ + ПАМЯТЬ + ВАЛИДАЦИЯ + МАРШРУТИЗАЦИЯ
#  НИЧЕГО НЕ ВЫРЕЗАНО — КОД РАЗБИТ НА 5 ЧАСТЕЙ ДЛЯ УДОБСТВА
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

# Попытка импорта BeautifulSoup (опционально)
try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
#  СИСТЕМНАЯ ИНСТРУКЦИЯ (ЛИЧНОСТЬ И ПРАВИЛА)
#  ОБНОВЛЕНА: РАЗДЕЛЕНИЕ ИНТЕРНЕТ / ПАМЯТЬ / ЗНАНИЯ МОДЕЛИ
# ═══════════════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """
Ты — **Джарвис**, персональный ИИ-ассистент. Твоя личность и правила:

1. **Абсолютная честность** — ты никогда не выдумываешь факты, цифры, даты. Если не знаешь — говори честно: «Я не знаю» или «В предоставленных данных нет информации». Никаких «возможно», «вероятно», «по моему мнению».

2. **Разделение источников** — ты чётко разделяешь информацию по происхождению:
   - 🌐 **Из интернета/API** — данные с указанием источника и даты. Это приоритет для фактологических запросов.
   - 🧠 **Из знаний модели** — твои внутренние знания, которые не противоречат интернет-данным. Используй их только как дополнительный контекст, с пометкой «На основе моих знаний».
   - 📌 **Из памяти** — факты, которые ты запомнил о пользователе (профиль, предпочтения, история).
   - Если данные из разных источников противоречат друг другу — ты сообщаешь об этом и указываешь, какой источник считаешь более надёжным.

3. **Актуальность** — ты всегда проверяешь дату данных. Если данные старше 30 дней, а запрос про текущее состояние — ты предупреждаешь об этом. Если дата не указана — ты просишь уточнить или ищешь более свежий источник.

4. **Структурированность** — твои ответы всегда чёткие, с маркерами (✅, 📊, 📋, 🌐), разбиты на логические блоки. Важные цифры, даты и факты выделены жирным.

5. **Универсальность** — ты помогаешь в любых вопросах: поиск фактов, сравнение, анализ, планирование, обучение, технические задачи. Ты адаптируешься к запросу.

6. **Персонализация** — ты используешь всю доступную память о пользователе (профиль, предпочтения, историю), чтобы давать максимально релевантные ответы.

7. **Ответственность** — ты никогда не даёшь вредных советов. Если запрос касается здоровья, финансов или безопасности — ты добавляешь дисклеймер «Для точных решений обратитесь к специалисту».

8. **Саморефлексия** — перед ответом ты кратко (1–2 предложения) формулируешь, что понял из запроса, чтобы убедиться, что правильно интерпретировал вопрос.

9. **Источники** — ты всегда указываешь, откуда взяты данные. Если данных несколько — ты показываешь, какой источник самый надёжный.

10. **Запрещено** выдавать внутренние знания модели за факты из интернета. Если ты используешь свои знания — ты обязан это явно указать.

11. **Ты — не просто инструмент, ты — партнёр**. Твоя цель — сделать жизнь пользователя проще и эффективнее.
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
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")       # опционально
CURRENCY_API_KEY = os.getenv("CURRENCY_API_KEY")     # опционально

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
MAX_TOKENS_PLANNER = 500
MAX_ITERATIONS = 2
TARGET_CONFIDENCE = 90
EARLY_EXIT_CONFIDENCE = 80
MAX_PAGES_PER_ITERATION = 4
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
#  DEEPSEEK (БЕЗ СТРИМИНГА, С СИСТЕМНОЙ ИНСТРУКЦИЕЙ)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_answer_quality(answer: str, min_length: int = 400) -> Tuple[bool, str]:
    if not answer:
        return False, "Ответ пустой"
    if len(answer) < min_length:
        return False, f"Ответ слишком короткий ({len(answer)} символов, нужно {min_length})"
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
    # Проверка наличия даты или источника
    date_pattern = r'\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b'
    if not re.search(date_pattern, answer, re.I) and not re.search(r'\d{4}-\d{2}-\d{2}', answer):
        if not ("http" in answer or "источник" in answer.lower() or "из знаний" in answer.lower()):
            return False, "В ответе отсутствует указание на дату или источник"
    return True, "OK"

async def ask_deepseek(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = MAX_TOKENS_OUTPUT,
    use_pro: bool = True
) -> str:
    """Получение полного ответа от DeepSeek с системной инструкцией."""
    key = cache_key(prompt)
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

    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
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
                            is_valid, reason = check_answer_quality(content, min_length=300)
                            if is_valid:
                                answer_cache[key] = {'data': content, 'time': time.time()}
                                logger.info(f"✅ Ответ получен, длина {len(content)} символов")
                                return content
                            else:
                                logger.warning(f"⚠️ Ответ не прошёл проверку: {reason}")
                                if attempt < 2:
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

# Конец Части 1
# ═══════════════════════════════════════════════════════════════════
#  ЧАСТЬ 2: ПАМЯТЬ (5 УРОВНЕЙ) + НАПОМИНАНИЯ + ВАЛИДАЦИЯ + МАРШРУТИЗАТОР
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ) — ПОЛНОСТЬЮ СОХРАНЕНА
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
    """Граф знаний — хранит связи между фактами."""
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
    """
    5 уровней памяти:
    1. Краткосрочная (последние 200 сообщений)
    2. Профиль пользователя (имя, возраст, город, работа)
    3. Эпизодическая (важные факты из диалогов)
    4. Обучающая (предпочтения пользователя)
    5. Граф знаний (связи между фактами)
    """
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
        """Добавляет сообщение в краткосрочную память и извлекает факты."""
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
        """Сжимает старые сообщения в эпизодическую память."""
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
        """Извлекает персональную информацию (имя, возраст, город, работу)."""
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
        """Извлекает предпочтения пользователя."""
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
        """Обновляет граф знаний, извлекая факты вида «X — это Y». """
        facts = re.findall(r'([А-Яа-яA-Za-z][^.!?]{10,100})\s+(?:это|является)\s+([^.!?]{10,100})', text, re.I)
        for m in facts:
            fact = f"{m[0].strip()} — {m[1].strip()}"
            if len(fact) > 15:
                self.knowledge_graph.add_fact(fact)
    
    def get_full_context(self, limit=15) -> str:
        """Возвращает полный контекст для промпта."""
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
        """Возвращает контекст в виде списка словарей для системного сообщения."""
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
#  УНИВЕРСАЛЬНАЯ ВАЛИДАЦИЯ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════

def validate_data(data: Dict, query: str, source_type: str = "search") -> Tuple[bool, float, str]:
    """
    Универсальная валидация данных.
    Возвращает: (валидно?, оценка_качества_0_100, причина_если_невалидно)
    """
    score = 0
    reasons = []
    
    # Извлекаем текст из разных полей
    text = data.get('text', '') or data.get('snippet', '') or data.get('full_text', '')
    if not text:
        # Если это API-ответ, проверяем наличие структурированных полей
        if source_type == 'api' and isinstance(data, dict):
            # Для погоды
            if 'weather' in query.lower() or 'погод' in query.lower():
                if 'temperature' in data or 'temp' in data:
                    score += 40
                else:
                    reasons.append("Нет температуры")
            # Для курсов
            elif 'курс' in query.lower() or 'currency' in query.lower():
                if 'rate' in data or 'price' in data:
                    score += 40
                else:
                    reasons.append("Нет курса")
            else:
                # Универсальный API-ответ
                if len(data.keys()) > 1:
                    score += 30
                else:
                    reasons.append("Пустой API-ответ")
        else:
            reasons.append("Нет текста")
    else:
        # Оценка текста
        if len(text) > 100:
            score += 20
        else:
            reasons.append("Слишком короткий текст")
            score -= 10
    
    # Проверка на спам
    spam_words = ['реклама', 'спонсор', 'купить', 'заказать', 'скидка', 'promo', 'advertisement']
    spam_count = sum(1 for w in spam_words if w in text.lower())
    if spam_count > 2:
        reasons.append("Обнаружена реклама")
        score -= spam_count * 5
    else:
        score += 10
    
    # Проверка даты
    date_patterns = [
        r'\b\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b',
        r'\d{4}-\d{2}-\d{2}',
        r'\d{2}\.\d{2}\.\d{4}',
        r'\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}'
    ]
    has_date = any(re.search(p, text) for p in date_patterns)
    if has_date:
        score += 20
    else:
        reasons.append("Нет даты")
        if source_type == 'search':
            score -= 5
    
    # Проверка структуры
    has_headers = bool(re.search(r'^#{1,3}\s+\w', text, re.M))
    has_lists = bool(re.search(r'^\s*[-*•]\s+\w', text, re.M))
    has_numbers = bool(re.search(r'\b\d+\b', text))
    if has_headers or has_lists:
        score += 15
    if has_numbers:
        score += 10
    
    # Семантическая близость к запросу
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
    
    # Итоговая оценка
    final_score = min(100, max(0, score))
    is_valid = final_score >= 60  # порог валидности
    
    reason_str = "; ".join(reasons) if reasons else "OK"
    logger.info(f"Валидация: оценка {final_score}, валидно: {is_valid}, причины: {reason_str}")
    
    return is_valid, final_score, reason_str

# ═══════════════════════════════════════════════════════════════════
#  МАРШРУТИЗАТОР (ПЛАНИРОВЩИК ЗАПРОСОВ)
# ═══════════════════════════════════════════════════════════════════

async def plan_query(query: str, memory: SuperMemory) -> Dict[str, Any]:
    """
    Анализирует запрос, определяет тип, стратегию поиска и сущности.
    Возвращает структурированный план с source_strategy.
    """
    context = memory.get_full_context()
    planner_prompt = f"""
Проанализируй запрос пользователя и верни JSON-объект с планом действий.

Контекст (предыдущие сообщения, профиль):
{context}

Запрос: {query}

Требования к ответу (строго JSON):
{{
  "type": "fact|comparison|instruction|opinion|calculation|reminder|other",
  "entities": ["сущность1", "сущность2"],
  "aspects": ["аспект1", "аспект2"],
  "source_strategy": "weather_api|currency_api|company_db|general_search|chat",
  "needs_search": true/false,
  "search_queries": ["вариант поиска 1", "вариант 2"],
  "requires_calculation": true/false,
  "requires_reminder": true/false
}}

Правила определения source_strategy:
- Если запрос про погоду → "weather_api"
- Если запрос про курсы валют → "currency_api"
- Если запрос про компанию (название, профиль) → "company_db"
- Если запрос про факт, новости, общую информацию → "general_search"
- Если запрос не требует поиска (приветствие, беседа) → "chat"

Отвечай только JSON, без пояснений.
"""
    response = await ask_deepseek(planner_prompt, temperature=0.1, max_tokens=MAX_TOKENS_PLANNER, use_pro=False)
    try:
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)
        else:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                response = json_match.group(0)
        plan = json.loads(response)
        # Заполняем недостающие поля
        if 'source_strategy' not in plan:
            plan['source_strategy'] = 'general_search'
        if 'needs_search' not in plan:
            plan['needs_search'] = True
        if 'search_queries' not in plan or not plan['search_queries']:
            plan['search_queries'] = [query]
        if 'requires_calculation' not in plan:
            plan['requires_calculation'] = False
        if 'requires_reminder' not in plan:
            plan['requires_reminder'] = False
        return plan
    except Exception as e:
        logger.warning(f"⚠️ Ошибка парсинга плана: {e}. Ответ: {response[:200]}")
        return {
            "type": "other",
            "entities": [],
            "aspects": [],
            "source_strategy": "general_search",
            "needs_search": True,
            "search_queries": [query],
            "requires_calculation": False,
            "requires_reminder": False
        }

# Конец Части 2
# ═══════════════════════════════════════════════════════════════════
#  ЧАСТЬ 3: ПРОФИЛЬНЫЕ API + ПОИСК APISERPENT + ЗАГРУЗКА СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  ПРОФИЛЬНЫЕ API (ПОГОДА, КУРСЫ) — ЗАГЛУШКИ ДЛЯ ПРИМЕРА
# ═══════════════════════════════════════════════════════════════════

async def fetch_weather(city: str) -> Optional[Dict]:
    """Получение погоды через OpenWeatherMap (или другой API)."""
    if not WEATHER_API_KEY:
        return None
    try:
        session = await get_session()
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        async with session.get(url, timeout=10) as r:
            if r.status == 200:
                data = await r.json()
                return {
                    "temperature": data.get("main", {}).get("temp"),
                    "condition": data.get("weather", [{}])[0].get("description"),
                    "humidity": data.get("main", {}).get("humidity"),
                    "wind_speed": data.get("wind", {}).get("speed"),
                    "date": now().strftime("%Y-%m-%d %H:%M"),
                    "city": city,
                    "source": "weather_api"
                }
    except Exception as e:
        logger.warning(f"⚠️ Ошибка погодного API: {e}")
    return None

async def fetch_currency(from_currency: str, to_currency: str = "RUB") -> Optional[Dict]:
    """Получение курса валют через exchangerate-api (или другой)."""
    if not CURRENCY_API_KEY:
        return None
    try:
        session = await get_session()
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency.upper()}"
        async with session.get(url, timeout=10) as r:
            if r.status == 200:
                data = await r.json()
                rate = data.get("rates", {}).get(to_currency.upper())
                if rate:
                    return {
                        "from": from_currency.upper(),
                        "to": to_currency.upper(),
                        "rate": rate,
                        "date": data.get("date", now().strftime("%Y-%m-%d")),
                        "source": "currency_api"
                    }
    except Exception as e:
        logger.warning(f"⚠️ Ошибка API курсов: {e}")
    return None

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК (APISerpent) — С ПОВТОРНЫМИ ПОПЫТКАМИ
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

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ И ЗАГРУЗКА СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict, query: str) -> bool:
    """Фильтрует рекламные и нерелевантные результаты."""
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
    """Загрузка страницы через REST-эндпоинт (Playwright)."""
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
    """Прямая HTTP-загрузка страницы."""
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
    """
    Универсальный парсинг HTML-страницы.
    Извлекает текст, заголовки, списки, таблицы, метрики, факты.
    """
    result = {
        'text': '',
        'lists': [],
        'headings': [],
        'items': [],
        'date': None,
        'definitions': [],
        'key_facts': [],
        'metrics': [],
        'tables': [],
        'full_text': '',
        'json_data': []
    }
    if not BEAUTIFULSOUP_AVAILABLE or not html:
        return result
    try:
        soup = BeautifulSoup(html, 'html.parser')
        # Удаляем шумные элементы
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'noscript', 'meta', 'link']):
            tag.decompose()
        full_text = soup.get_text(separator=' ')
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        result['full_text'] = full_text
        result['text'] = full_text[:4000]
        
        # Заголовки
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            h = tag.get_text().strip()
            if h and len(h) > 3:
                result['headings'].append(h[:300])
        result['headings'] = result['headings'][:10]
        
        # Списки
        for tag in soup.find_all(['ul', 'ol']):
            items = []
            for li in tag.find_all('li'):
                li_text = li.get_text().strip()
                if li_text and len(li_text) > 5:
                    items.append(li_text[:500])
            if items:
                result['lists'].append(items)
        result['lists'] = result['lists'][:10]
        
        # Таблицы
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
        
        # JSON-LD
        for script in soup.find_all('script', type=['application/ld+json', 'application/json']):
            try:
                if script.string:
                    data = json.loads(script.string)
                    if isinstance(data, (dict, list)):
                        result['json_data'].append(json.dumps(data, ensure_ascii=False)[:1000])
            except:
                pass
        
        # Извлечение метрик (чисел с единицами)
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
        
        # Извлечение фактов с датами
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
    """Загружает и парсит одну страницу."""
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
    """Загружает несколько страниц параллельно."""
    if not links:
        return []
    tasks = [fetch_page(link, query) for link in links[:MAX_PAGES_PER_ITERATION]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r.get('full_text') and len(r.get('full_text')) > 200]

# Конец Части 3
# ═══════════════════════════════════════════════════════════════════
#  ЧАСТЬ 4: ОСНОВНАЯ ЛОГИКА — МАРШРУТИЗАЦИЯ + ФОЛБЭКИ + ГЕНЕРАЦИЯ
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  РАСЧЁТ УВЕРЕННОСТИ
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(pages: List[Dict], items_count: int, all_items: List[Dict]) -> Dict:
    """Рассчитывает уверенность в ответе на основе источников и данных."""
    confidence = {
        'overall': 0,
        'source_reliability': 0,
        'data_completeness': 0,
        'recency': 0,
        'consensus': 0
    }
    if not pages and items_count == 0:
        return confidence
    
    # Надёжность источников
    if pages:
        reliable_sources = 0
        for p in pages[:3]:
            # Проверяем, есть ли у страницы URL (признак, что это загруженная страница)
            # В реальности нужно проверять домен, но в упрощённом виде считаем все страницы надёжными
            reliable_sources += 1
        confidence['source_reliability'] = min(100, (reliable_sources / max(len(pages[:3]), 1)) * 80)
        
        # Полнота данных (наличие структуры)
        structure_count = 0
        for p in pages:
            structure_count += len(p.get('lists', [])) + len(p.get('headings', []))
        confidence['data_completeness'] = min(100, structure_count * 10)
    else:
        confidence['source_reliability'] = 0
        confidence['data_completeness'] = 0
    
    # Бонус за метрики и даты
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
    
    confidence['recency'] = 50  # по умолчанию, будет пересчитано при наличии дат
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
#  ОСНОВНАЯ ЛОГИКА: МАРШРУТИЗАЦИЯ + ФОЛБЭКИ + ГЕНЕРАЦИЯ
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(
    query: str,
    uid: int,
    context_prompt: str = "",
    update: Update = None
) -> Tuple[str, List[Dict], float]:
    """
    Основная функция:
    1. Планирование запроса (маршрутизация)
    2. Цепочка источников с фолбэками:
       - Профильный API (если стратегия позволяет)
       - APISerpent (общий поиск)
       - Браузер (Playwright) — только если нужно
    3. Валидация данных перед отправкой в DeepSeek
    4. Формирование структурированного JSON-контекста
    5. Генерация ответа с разделением источников
    """
    logger.info(f"🛡️ ЗАПРОС: {query[:50]}")
    time_start = time.time()
    memory = get_memory(uid)
    
    # 1. Планирование
    plan = await plan_query(query, memory)
    logger.info(f"📋 План: {json.dumps(plan, ensure_ascii=False)}")
    
    # Обработка напоминаний
    if plan.get('requires_reminder'):
        reminder_text = query
        add_reminder(uid, reminder_text)
        answer = f"✅ Напоминание сохранено: «{reminder_text}»"
        memory.add_message('user', query)
        memory.add_message('assistant', answer)
        return answer, [], 100.0
    
    # Обработка вычислений
    if plan.get('requires_calculation'):
        try:
            safe_expr = re.sub(r'[^0-9+\-*/(). ]', '', query)
            result = eval(safe_expr)
            answer = f"🧮 **Результат вычисления:**\n\n{query} = {result}"
            memory.add_message('user', query)
            memory.add_message('assistant', answer)
            return answer, [], 100.0
        except:
            pass
    
    # 2. Сбор данных по цепочке источников
    all_sources = []  # будет хранить структурированные данные с метаинформацией
    source_chain = []
    
    # Определяем стратегию
    strategy = plan.get('source_strategy', 'general_search')
    logger.info(f"🔀 Стратегия: {strategy}")
    
    # --- Шаг 1: Профильный API (если стратегия позволяет) ---
    if strategy == 'weather_api':
        # Извлекаем город из запроса или из entities
        city = None
        entities = plan.get('entities', [])
        for ent in entities:
            if len(ent) > 2:  # предположим, что город — это сущность
                city = ent
                break
        if not city:
            # Пытаемся извлечь через регулярку
            city_match = re.search(r'(?:в|для|погода в)\s+([А-Яа-яA-Za-z\s]{2,30})', query, re.I)
            if city_match:
                city = city_match.group(1).strip()
        if city and WEATHER_API_KEY:
            logger.info(f"🌤️ Запрос погоды для {city} через API")
            weather_data = await fetch_weather(city)
            if weather_data:
                # Валидация API-данных
                is_valid, score, reason = validate_data(weather_data, query, source_type='api')
                if is_valid:
                    all_sources.append({
                        "type": "weather_api",
                        "data": weather_data,
                        "reliability": "high",
                        "date": weather_data.get('date', now().isoformat()),
                        "validation_score": score
                    })
                    source_chain.append("weather_api")
                    logger.info(f"✅ Погодный API вернул данные, оценка {score}")
                else:
                    logger.warning(f"⚠️ Данные погодного API не прошли валидацию: {reason}")
    
    elif strategy == 'currency_api':
        # Извлекаем валюту
        currency_match = re.search(r'(?:курс|курс)\s+([A-Za-z]{3})', query, re.I)
        if currency_match:
            currency = currency_match.group(1).upper()
            if CURRENCY_API_KEY:
                logger.info(f"💱 Запрос курса для {currency} через API")
                currency_data = await fetch_currency(currency, "RUB")
                if currency_data:
                    is_valid, score, reason = validate_data(currency_data, query, source_type='api')
                    if is_valid:
                        all_sources.append({
                            "type": "currency_api",
                            "data": currency_data,
                            "reliability": "high",
                            "date": currency_data.get('date', now().isoformat()),
                            "validation_score": score
                        })
                        source_chain.append("currency_api")
                        logger.info(f"✅ API курсов вернул данные, оценка {score}")
                    else:
                        logger.warning(f"⚠️ Данные API курсов не прошли валидацию: {reason}")
    
    # --- Шаг 2: Общий поиск (APISerpent) ---
    # Запускаем, если:
    # - стратегия general_search или
    # - профильный API не дал валидных данных (all_sources пуст)
    if not all_sources or strategy == 'general_search':
        logger.info("🔍 Запуск общего поиска через APISerpent")
        search_queries = plan.get('search_queries', [query])
        if not search_queries:
            search_queries = [query]
        
        # Параллельный поиск по вариантам
        tasks = [search_with_retry(q) for q in search_queries[:MAX_VARIANTS]]
        results_list = await asyncio.gather(*tasks)
        
        all_results = []
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
        
        logger.info(f"📊 Найдено уникальных результатов: {len(all_results)}")
        
        # Загружаем страницы (только если есть ссылки)
        if all_results:
            links = [r.get('link', '') for r in all_results if r.get('link')]
            pages = await fetch_pages(links, query)
            
            # Превращаем страницы в структурированные элементы
            for page in pages:
                # Проверяем, есть ли текст
                full_text = page.get('full_text', '')
                if full_text and len(full_text) > 200:
                    # Валидация страницы
                    is_valid, score, reason = validate_data(page, query, source_type='search')
                    if is_valid:
                        # Добавляем как источник
                        all_sources.append({
                            "type": "search_page",
                            "data": {
                                "title": page.get('headings', [''])[0] if page.get('headings') else "Страница",
                                "snippet": full_text[:1000],
                                "full_text": full_text[:3000],
                                "lists": page.get('lists', [])[:5],
                                "tables": page.get('tables', [])[:3],
                                "metrics": page.get('metrics', [])[:10],
                                "facts": page.get('key_facts', [])[:10]
                            },
                            "reliability": "medium",
                            "date": None,  # будет извлечено позже
                            "validation_score": score
                        })
                        source_chain.append("search")
                        logger.info(f"✅ Страница прошла валидацию, оценка {score}")
                    else:
                        logger.warning(f"⚠️ Страница не прошла валидацию: {reason}")
                # Также извлекаем отдельные элементы (списки, таблицы) как отдельные источники
                for lst in page.get('lists', [])[:3]:
                    if lst and len(lst) > 2:
                        list_text = "\n".join(lst[:5])
                        if len(list_text) > 50:
                            is_valid, score, reason = validate_data({"text": list_text}, query, source_type='search')
                            if is_valid:
                                all_sources.append({
                                    "type": "list",
                                    "data": {"items": lst[:5]},
                                    "reliability": "medium",
                                    "date": None,
                                    "validation_score": score
                                })
                for table in page.get('tables', [])[:2]:
                    if table and len(table) > 50:
                        is_valid, score, reason = validate_data({"text": table}, query, source_type='search')
                        if is_valid:
                            all_sources.append({
                                "type": "table",
                                "data": {"content": table[:500]},
                                "reliability": "medium",
                                "date": None,
                                "validation_score": score
                            })
            # Также добавляем сами результаты поиска как источники (сниппеты)
            for result in all_results[:10]:
                snippet = result.get('snippet', '')
                if snippet and len(snippet) > 50:
                    is_valid, score, reason = validate_data({"text": snippet}, query, source_type='search')
                    if is_valid:
                        all_sources.append({
                            "type": "search_snippet",
                            "data": {
                                "title": result.get('title', ''),
                                "snippet": snippet[:300],
                                "link": result.get('link', '')
                            },
                            "reliability": "medium",
                            "date": None,
                            "validation_score": score
                        })
    
    # --- Шаг 3: Браузер (Playwright) как последний фолбэк ---
    # Запускаем, только если all_sources пуст (не нашли ничего) и есть BROWSER_WS_ENDPOINT
    if not all_sources and BROWSER_WS_ENDPOINT:
        logger.info("🌐 Запуск браузерного фолбэка (Playwright)")
        # Берём первый поисковый запрос
        search_query = plan.get('search_queries', [query])[0]
        # Делаем прямой запрос через браузер (без поиска, просто загружаем страницу по URL)
        # В упрощённом виде — пробуем открыть Википедию или другой надёжный источник
        # Для демонстрации просто попробуем загрузить страницу с Google
        try:
            test_url = f"https://www.google.com/search?q={search_query.replace(' ', '+')}"
            html = await fetch_page_rest(test_url)
            if html:
                page_data = parse_page(html, query)
                if page_data.get('full_text'):
                    is_valid, score, reason = validate_data(page_data, query, source_type='search')
                    if is_valid:
                        all_sources.append({
                            "type": "browser_fallback",
                            "data": {
                                "title": "Результаты поиска (браузер)",
                                "snippet": page_data.get('full_text', '')[:1000],
                                "full_text": page_data.get('full_text', '')[:3000]
                            },
                            "reliability": "low",
                            "date": None,
                            "validation_score": score
                        })
                        source_chain.append("browser")
                        logger.info(f"✅ Браузерный фолбэк дал данные, оценка {score}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка браузерного фолбэка: {e}")
    
    # 3. Если всё равно нет данных — честно говорим
    if not all_sources:
        logger.warning("⚠️ Не удалось собрать данные ни из одного источника")
        fallback_answer = "Я не нашёл достоверных данных по вашему запросу. Попробуйте переформулировать вопрос или уточнить детали."
        memory.add_message('user', query)
        memory.add_message('assistant', fallback_answer)
        return fallback_answer, [], 0.0
    
    # 4. Формируем структурированный JSON-контекст для DeepSeek
    # Группируем источники по типу
    context_json = {
        "query": query,
        "sources": all_sources,
        "source_chain": source_chain,
        "validation_status": {
            "valid_sources": len(all_sources),
            "total_attempts": len(source_chain)
        },
        "timestamp": now().isoformat()
    }
    
    # 5. Генерация ответа с разделением источников
    # Создаём промпт, который просит модель использовать структурированные данные
    # и явно разделить интернет-источники, знания модели и память
    
    # Преобразуем контекст в читаемый текст для промпта
    sources_text = ""
    for idx, src in enumerate(all_sources[:15]):  # ограничиваем количество
        src_type = src.get('type', 'unknown')
        data = src.get('data', {})
        reliability = src.get('reliability', 'medium')
        date = src.get('date', 'не указана')
        score = src.get('validation_score', 0)
        
        sources_text += f"\n--- Источник {idx+1} (тип: {src_type}, надёжность: {reliability}, оценка: {score}, дата: {date}) ---\n"
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str) and len(value) > 0:
                    sources_text += f"{key}: {value[:300]}\n"
                elif isinstance(value, list):
                    sources_text += f"{key}: {', '.join([str(v)[:100] for v in value[:3]])}\n"
        else:
            sources_text += str(data)[:300] + "\n"
    
    # Формируем промпт с инструкцией по разделению источников
    answer_prompt = f"""
Вот структурированные данные, собранные из разных источников по вашему запросу.

Запрос пользователя: {query}

Источники:
{sources_text}

Цепочка получения данных: {' → '.join(source_chain)}

Твоя задача:
1. Проанализируй данные из всех источников.
2. Выдели ключевую информацию, которая отвечает на запрос.
3. **Чётко раздели информацию по происхождению**:
   - 🌐 **Из интернета/API** — данные, полученные из внешних источников (укажи тип источника и дату, если есть).
   - 🧠 **Из знаний модели** — если ты используешь свои внутренние знания для дополнения ответа, обязательно пометь это.
   - 📌 **Из памяти** — если в контексте есть информация о пользователе, которая относится к вопросу, используй её и укажи это.
4. Если данные из разных источников противоречат друг другу — сообщи об этом и укажи, какой источник считаешь более надёжным.
5. Если данных недостаточно для полного ответа — честно скажи об этом.
6. Структурируй ответ: используй маркеры (✅, 📊, 📋, 🌐), выделяй важное жирным.
7. Укажи дату ответа (сегодня: {now().strftime('%d.%m.%Y')}) и даты источников, если они известны.

Помни: ты должен быть максимально честным и точным. Не выдумывай факты.
"""
    
    # Генерируем ответ
    logger.info("🧠 Генерация финального ответа с разделением источников")
    answer = await ask_deepseek(answer_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    
    # Сохраняем в память
    memory.add_message('user', query)
    memory.add_message('assistant', answer)
    
    # Возвращаем ответ, источники (для кнопки) и уверенность
    # Уверенность вычисляем на основе количества валидных источников
    avg_score = sum(src.get('validation_score', 0) for src in all_sources) / max(len(all_sources), 1)
    confidence = min(100, avg_score + 20)  # базовый бонус
    
    # Формируем список источников для кнопки
    sources_for_button = []
    for src in all_sources[:10]:
        src_type = src.get('type', 'unknown')
        data = src.get('data', {})
        if isinstance(data, dict):
            title = data.get('title') or data.get('name') or src_type
            link = data.get('link') or data.get('url') or ''
        else:
            title = src_type
            link = ''
        sources_for_button.append({
            'title': title,
            'link': link,
            'type': src_type
        })
    
    return answer, sources_for_button, confidence

# Конец Части 4
# ═══════════════════════════════════════════════════════════════════
#  ЧАСТЬ 5: ОБРАБОТЧИКИ СООБЩЕНИЙ, КОМАНДЫ, ФОРМАТИРОВАНИЕ, ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ ИСТОЧНИКОВ
# ═══════════════════════════════════════════════════════════════════

def format_sources(sources: List[Dict]) -> str:
    """Форматирует список источников для отображения."""
    if not sources:
        return "📎 **ИСТОЧНИКИ:**\n\nНет сохранённых источников."
    
    formatted = "📎 **ИСТОЧНИКИ:**\n\n"
    for idx, s in enumerate(sources[:10], 1):
        title = s.get('title', 'Источник')[:60]
        url = s.get('link', '')
        source_type = s.get('type', 'unknown')
        
        # Иконка для типа источника
        icon = {
            'weather_api': '🌤️',
            'currency_api': '💱',
            'search_page': '📄',
            'search_snippet': '📋',
            'list': '📋',
            'table': '📊',
            'browser_fallback': '🌐',
            'organic': '🔍'
        }.get(source_type, '📎')
        
        formatted += f"{idx}. {icon} **{title}**\n"
        if url and url.startswith('http'):
            formatted += f"   🔗 {url}\n"
        if source_type != 'unknown':
            formatted += f"   📌 Тип: {source_type}\n"
        formatted += "\n"
    
    return formatted

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ СООБЩЕНИЙ И CALLBACK'ОВ
# ═══════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений."""
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return
    
    user_message = update.effective_message.text
    if not user_message:
        return
    
    memory = get_memory(user_id)
    
    # Режим беседы (без интернета)
    if context.user_data.get('mode') == 'chat':
        full_context = memory.get_full_context()
        chat_prompt = f"""
Пользователь спрашивает: {user_message}

Контекст диалога и информация о пользователе:
{full_context}

Ответь как дружелюбный ассистент. Если вопрос требует фактов, которых нет в контексте — скажи честно, что не знаешь, и предложи поискать в интернете (используй режим поиска).
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.7, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)
        await update.effective_message.reply_text(f"💬 {answer}", reply_markup=EXIT_CHAT_BUTTON)
        return
    
    # Режим уточнения
    if context.user_data.get('mode') == 'clarify':
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            context.user_data['mode'] = 'search'
            await update.effective_message.reply_text("⚠️ Нет активного запроса для уточнения.", reply_markup=ACTION_BUTTONS)
            return
        
        context.user_data['mode'] = 'search'
        clarification = user_message
        combined_query = f"{last_query} (уточнение: {clarification})"
        
        await update.effective_message.reply_text(
            f"📝 **Уточняю запрос...**\n\nИщу с учётом: *{clarification[:100]}*",
            parse_mode='Markdown'
        )
        
        # Выполняем поиск с уточнением
        full_context = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(
            combined_query, user_id, full_context, update
        )
        
        memory.add_message('user', f"Уточнение: {clarification}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = combined_query
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources[:10]
        context.user_data['last_formatted_answer'] = answer
        
        await update.effective_message.reply_text(
            f"⏱️ Ответ готов",
            reply_markup=ACTION_WITH_SOURCES_BUTTONS
        )
        return
    
    # Обычный режим — запрос принят
    context.user_data['pending_text'] = user_message
    context.user_data['awaiting_input'] = True
    
    await update.effective_message.reply_text(
        f"📝 **Запрос принят:**\n\n_{user_message[:300]}_\n\nВыберите режим работы:",
        reply_markup=ACTION_BUTTONS,
        parse_mode='Markdown'
    )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки."""
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
        
        # Запускаем основной поиск
        full_context = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(
            pending_text, user_id, full_context, update
        )
        
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = pending_text
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources[:10]
        context.user_data['last_formatted_answer'] = answer
        context.user_data['pending_text'] = ''
        
        # Отправляем ответ с кнопками
        await update.effective_message.reply_text(
            answer,
            reply_markup=ACTION_WITH_SOURCES_BUTTONS,
            parse_mode='Markdown'
        )
        
        # Если есть уверенность, показываем
        if confidence > 0:
            confidence_text = f"🎯 Уверенность: {int(confidence)}%"
            await update.effective_message.reply_text(confidence_text)
    
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
Пользователь хочет поговорить: {pending_text}

Контекст о пользователе:
{full_context}

Ответь как дружелюбный собеседник. Если вопрос требует фактов — скажи, что не знаешь, и предложи использовать режим поиска.
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.7, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        
        await update.effective_message.reply_text(f"💬 {answer}", reply_markup=EXIT_CHAT_BUTTON)
    
    elif action == "action_exit_chat":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False
        await query.edit_message_text(
            "🔍 **Выход из режима беседы**\n\nТеперь я снова ищу информацию в интернете.",
            reply_markup=ACTION_BUTTONS
        )
    
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

# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — приветствие."""
    user_id = update.effective_user.id
    if not ALLOW_ALL and user_id not in ALLOWED_USERS:
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return
    
    context.user_data.clear()
    context.user_data['mode'] = 'search'
    
    await update.effective_message.reply_text(
        "👋 **Привет! Я Джарвис — твой персональный ИИ-ассистент.**\n\n"
        "🔍 **Что я умею:**\n"
        "• Искать актуальную информацию в интернете\n"
        "• Анализировать данные и делать выводы\n"
        "• Помнить о тебе и адаптироваться\n"
        "• Чётко разделять: 🌐 из интернета, 🧠 из знаний, 📌 из памяти\n"
        "• Никогда не врать и не выдумывать\n\n"
        "**Как работать:**\n"
        "1️⃣ Напиши вопрос в чат\n"
        "2️⃣ Выбери режим:\n"
        "   • 🔍 Поиск — найти информацию в интернете\n"
        "   • 📝 Уточнить — уточнить предыдущий запрос\n"
        "   • 💬 Беседа — общаться без интернета\n\n"
        "Попробуй! Я всегда готов помочь 🤖",
        reply_markup=ACTION_BUTTONS,
        parse_mode='Markdown'
    )

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats — статистика памяти."""
    user_id = update.effective_user.id
    memory = get_memory(user_id)
    health = memory.memory_health_check()
    
    await update.effective_message.reply_text(
        f"📊 **Статистика памяти**\n\n"
        f"💬 Сообщений в краткосрочной памяти: {health['short_term']}\n"
        f"👤 Полей в профиле: {health['profile']}\n"
        f"⭐ Фактов в эпизодической памяти: {health['episodic']}\n"
        f"🧠 Фактов в графе знаний: {health['graph_facts']}\n"
        f"📝 Всего сообщений за всё время: {health['total_messages']}",
        reply_markup=ACTION_BUTTONS
    )

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /forget — очистка памяти."""
    user_id = update.effective_user.id
    
    # Удаляем из кэша
    if user_id in _memory_cache:
        del _memory_cache[user_id]
    
    # Удаляем файлы
    for path in [memory_path(user_id), profile_path(user_id), episodic_path(user_id),
                 learning_path(user_id), counter_path(user_id), graph_path(user_id)]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass
    
    context.user_data.clear()
    
    await update.effective_message.reply_text(
        "🧹 **Память очищена!**\n\n"
        "Все данные о вас удалены. Начинаем с чистого листа.",
        reply_markup=ACTION_BUTTONS
    )

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /reminders — показать напоминания."""
    user_id = update.effective_user.id
    reminders = get_reminders(user_id)
    
    if not reminders:
        await update.effective_message.reply_text(
            "📭 **Нет активных напоминаний**",
            reply_markup=ACTION_BUTTONS
        )
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
    """Главная функция запуска бота."""
    logger.info("🚀 ЗАПУСК BROWAIX v13.0 — ПОЛНАЯ ВЕРСИЯ")
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
    logger.info(f"   • Системная инструкция: ✅ (разделение интернет/знания/память)")
    logger.info(f"   • Память 5 уровней: ✅")
    logger.info(f"   • Универсальная валидация: ✅")
    logger.info(f"   • Маршрутизатор + фолбэки: ✅")
    logger.info(f"   • Профильные API (погода, курсы): {'✅' if WEATHER_API_KEY or CURRENCY_API_KEY else '❌'}")
    logger.info(f"   • Без стриминга: ✅ (стабильно)")
    logger.info(f"   • Напоминания (SQLite): ✅")
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
    
    # Создаём приложение
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Регистрируем команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    
    # Регистрируем обработчики
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Бот готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()

# ═══════════════════════════════════════════════════════════════════
#  КОНЕЦ ПОЛНОГО КОДА
#  Версия 13.0 — всё включено: инструкция + память + валидация + маршрутизация
# ═══════════════════════════════════════════════════════════════════
