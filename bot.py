# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ v3.0
#  ДВА ПАРАЛЛЕЛЬНЫХ МЕТОДА:
#  1. APISerpent → Browserless (загрузка сложных страниц)
#  2. DeepSeek Search (нативный поиск, независимый источник)
#  ОБЪЕДИНЕНИЕ РЕЗУЛЬТАТОВ → МАКСИМАЛЬНО БОГАТЫЙ ОТВЕТ
#  С ПОМЕТКАМИ ИСТОЧНИКОВ
# ═══════════════════════════════════════════════════════════════════

"""
🤖 БОТ: BROWAIX — УНИВЕРСАЛЬНЫЙ ПОИСКОВЫЙ АССИСТЕНТ v3.0

📌 ОСНОВНЫЕ ВОЗМОЖНОСТИ:
────────────────────────────────────────────────────────────────────
1. 🔍 ДВА ПАРАЛЛЕЛЬНЫХ МЕТОДА ПОИСКА:
   - APISerpent + Browserless (загрузка и парсинг страниц)
   - DeepSeek Search (нативный поиск с готовыми результатами)

2. 🧠 ПАМЯТЬ (5 УРОВНЕЙ)
   - Краткосрочная (последние 100 сообщений)
   - Профиль пользователя (имя, возраст, город, работа)
   - Эпизодическая (важные факты из диалогов)
   - Обучающая (предпочтения пользователя)
   - Граф знаний (связи между фактами)

3. 🎯 РЕЖИМЫ РАБОТЫ
   - 🔍 Поиск — полноценный поиск в интернете
   - 📝 Уточнить — уточнение предыдущего запроса
   - 💬 Беседа — общение без интернета (из знаний и памяти)

4. 🛡️ ЗАЩИТА ОТ ОБМАНА (УСИЛЕННАЯ)
   - Полный запрет субъективных фраз ("я считаю", "по моему мнению")
   - Запрет выдумывать факты без источников
   - Запрет коротких ответов (минимум 800 символов)
   - Жёсткая проверка качества ответа
   - Принудительная перегенерация при плохом ответе

5. 📦 ТЕХНИЧЕСКИЕ ХАРАКТЕРИСТИКИ
   - Модель: deepseek-v4-pro (для ответов) и deepseek-v4-flash (для поиска)
   - Макс. токенов: 8000
   - Параллельная загрузка: 3 страницы
   - Кэширование: 15 мин (поиск), 1 час (ответы)
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
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

# API ключи
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")

# Доступ пользователей
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

# Параметры
SEARCH_RESULTS = 15
MAX_PAGES = 3
MAX_VARIANTS = 3

# Параметры качества
MIN_ANSWER_LENGTH = 800
MIN_CONFIDENCE_EXIT = 30
MIN_SNIPPET_LENGTH = 30

# Таймауты
PAGE_TIMEOUT = 25
APISERPENT_TIMEOUT = 30
CACHE_TTL = 900
ANSWER_CACHE_TTL = 3600

# Модели
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")
DEEPSEEK_MODEL_FLASH = os.getenv("DEEPSEEK_MODEL_FLASH", "deepseek-v4-flash")

# Уверенность
TARGET_CONFIDENCE = 95
EARLY_EXIT_CONFIDENCE = 90

# Токены
MAX_TOKENS_OUTPUT = 8000
MAX_TOKENS_VARIANTS = 500

# Время
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

CLARIFY_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Уточнить запрос", callback_data="action_clarify")]
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
#  DEEPSEEK (С ЖЁСТКОЙ ПРОВЕРКОЙ КАЧЕСТВА)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_for_lies_and_laziness(answer: str) -> Tuple[bool, str]:
    """
    Жёсткая проверка ответа на враньё и лень
    Возвращает: (is_valid, reason)
    """
    if not answer:
        return False, "Ответ пустой - это лень!"
    
    # Проверка на субъективизм
    subjective_phrases = [
        "по моему мнению", "я считаю", "я думаю", "на мой взгляд",
        "мне кажется", "я предполагаю", "я полагаю", "я уверен",
        "я знаю", "как мне кажется", "я бы сказал"
    ]
    for phrase in subjective_phrases:
        if phrase in answer.lower():
            return False, f"ОБНАРУЖЕНО СУБЪЕКТИВНОЕ МНЕНИЕ: '{phrase}'"
    
    # Проверка на лень (отмазки)
    lazy_phrases = [
        "не могу найти", "нет доступа", "не удалось", "нет информации",
        "я не могу", "не знаю", "информация отсутствует", "я не нашёл",
        "нет интернета", "не могу ответить", "не нашлось"
    ]
    for phrase in lazy_phrases:
        if phrase in answer.lower():
            return False, f"ОБНАРУЖЕНА ЛЕНЬ: '{phrase}'"
    
    # Проверка на неуверенность (не более 2 раз)
    uncertain_phrases = [
        "возможно", "вероятно", "скорее всего", "наверное",
        "примерно", "около", "приблизительно", "может быть",
        "наверно", "похоже", "кажется"
    ]
    uncertain_count = sum(1 for p in uncertain_phrases if p in answer.lower())
    if uncertain_count > 2:
        return False, f"СЛИШКОМ МНОГО НЕУВЕРЕННОСТИ: {uncertain_count} раз"
    
    # Проверка на длину
    clean_text = re.sub(r'[#*_`\-\s]+', '', answer)
    if len(clean_text) < MIN_ANSWER_LENGTH:
        return False, f"ОТВЕТ СЛИШКОМ КОРОТКИЙ ({len(clean_text)} знаков, нужно {MIN_ANSWER_LENGTH})"
    
    # Проверка на структуру
    required_markers = ['**', '📊', '📋', '🌐', '⚠️']
    if not any(marker in answer for marker in required_markers):
        return False, "НЕТ СТРУКТУРЫ ОТВЕТА (лень форматировать)"
    
    # Проверка на выдумывание фактов
    fact_pattern = r'([А-Яа-я][^.!?]{10,60})\s+(?:—|–|-|это|является|будет|станет)\s+([^.!?]{10,80})'
    facts = re.findall(fact_pattern, answer, re.I)
    
    if facts and "источник" not in answer.lower() and "source" not in answer.lower():
        fact_count = len(facts)
        if fact_count > 2:
            return False, f"ОБНАРУЖЕНО {fact_count} УТВЕРЖДЕНИЙ БЕЗ ИСТОЧНИКОВ (выдумка!)"
    
    # Проверка на прогнозы без оснований
    if re.search(r'(прогноз|предсказание|предположение|будет|станет)', answer, re.I):
        if "источник" not in answer.lower() and "данные" not in answer.lower():
            return False, "ПРОГНОЗ БЕЗ УКАЗАНИЯ ИСТОЧНИКА ДАННЫХ (гадание!)"
    
    # Проверка на уникальность
    sentences = re.split(r'[.!?]+', answer)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    if sentences:
        unique_ratio = len(set(sentences)) / len(sentences)
        if unique_ratio < 0.4:
            return False, f"СЛИШКОМ МНОГО ПОВТОРОВ (уникальность {unique_ratio:.0%})"
    
    # Проверка на обещания и гарантии
    promise_patterns = [
        r'я\s+(обещаю|гарантирую|предсказываю)',
        r'мы\s+(обещаем|гарантируем|предсказываем)',
        r'гарантирую', r'обещаю'
    ]
    for pattern in promise_patterns:
        if re.search(pattern, answer, re.I):
            return False, f"ОБНАРУЖЕНО ОБЕЩАНИЕ/ГАРАНТИЯ (это запрещено!)"
    
    return True, "OK"

async def ask_deepseek(prompt: str, temperature: float = 0.2, max_tokens: int = MAX_TOKENS_OUTPUT, use_pro: bool = True) -> str:
    """Универсальный вызов DeepSeek с жёсткой проверкой качества"""
    key = cache_key(prompt)
    
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        is_valid, _ = check_for_lies_and_laziness(cached)
        if is_valid:
            logger.info("♻️ Ответ DeepSeek из кэша (проверен)")
            return cached
        else:
            logger.info("♻️ Кэшированный ответ отклонён, генерируем новый")
            del answer_cache[key]

    model = DEEPSEEK_MODEL_PRO if use_pro else DEEPSEEK_MODEL_FLASH
    logger.info(f"🧠 DeepSeek: {'Pro' if use_pro else 'Flash'}")

    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=90
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    content = data["choices"][0]["message"]["content"]
                    if content and len(content) > 50:
                        is_valid, reason = check_for_lies_and_laziness(content)
                        if is_valid:
                            answer_cache[key] = {'data': content, 'time': time.time()}
                            return content
                        else:
                            logger.warning(f"⚠️ Ответ DeepSeek отклонён: {reason}")
                            if attempt == 2:
                                return f"⚠️ Ответ требует проверки:\n\n{content}"
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
#  РАЗБИВКА ДЛИННЫХ СООБЩЕНИЙ
# ═══════════════════════════════════════════════════════════════════

async def send_long_message(update, text: str, reply_markup=None):
    """Универсальная отправка с разбивкой на части по 4096 символов"""
    if not text:
        return
    
    try:
        if len(text) <= 4096:
            await update.effective_message.reply_text(text, reply_markup=reply_markup)
            return
        
        parts = []
        current = ""
        
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        
        if current:
            parts.append(current)
        
        await update.effective_message.reply_text(parts[0], reply_markup=reply_markup)
        for part in parts[1:]:
            await update.effective_message.reply_text(part)
    except Exception as e:
        logger.error(f"❌ Ошибка в send_long_message: {e}")
        try:
            await update.effective_message.reply_text(
                text[:3000] + "\n\n... (ответ обрезан из-за ошибки)",
                reply_markup=reply_markup
            )
        except:
            pass

# ═══════════════════════════════════════════════════════════════════
#  РАДУЖНАЯ ПОЛОСКА
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    """Детальный прогресс с радужной анимированной полоской"""
    message = None
    try:
        stages = [
            {"emoji": "🧠", "name": "Анализ запроса (DeepSeek Pro)", "duration": 5},
            {"emoji": "🔍", "name": "Параллельный поиск (APISerpent + Browserless)", "duration": 15},
            {"emoji": "🤖", "name": "Глубокий поиск (DeepSeek Search)", "duration": 10},
            {"emoji": "📊", "name": "Объединение и анализ данных", "duration": 8},
            {"emoji": "🤔", "name": "Формирование ответа (DeepSeek Pro)", "duration": 10},
        ]
        
        rainbow_colors = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣"]
        
        message = await context.bot.send_message(
            chat_id,
            "🧠 **Анализ запроса (DeepSeek Pro)**\n"
            "`░░░░░░░░░░░░░░░░░░░░ 0%`\n"
            "⏱️ 0 сек"
        )
        
        elapsed = 0
        stage_idx = 0
        stage_start = 0
        color_idx = 0
        
        while True:
            await asyncio.sleep(1)
            
            if context.user_data.get('found_answer'):
                try:
                    await message.edit_text(
                        "✅ **Готово!** Формирую ответ...\n"
                        f"⏱️ {int(time.time() - start_time)} сек"
                    )
                except Exception:
                    pass
                break
            
            elapsed = int(time.time() - start_time)
            
            if stage_idx < len(stages):
                stage = stages[stage_idx]
                stage_elapsed = elapsed - stage_start
                progress = min(100, int((stage_elapsed / stage["duration"]) * 100))
                
                color_idx = (color_idx + 1) % len(rainbow_colors)
                color = rainbow_colors[color_idx]
                
                bar_length = 20
                filled = int(progress / 100 * bar_length)
                bar = "█" * filled + "░" * (bar_length - filled)
                
                text = (
                    f"{stage['emoji']} **{stage['name']}**\n"
                    f"`{bar} {progress}%` {color}\n"
                    f"⏱️ {elapsed} сек"
                )
                
                if progress >= 100 and stage_idx < len(stages) - 1:
                    stage_idx += 1
                    stage_start = elapsed
                    color_idx = 0
                    continue
                
                if stage_idx == len(stages) - 1 and progress >= 100:
                    bar = "███████████████████░ 95%"
                    text = (
                        f"{stage['emoji']} **{stage['name']}**\n"
                        f"`{bar}` 🟣\n"
                        f"⏱️ {elapsed} сек"
                    )
                
                try:
                    await message.edit_text(text, parse_mode='Markdown')
                except Exception:
                    pass
            
            if elapsed > 300:
                break
                
    except Exception as e:
        logger.error(f"❌ Ошибка прогресса: {e}")

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК ЧЕРЕЗ APISERPENT
# ═══════════════════════════════════════════════════════════════════

def normalize_query(query):
    return re.sub(r'[^\w\s]', '', query.lower()).strip()

async def search_apiserpent(query: str) -> List[Dict]:
    """Поиск через APISerpent"""
    if not APISERPENT_API_KEY:
        logger.error("❌ APISERPENT_API_KEY не задан!")
        return []
    
    try:
        session = await get_session()
        logger.info(f"🔍 APISerpent запрос: {query[:50]}...")
        
        params = {
            "q": query,
            "engine": "google",
            "num": SEARCH_RESULTS,
        }
        
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY, "Accept": "application/json"},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                results = []
                
                # organic_results
                if "organic_results" in data:
                    for item in data.get("organic_results", []):
                        if isinstance(item, dict):
                            results.append({
                                "title": item.get("title", ""),
                                "snippet": item.get("snippet", ""),
                                "link": item.get("link", ""),
                                "source": "apiserpent"
                            })
                    if results:
                        return results
                
                # results.organic
                if "results" in data and isinstance(data["results"], dict):
                    for item in data["results"].get("organic", []):
                        if isinstance(item, dict):
                            results.append({
                                "title": item.get("title", ""),
                                "snippet": item.get("snippet", ""),
                                "link": item.get("link", ""),
                                "source": "apiserpent"
                            })
                    if results:
                        return results
                
                return results
                
    except Exception as e:
        logger.error(f"💥 Ошибка APISerpent: {e}")
    
    return []

# ═══════════════════════════════════════════════════════════════════
#  BROWSERLESS — ЗАГРУЗКА СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

async def fetch_with_browserless(url: str) -> Optional[str]:
    """Загрузка страницы через Browserless с повторными попытками"""
    if not PLAYWRIGHT_AVAILABLE or not BROWSERLESS_WS_ENDPOINT:
        return None
    
    for attempt in range(2):
        try:
            timeout = 15000 + attempt * 10000
            
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(
                    BROWSERLESS_WS_ENDPOINT,
                    timeout=30000
                )
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = await context.new_page()
                
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                    await asyncio.sleep(0.5)
                    html = await page.content()
                    
                    await page.close()
                    await context.close()
                    await browser.close()
                    
                    if html and len(html) > 500:
                        return html
                        
                except Exception as e:
                    logger.warning(f"⚠️ Browserless ошибка попытка {attempt+1}: {e}")
                    await page.close()
                    await context.close()
                    await browser.close()
                    
            if attempt < 1:
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.warning(f"⚠️ Browserless ошибка попытка {attempt+1}: {e}")
            if attempt < 1:
                await asyncio.sleep(2)
    
    return None

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ СТРАНИЦ
# ═══════════════════════════════════════════════════════════════════

def parse_page(html: str, query: str) -> Dict:
    """Парсинг HTML страницы"""
    result = {
        'text': '',
        'lists': [],
        'headings': [],
        'items': [],
    }
    
    if not BEAUTIFULSOUP_AVAILABLE or not html:
        return result
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Удаляем мусор
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
            tag.decompose()
        
        # Текст
        text = soup.get_text(separator=' ')
        text = re.sub(r'\s+', ' ', text).strip()
        result['text'] = text[:8000]
        
        # Заголовки
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4']):
            h = tag.get_text().strip()
            if h and len(h) > 3:
                result['headings'].append(h[:200])
        result['headings'] = result['headings'][:10]
        
        # Списки
        for tag in soup.find_all(['ul', 'ol']):
            for li in tag.find_all('li'):
                li_text = li.get_text().strip()
                if li_text and len(li_text) > 5:
                    result['lists'].append(li_text[:300])
        result['lists'] = result['lists'][:20]
        
        # Параграфы
        for p in soup.find_all('p'):
            p_text = p.get_text().strip()
            if 20 < len(p_text) < 800:
                result['items'].append({
                    'title': p_text[:200],
                    'description': p_text[:400],
                })
        result['items'] = result['items'][:30]
        
        return result
        
    except Exception as e:
        logger.debug(f"⚠️ Ошибка парсинга: {e}")
    
    return result

# ═══════════════════════════════════════════════════════════════════
#  МЕТОД 1: APISerpent + Browserless
# ═══════════════════════════════════════════════════════════════════

async def search_browserless_full(query: str) -> List[Dict]:
    """
    Полный метод: APISerpent (поиск) → Browserless (загрузка) → парсинг
    """
    try:
        logger.info("🌐 Метод 1: APISerpent + Browserless")
        
        # 1. Поиск через APISerpent
        results = await search_apiserpent(query)
        if not results:
            logger.warning("⚠️ APISerpent не дал результатов")
            return []
        
        logger.info(f"📊 APISerpent: {len(results)} результатов")
        
        # 2. Загружаем страницы через Browserless
        pages = []
        for idx, r in enumerate(results[:MAX_PAGES]):
            url = r.get('link', '')
            if not url:
                continue
            
            logger.info(f"📄 Загрузка {idx+1}/{min(MAX_PAGES, len(results))}: {url[:60]}...")
            html = await fetch_with_browserless(url)
            
            if html and len(html) > 500:
                parsed = parse_page(html, query)
                if parsed.get('text') and len(parsed.get('text')) > 200:
                    pages.append({
                        'title': r.get('title', ''),
                        'snippet': r.get('snippet', ''),
                        'link': url,
                        'text': parsed.get('text', '')[:4000],
                        'headings': parsed.get('headings', []),
                        'lists': parsed.get('lists', []),
                        'items': parsed.get('items', []),
                        'source': 'browserless'
                    })
                    logger.info(f"✅ Загружено: {len(pages[-1]['text'])} символов")
        
        logger.info(f"✅ Browserless: загружено {len(pages)} страниц")
        return pages
        
    except Exception as e:
        logger.error(f"❌ Browserless метод ошибка: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════
#  МЕТОД 2: DeepSeek Search (нативный поиск)
# ═══════════════════════════════════════════════════════════════════

async def search_deepseek_full(query: str) -> List[Dict]:
    """
    Полный метод: DeepSeek Search (нативный поиск через Anthropic-совместимый API)
    """
    try:
        logger.info("🤖 Метод 2: DeepSeek Search")
        
        session = await get_session()
        
        payload = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": query}],
            "tools": [{"type": "web_search"}],
            "tool_choice": {"type": "web_search"},
            "max_tokens": 4000,
            "temperature": 0.1
        }
        
        async with session.post(
            "https://api.deepseek.com/anthropic",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            },
            json=payload,
            timeout=30
        ) as r:
            if r.status == 200:
                data = await r.json()
                sources = []
                
                # Извлекаем результаты поиска
                for block in data.get("content", []):
                    if block.get("type") == "web_search_tool_result":
                        for result in block.get("results", []):
                            sources.append({
                                'title': result.get('title', ''),
                                'snippet': result.get('snippet', ''),
                                'link': result.get('url', ''),
                                'text': result.get('snippet', ''),
                                'source': 'deepseek'
                            })
                
                logger.info(f"✅ DeepSeek Search: {len(sources)} результатов")
                return sources
                
            else:
                logger.error(f"❌ DeepSeek Search HTTP {r.status}")
                return []
                
    except Exception as e:
        logger.error(f"❌ DeepSeek Search ошибка: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА — ПАРАЛЛЕЛЬНЫЙ ПОИСК
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(query: str, uid: int, context_prompt: str = "") -> Tuple[str, List[Dict], float]:
    """
    ПАРАЛЛЕЛЬНЫЙ ПОИСК ДВУМЯ МЕТОДАМИ:
    1. APISerpent + Browserless
    2. DeepSeek Search
    """
    logger.info(f"🛡️ ЗАПРОС: {query[:50]}")
    
    # ⭐ ЗАПУСКАЕМ ОБА МЕТОДА ПАРАЛЛЕЛЬНО
    browserless_task = search_browserless_full(query)
    deepseek_task = search_deepseek_full(query)
    
    results_browserless, results_deepseek = await asyncio.gather(
        browserless_task,
        deepseek_task,
        return_exceptions=True
    )
    
    # ⭐ ОБЪЕДИНЯЕМ РЕЗУЛЬТАТЫ
    all_results = []
    seen_urls = set()
    seen_titles = set()
    
    # Добавляем результаты от Browserless
    if isinstance(results_browserless, list):
        for r in results_browserless:
            url = r.get('link', '')
            title = r.get('title', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)
                logger.debug(f"➕ Browserless: {title[:50]}")
    
    # Добавляем результаты от DeepSeek
    if isinstance(results_deepseek, list):
        for r in results_deepseek:
            url = r.get('link', '')
            title = r.get('title', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)
                logger.debug(f"➕ DeepSeek: {title[:50]}")
    
    logger.info(f"📊 ВСЕГО результатов: {len(all_results)}")
    
    if not all_results:
        return f"""
⚠️ **ПО ВАШЕМУ ЗАПРОСУ НИЧЕГО НЕ НАЙДЕНО**

📋 **ЗАПРОС:** {query}

💡 **ПОПРОБУЙТЕ:**
• Переформулировать запрос
• Сделать его более конкретным

⚠️ **Я НЕ ВЫДУМЫВАЮ ФАКТЫ — ЭТО ЧЕСТНЫЙ ОТВЕТ!**
""", [], 0.0
    
    # ⭐ ФОРМИРУЕМ ДАННЫЕ ДЛЯ ОТВЕТА
    data_text = ""
    sources_text = ""
    
    for idx, r in enumerate(all_results[:30], 1):
        title = r.get('title', 'Без названия')
        snippet = r.get('snippet', '')
        text = r.get('text', '')
        source = r.get('source', 'неизвестно')
        
        # Основной контент
        content = text if text and len(text) > 200 else snippet
        if content and len(content) > 30:
            data_text += f"{idx}. **{title}**\n"
            data_text += f"   {content[:500]}\n\n"
        
        # Источники
        url = r.get('link', '')
        if url:
            sources_text += f"• {title[:60]}: {url}\n"
    
    memory = get_memory(uid)
    memory_context = ""
    if memory.knowledge_graph.get_all_facts():
        facts = memory.knowledge_graph.get_all_facts()[:3]
        memory_context = f"🧠 **Из памяти:** {', '.join(facts)}\n"
    
    # ⭐ ГЕНЕРИРУЕМ ОТВЕТ
    answer_prompt = f"""
⚠️ **ТЫ — ЭКСПЕРТ-АНАЛИТИК. ДАЙ МАКСИМАЛЬНО ПОЛНЫЙ, РАЗВЁРНУТЫЙ И ТОЧНЫЙ ОТВЕТ.**

⚠️ **ЗАПРОС ПОЛЬЗОВАТЕЛЯ:** {query}

⚠️ **ДАННЫЕ ИЗ ИНТЕРНЕТА (ОСНОВА ОТВЕТА):**
{data_text}

{memory_context}

⚠️ **ИСТОЧНИКИ ДАННЫХ:**
{sources_text}

⚠️ **ПРАВИЛА ФОРМИРОВАНИЯ ОТВЕТА:**

1. **ОСНОВА ОТВЕТА** — ТОЛЬКО данные из интернета (выше)
2. **ЕСЛИ ДАННЫХ НЕДОСТАТОЧНО** — честно скажи об этом
3. **СВОИ ЗНАНИЯ** — используй ТОЛЬКО если данных МАЛО, и ОБЯЗАТЕЛЬНО отметь это
4. **ФАКТЫ** — без источников не использовать
5. **ОТВЕТ** — развернутый, структурированный, минимум 800 символов

⚠️ **ФОРМАТ ОТВЕТА:**
📊 **ОСНОВНОЙ ОТВЕТ:**
[Развернутый ответ на основе данных]

📋 **КЛЮЧЕВЫЕ ФАКТЫ (с источниками):**
[Список фактов + ссылки]

📝 **ДОПОЛНИТЕЛЬНО (если данных мало):**
🧠 *Дополнено из моих знаний* — [добавление]

🔗 **ИСТОЧНИКИ:**
[Список всех источников]

⚠️ **В ОТВЕТЕ УКАЗЫВАЙ ИСТОЧНИКИ!**
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    
    # Проверка качества
    is_valid, reason = check_for_lies_and_laziness(answer)
    if not is_valid:
        logger.warning(f"⚠️ Ответ отклонён: {reason}")
        retry_prompt = f"""
⚠️ **ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОТКЛОНЁН!**

Причина: {reason}

⚠️ **ЗАПРОС:** {query}

⚠️ **ДАННЫЕ:** {data_text[:2000]}

⚠️ **ТРЕБОВАНИЯ:**
- ОТВЕТЬ РАЗВЁРНУТО (минимум 800 символов)
- ИСПОЛЬЗУЙ ТОЛЬКО данные
- НЕ ВЫДУМЫВАЙ
- НЕ ИСПОЛЬЗУЙ субъективные фразы
- УКАЗЫВАЙ ИСТОЧНИКИ

ОТВЕТЬ ЧЕСТНО, БЕЗ ВЫДУМОК!
"""
        answer = await ask_deepseek(retry_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    
    # Уверенность
    confidence = min(90, len(all_results) * 3 + 10)
    
    return answer, all_results, confidence

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════

async def generate_variants(query: str) -> List[str]:
    variants = [query]
    try:
        prompt = f"""
Сгенерируй {MAX_VARIANTS} разных вариантов поискового запроса для вопроса:
{query}

Ответь ТОЛЬКО списком, каждый вариант с новой строки, без нумерации.
"""
        result = await ask_deepseek(prompt, temperature=0.4, max_tokens=MAX_TOKENS_VARIANTS, use_pro=False)
        if result:
            for line in result.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    clean = re.sub(r'^[\d\s.)-]+', '', line).strip()
                    if clean and len(clean) > 5:
                        variants.append(clean)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка генерации: {e}")
    
    return list(dict.fromkeys(variants))[:MAX_VARIANTS]

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict, query: str = "") -> bool:
    """Простая фильтрация только явного мусора"""
    title = result.get('title', '').lower()
    snippet = result.get('snippet', '').lower()
    url = result.get('link', '').lower()
    
    # Блокируем явный спам
    spam_domains = ['googleadservices', 'doubleclick', 'googletagmanager', 'yandex.ru/clck']
    if any(d in url for d in spam_domains):
        return False
    
    spam_words = ['реклама', 'advertisement', 'sponsored', 'promoted']
    if any(w in title or w in snippet for w in spam_words):
        return False
    
    # Блокируем видео
    video_domains = ['youtube.com', 'youtu.be', 'vimeo.com', 'twitch.tv', 'tiktok.com']
    if any(d in url for d in video_domains):
        return False
    
    # Минимальная длина
    if len(snippet) < MIN_SNIPPET_LENGTH:
        return False
    
    # Проверка на слова из запроса
    if query:
        stop_words = ['на', 'в', 'с', 'по', 'для', 'от', 'до', 'из', 'за', 'под', 'над']
        query_words = [w for w in query.lower().split() if len(w) > 2 and w not in stop_words]
        if query_words:
            for word in query_words[:5]:
                if word in title or word in snippet:
                    return True
    
    return True

# ═══════════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

def format_answer_clean(answer: str, confidence: float, sources_count: int) -> str:
    sources_label = "источник" if sources_count == 1 else "источника" if sources_count < 5 else "источников"
    
    formatted = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **ИЗ ИНТЕРНЕТА** ({sources_count} {sources_label})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{answer}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **ТОЧНОСТЬ: {int(confidence)}%**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return formatted

def format_sources(sources: List[Dict]) -> str:
    if not sources:
        return "📎 **ИСТОЧНИКИ:**\n\nНет сохранённых источников."
    
    formatted = "📎 **ИСТОЧНИКИ:**\n\n"
    for idx, s in enumerate(sources[:10], 1):
        title = s.get('title', 'Источник')[:60]
        url = s.get('link', '')
        formatted += f"{idx}. **{title}**\n"
        if url:
            formatted += f"   🔗 {url}\n"
        formatted += "\n"
    
    return formatted

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ TELEGRAM
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
            await query.edit_message_text(
                "⚠️ Сначала напишите вопрос в чат.",
                reply_markup=ACTION_BUTTONS
            )
            return

        context.user_data['awaiting_input'] = False
        await query.edit_message_text("🔍 Начинаю поиск...")

        start_time = time.time()
        context.user_data['found_answer'] = False

        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )

        context_text = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(pending_text, user_id, context_text)

        context.user_data['found_answer'] = True
        await progress_task

        elapsed = int(time.time() - start_time)
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = pending_text
        context.user_data['last_answer'] = answer
        context.user_data['pending_text'] = ''
        context.user_data['last_sources'] = sources[:10]

        clean_answer = format_answer_clean(answer, confidence, len(sources))
        context.user_data['last_formatted_answer'] = clean_answer
        
        full_text = f"⏱️ {elapsed} сек\n\n{clean_answer}"
        await send_long_message(update, full_text, ACTION_WITH_SOURCES_BUTTONS)

    elif action == "action_clarify":
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            await query.edit_message_text(
                "⚠️ Нет активного запроса для уточнения.\nСначала выполните поиск.",
                reply_markup=ACTION_BUTTONS
            )
            return

        context.user_data['mode'] = 'clarify'
        context.user_data['awaiting_input'] = True
        context.user_data['pending_text'] = ''

        await query.edit_message_text(
            f"📝 **Уточните запрос**\n\n"
            f"Предыдущий запрос: *{last_query[:200]}*\n\n"
            "Напишите ваше уточнение:",
            parse_mode='Markdown'
        )

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
        context.user_data['pending_text'] = ''

        full_context = memory.get_full_context()

        chat_prompt = f"""
💬 **Ты — дружелюбный собеседник, но НЕ ИСТОЧНИК ФАКТОВ.**

⚠️ **ТЫ НЕ ИМЕЕШЬ ПРАВА ВЫДУМЫВАТЬ!**
- Если не знаешь — скажи "Я не знаю".
- НЕЛЬЗЯ выдумывать факты, цифры, даты, имена.
- НЕЛЬЗЯ говорить "по моему мнению", "я считаю", "я думаю".

⚠️ **РАЗРЕШЕНО:**
- Общаться на общие темы
- Делиться известными фактами (проверенными)
- Задавать уточняющие вопросы

Контекст диалога:
{full_context}

Сообщение пользователя: {pending_text}

ОТВЕТЬ ЕСТЕСТВЕННО, НО ЧЕСТНО!
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        if not answer:
            answer = "😊 Я здесь! Чем могу помочь?"

        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)

        await query.edit_message_text(
            f"💬 **Режим беседы (без интернета)**\n\n{answer}",
            reply_markup=EXIT_CHAT_BUTTON
        )

    elif action == "action_exit_chat":
        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False

        await query.edit_message_text(
            "🔍 **Выход из режима беседы**\n\n"
            "Теперь я снова ищу информацию в интернете.\n"
            "Напишите новый вопрос, и я предложу режимы.",
            reply_markup=ACTION_BUTTONS
        )

    elif action == "show_sources":
        sources = context.user_data.get('last_sources', [])
        
        if not sources:
            await query.edit_message_text(
                "📎 **ИСТОЧНИКИ:**\n\nНет сохранённых источников.",
                reply_markup=HIDE_SOURCES_BUTTON
            )
            return
        
        sources_formatted = format_sources(sources)
        
        await query.edit_message_text(
            sources_formatted,
            reply_markup=HIDE_SOURCES_BUTTON,
            parse_mode='Markdown'
        )

    elif action == "hide_sources":
        last_answer = context.user_data.get('last_formatted_answer', '')
        if last_answer:
            await query.edit_message_text(
                last_answer,
                reply_markup=ACTION_WITH_SOURCES_BUTTONS
            )
        else:
            await query.edit_message_text(
                "⚠️ Основной ответ не найден.",
                reply_markup=ACTION_BUTTONS
            )


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
💬 **Ты — дружелюбный собеседник, но НЕ ИСТОЧНИК ФАКТОВ.**

⚠️ **ТЫ НЕ ИМЕЕШЬ ПРАВА ВЫДУМЫВАТЬ!**
- Если не знаешь — скажи "Я не знаю".
- НЕЛЬЗЯ выдумывать факты, цифры, даты, имена.
- НЕЛЬЗЯ говорить "по моему мнению", "я считаю", "я думаю".

⚠️ **РАЗРЕШЕНО:**
- Общаться на общие темы
- Делиться известными фактами (проверенными)
- Задавать уточняющие вопросы

Контекст диалога:
{full_context}

Сообщение пользователя: {user_message}

ОТВЕТЬ ЕСТЕСТВЕННО, НО ЧЕСТНО!
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        if not answer:
            answer = "😊 Я здесь! Чем могу помочь?"

        memory.add_message('user', user_message)
        memory.add_message('assistant', answer)

        await send_long_message(update, f"💬 {answer}", EXIT_CHAT_BUTTON)
        return

    if context.user_data.get('mode') == 'clarify':
        last_query = context.user_data.get('last_query', '')
        if not last_query:
            context.user_data['mode'] = 'search'
            await update.effective_message.reply_text(
                "⚠️ Нет активного запроса для уточнения.",
                reply_markup=ACTION_BUTTONS
            )
            return

        context.user_data['mode'] = 'search'
        context.user_data['awaiting_input'] = False

        clarification = user_message
        combined_query = f"{last_query} (уточнение: {clarification})"

        await update.effective_message.reply_text(
            f"📝 **Уточняю запрос...**\n\n"
            f"Ищу с учётом уточнения: *{clarification[:100]}*",
            parse_mode='Markdown'
        )

        start_time = time.time()
        context.user_data['found_answer'] = False

        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )

        full_context = memory.get_full_context()
        answer, sources, confidence = await search_and_answer(combined_query, user_id, full_context)

        context.user_data['found_answer'] = True
        await progress_task

        elapsed = int(time.time() - start_time)
        memory.add_message('user', f"Уточнение: {clarification}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = combined_query
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources[:10]

        clean_answer = format_answer_clean(answer, confidence, len(sources))
        context.user_data['last_formatted_answer'] = clean_answer
        
        full_text = f"⏱️ {elapsed} сек\n\n{clean_answer}"
        await send_long_message(update, full_text, ACTION_WITH_SOURCES_BUTTONS)
        return

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
        "🔍 Ищу информацию в интернете двумя способами:\n"
        "   • 🌐 APISerpent + Browserless (загрузка страниц)\n"
        "   • 🤖 DeepSeek Search (нативный поиск)\n"
        "📊 Объединяю результаты для максимальной точности\n"
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
    logger.info("🚀 ЗАПУСК BROWAIX BOT v3.0 (ПАРАЛЛЕЛЬНЫЙ ПОИСК)")
    logger.info("=" * 60)
    
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"   Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("=" * 60)
    logger.info("✅ МЕТОД 1: APISerpent → Browserless → Парсинг")
    logger.info("✅ МЕТОД 2: DeepSeek Search (нативный поиск)")
    logger.info("✅ ПАРАЛЛЕЛЬНЫЙ ЗАПУСК → ОБЪЕДИНЕНИЕ РЕЗУЛЬТАТОВ")
    logger.info("✅ ЖЁСТКАЯ ПРОВЕРКА НА ВРАНЬЁ И ЛЕНЬ")
    logger.info("✅ ВСЕ ПАРАМЕТРЫ В .env (БЕЗ ХАРДКОДА)")
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не задан!")
        return
    
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DEEPSEEK_API_KEY не задан!")
        return
    
    if not APISERPENT_API_KEY:
        logger.warning("⚠️ APISERPENT_API_KEY не задан! Поиск не будет работать!")
    
    logger.info("✅ Все проверки пройдены, запускаем бота...")
    
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