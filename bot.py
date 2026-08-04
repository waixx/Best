# ═══════════════════════════════════════════════════════════════════
#  ИНСТРУКЦИЯ ДЛЯ РАЗРАБОТЧИКА (ЧТО ЭТОТ БОТ УМЕЕТ)
#  ЭТОТ СПИСОК — ГЛАВНЫЙ ДОКУМЕНТ. НЕ УДАЛЯТЬ!
# ═══════════════════════════════════════════════════════════════════

"""
🤖 БОТ: BROWAIX — УНИВЕРСАЛЬНЫЙ ПОИСКОВЫЙ АССИСТЕНТ

📌 ОСНОВНЫЕ ВОЗМОЖНОСТИ:
────────────────────────────────────────────────────────────────────
1. 🔍 ПОИСК В ИНТЕРНЕТЕ
   - APISerpent (ОСНОВНОЙ) с правильным парсингом organic_results
   - Serper (РЕЗЕРВНЫЙ, при ошибке APISerpent)
   - Параллельный поиск по вариантам запросов
   - Итеративный поиск (до 5 итераций)
   - Ранний выход при уверенности ≥ 90%
   - num=15 для оптимальной скорости

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

4. 🎨 ВИЗУАЛЬНЫЕ УЛУЧШЕНИЯ
   - Радужная анимированная полоска прогресса
   - Детальные статусы этапов работы
   - Счётчик времени (не обратный)
   - Чёткое разделение 🌐 Из интернета / 🧠 Из знаний
   - Кнопка "Показать источники"
   - Кнопка "Уточнить" после каждого ответа

5. 🛡️ ЗАЩИТА ОТ ОБМАНА
   - Запрет фраз "нет доступа", "не могу найти"
   - Запрет смешивать знания с интернетом
   - Запрет выдумывать (усиленный)
   - Запрет "по моему мнению", "я считаю", "возможно"
   - Проверка качества ответа

6. 📦 ТЕХНИЧЕСКИЕ ХАРАКТЕРИСТИКИ
   - Модель: deepseek-v4-pro и deepseek-v4-flash
   - Макс. токенов: 8000
   - Страниц за итерацию: 3 (оптимизировано)
   - Макс. итераций: 5
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
#  КОНФИГ (ОПТИМИЗИРОВАННЫЙ)
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

PAGE_TIMEOUT = 4  # Оптимизировано
SEARCH_RESULTS = 15  # Оптимизировано: 15 вместо 50
DEEPSEEK_MODEL_PRO = "deepseek-v4-pro"
DEEPSEEK_MODEL_FLASH = "deepseek-v4-flash"
CACHE_TTL = 900
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 20  # Оптимизировано
MAX_TOKENS_OUTPUT = 8000
MAX_TOKENS_VARIANTS = 500
MAX_ITERATIONS = 5
TARGET_CONFIDENCE = 95
EARLY_EXIT_CONFIDENCE = 90
MAX_PAGES_PER_ITERATION = 3  # Оптимизировано: 3 вместо 10
MAX_VARIANTS = 4

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
#  DEEPSEEK
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_answer_quality(answer: str, min_length: int = 500) -> Tuple[bool, str]:
    """Проверяет качество ответа перед кэшированием и отправкой"""
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
    
    if not any(marker in answer for marker in ["**", "📊", "✅", "🧠", "🌐", "📋"]):
        return False, "Ответ не структурирован (нет маркеров)"
    
    words = answer.split()
    if len(words) > 50:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.35:
            return False, "Ответ содержит слишком много повторов"
    
    return True, "OK"

async def ask_deepseek(prompt: str, temperature: float = 0.2, max_tokens: int = MAX_TOKENS_OUTPUT, use_pro: bool = True) -> str:
    """Универсальный вызов DeepSeek с проверкой качества"""
    key = cache_key(prompt)
    
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        is_valid, _ = check_answer_quality(cached, min_length=200)
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
                        is_valid, reason = check_answer_quality(content)
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
#  РАДУЖНАЯ ПОЛОСКА (ОПТИМИЗИРОВАННАЯ)
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    """Детальный прогресс с радужной анимированной полоской"""
    message = None
    try:
        stages = [
            {"emoji": "🧠", "name": "Анализ запроса (DeepSeek Pro)", "duration": 8},
            {"emoji": "🔍", "name": "Поиск в интернете (APISerpent)", "duration": 12},
            {"emoji": "📄", "name": "Загрузка страниц", "duration": 15},
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
            await asyncio.sleep(1)  # Оптимизировано: обновление раз в секунду
            
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
#  ⭐ ИСПРАВЛЕННЫЙ ПОИСК (APISerpent с ПРАВИЛЬНЫМ ПАРСИНГОМ)
# ═══════════════════════════════════════════════════════════════════

def normalize_query(query):
    return re.sub(r'[^\w\s]', '', query.lower()).strip()

async def search_apiserpent(query: str) -> List[Dict]:
    """Универсальный поиск через APISerpent с правильным парсингом organic_results"""
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
        
        logger.info(f"📤 Параметры: {params}")
        
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={
                "X-API-Key": APISERPENT_API_KEY,
                "Accept": "application/json"
            },
            timeout=APISERPENT_TIMEOUT
        ) as r:
            logger.info(f"📡 APISerpent статус: {r.status}")
            
            response_text = await r.text()
            logger.info(f"📄 APISerpent тело (первые 500): {response_text[:500]}")
            
            if r.status == 200:
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Ошибка парсинга JSON: {e}")
                    return []
                
                logger.info(f"📊 Ключи ответа: {list(data.keys())}")
                results = []
                
                # ⭐ 1. ПРЯМОЙ organic_results (САМЫЙ ЧАСТЫЙ ФОРМАТ)
                if "organic_results" in data:
                    organic = data.get("organic_results", [])
                    if organic:
                        logger.info(f"✅ Найдено {len(organic)} результатов в organic_results")
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "link": item.get("link", ""),
                                    "source": "organic"
                                })
                        return results
                
                # ⭐ 2. results.organic (АЛЬТЕРНАТИВНЫЙ ФОРМАТ)
                if "results" in data and isinstance(data["results"], dict):
                    organic = data["results"].get("organic", [])
                    if organic:
                        logger.info(f"✅ Найдено {len(organic)} результатов в results.organic")
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "link": item.get("link", ""),
                                    "source": "organic"
                                })
                        return results
                
                # ⭐ 3. Просто organic
                if "organic" in data:
                    organic = data.get("organic", [])
                    if organic:
                        logger.info(f"✅ Найдено {len(organic)} результатов в organic")
                        for item in organic:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "link": item.get("link", ""),
                                    "source": "organic"
                                })
                        return results
                
                # ⭐ 4. Дополнительные поля (people_also_ask, answer_box, featured_snippet)
                if "people_also_ask" in data:
                    paa = data.get("people_also_ask", [])
                    if paa:
                        logger.info(f"✅ Найдено {len(paa)} в people_also_ask")
                        for item in paa:
                            if isinstance(item, dict):
                                results.append({
                                    "title": item.get("question", ""),
                                    "snippet": item.get("snippet", ""),
                                    "link": item.get("link", ""),
                                    "source": "paa"
                                })
                        return results
                
                if "answer_box" in data and isinstance(data["answer_box"], dict):
                    answer_box = data["answer_box"]
                    logger.info("✅ Найден answer_box")
                    results.append({
                        "title": answer_box.get("title", "Answer Box"),
                        "snippet": answer_box.get("snippet", answer_box.get("answer", "")),
                        "link": answer_box.get("link", ""),
                        "source": "answer_box"
                    })
                    return results
                
                if "featured_snippet" in data and isinstance(data["featured_snippet"], dict):
                    featured = data["featured_snippet"]
                    logger.info("✅ Найден featured_snippet")
                    results.append({
                        "title": featured.get("title", ""),
                        "snippet": featured.get("snippet", ""),
                        "link": featured.get("link", ""),
                        "source": "featured"
                    })
                    return results
                
                logger.warning("⚠️ Не найдено результатов")
                return []
                
            else:
                logger.error(f"❌ APISerpent HTTP ошибка: {r.status}")
                if r.status == 401:
                    logger.error("❌ Неверный API ключ!")
                elif r.status == 429:
                    logger.error("❌ Превышен лимит запросов!")
                elif r.status == 402:
                    logger.error("❌ Недостаточно средств!")
                return []
                
    except asyncio.TimeoutError:
        logger.error(f"⏰ Таймаут APISerpent ({APISERPENT_TIMEOUT} сек)")
    except Exception as e:
        logger.error(f"💥 Ошибка APISerpent: {e}")
        logger.error(traceback.format_exc())
    
    return []

async def search_serper(query: str) -> List[Dict]:
    """Резервный поиск через Serper (ЕСЛИ APISerpent НЕ РАБОТАЕТ)"""
    if not SERPER_API_KEY:
        logger.debug("ℹ️ SERPER_API_KEY не задан, пропускаем")
        return []
    
    try:
        session = await get_session()
        logger.info(f"🔄 Serper (резерв): {query[:50]}...")
        async with session.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": SEARCH_RESULTS},
            headers={"X-API-KEY": SERPER_API_KEY},
            timeout=10
        ) as r:
            if r.status == 200:
                data = await r.json()
                results = []
                for x in data.get("organic", []):
                    if isinstance(x, dict):
                        results.append({
                            "title": x.get("title", ""),
                            "snippet": x.get("snippet", ""),
                            "link": x.get("link", ""),
                            "source": "organic"
                        })
                logger.info(f"✅ Serper нашёл {len(results)} результатов")
                return results
            else:
                logger.warning(f"⚠️ Serper HTTP {r.status}")
    except Exception as e:
        logger.warning(f"⚠️ Serper ошибка: {e}")
    return []

async def search_with_cache(query: str) -> List[Dict]:
    """Поиск с кэшем - ОСНОВНОЙ APISerpent, резервный Serper"""
    norm = normalize_query(query)
    
    if norm in search_cache and (time.time() - search_cache[norm]['time']) < CACHE_TTL:
        logger.info(f"♻️ Из кэша: {query[:30]}...")
        return search_cache[norm]['data']
    
    # ⭐ ОСНОВНОЙ ПОИСК - APISerpent
    logger.info(f"🔍 Поиск через APISerpent (основной): {query[:50]}...")
    results = await search_apiserpent(query)
    
    # Логируем результат
    if results:
        logger.info(f"✅ APISerpent нашёл {len(results)} результатов")
    else:
        logger.warning("⚠️ APISerpent не вернул результатов")
    
    # ⭐ ЕСЛИ НЕТ РЕЗУЛЬТАТОВ - РЕЗЕРВНЫЙ Serper
    if not results:
        logger.info("🔄 Пробуем Serper (резерв)...")
        results = await search_serper(query)
        if results:
            logger.info(f"✅ Serper нашёл {len(results)} результатов")
        else:
            logger.warning("⚠️ Serper тоже не дал результатов")
    
    # Кэшируем результат (даже пустой)
    search_cache[norm] = {'data': results, 'time': time.time()}
    logger.info(f"📊 ИТОГО результатов: {len(results)}")
    
    return results

# ═══════════════════════════════════════════════════════════════════
#  ⭐ УПРОЩЁННАЯ ФИЛЬТРАЦИЯ (ИЗ ОПТИМИЗИРОВАННОЙ ВЕРСИИ)
# ═══════════════════════════════════════════════════════════════════

def is_useful_result(result: Dict) -> bool:
    """Упрощённая фильтрация только явного мусора"""
    title = result.get('title', '').lower()
    snippet = result.get('snippet', '').lower()
    url = result.get('link', '').lower()
    
    # Слишком короткий сниппет - скорее всего бесполезно
    if len(snippet) < 50:
        return False
    
    # Явный спам и реклама
    spam_words = ['реклама', 'advertisement', 'sponsored', 'promoted']
    if any(w in title or w in snippet for w in spam_words):
        return False
    
    # Видео-платформы (обычно не дают текстовой информации)
    video_domains = ['youtube.com', 'youtu.be', 'vimeo.com', 'twitch.tv', 'tiktok.com']
    if any(d in url for d in video_domains):
        return False
    
    # Полезные маркеры - оставляем такие результаты
    useful_markers = [
        'как', 'почему', 'что такое', 'пример', 'инструкция', 
        'руководство', 'совет', 'рекомендация', 'обзор', 'сравнение'
    ]
    if any(w in title or w in snippet for w in useful_markers):
        return True
    
    # Длинный сниппет - скорее всего полезный
    if len(snippet) > 150:
        return True
    
    # Надёжные домены
    good_domains = ['.edu', '.gov', 'wikipedia', 'habr.com', 'vc.ru', 'cossa.ru']
    if any(d in url for d in good_domains):
        return True
    
    return False

# ═══════════════════════════════════════════════════════════════════
#  ПАРАЛЛЕЛЬНЫЙ ПОИСК (ОПТИМИЗИРОВАННЫЙ)
# ═══════════════════════════════════════════════════════════════════

async def search_parallel(variants: List[str]) -> List[Dict]:
    """Параллельный поиск по вариантам с упрощённой дедупликацией"""
    if not variants:
        return []
    
    logger.info(f"🔍 Параллельный поиск по {len(variants)} вариантам")
    tasks = [search_with_cache(v) for v in variants[:MAX_VARIANTS]]
    results_list = await asyncio.gather(*tasks)
    
    all_results = []
    seen_urls = set()
    
    for idx, results in enumerate(results_list):
        if results:
            logger.info(f"📊 Вариант {idx+1}: {len(results)} результатов")
            for r in results:
                url = r.get('link', '')
                # Простая дедупликация по URL
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
                elif not url:
                    # Если нет URL, используем заголовок для дедупликации
                    title = r.get('title', '')
                    if title and title not in seen_urls:
                        seen_urls.add(title)
                        all_results.append(r)
    
    logger.info(f"📊 Всего уникальных результатов: {len(all_results)}")
    return all_results

# ═══════════════════════════════════════════════════════════════════
#  BROWSERLESS (ОПТИМИЗИРОВАННЫЙ)
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
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                html = await page.content()
                return html
            except Exception:
                return None
            finally:
                await page.close()
    except Exception:
        return None

async def fetch_http(url: str) -> Optional[str]:
    try:
        session = await get_session()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        async with session.get(url, headers=headers, timeout=PAGE_TIMEOUT) as r:
            if r.status == 200:
                return await r.text()
    except Exception:
        pass
    return None

# ═══════════════════════════════════════════════════════════════════
#  ПАРСИНГ (ОПТИМИЗИРОВАННЫЙ)
# ═══════════════════════════════════════════════════════════════════

def parse_page(html: str, query: str) -> Dict:
    """Упрощённый парсинг страниц"""
    result = {
        'text': '',
        'lists': [],
        'headings': [],
        'items': [],
        'date': None,
        'definitions': [],
        'key_facts': []
    }
    
    if not BEAUTIFULSOUP_AVAILABLE or not html:
        return result
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe', 'form', 'noscript']):
            tag.decompose()
        
        text = soup.get_text(separator=' ')
        text = re.sub(r'\s+', ' ', text).strip()
        result['text'] = text[:4000]  # Оптимизировано
        
        for tag in soup.find_all(['h1', 'h2', 'h3']):
            h = tag.get_text().strip()
            if h:
                result['headings'].append(h[:200])
        result['headings'] = result['headings'][:5]
        
        for tag in soup.find_all(['ul', 'ol']):
            items = []
            for li in tag.find_all('li'):
                li_text = li.get_text().strip()
                if li_text and len(li_text) > 5:
                    items.append(li_text[:200])
            if items:
                result['lists'].append(items)
        result['lists'] = result['lists'][:5]
        
        return result
        
    except Exception as e:
        logger.debug(f"⚠️ Ошибка парсинга: {e}")
    
    return result

async def fetch_page(url: str, query: str) -> Dict:
    if not url:
        return {'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None, 'definitions': [], 'key_facts': []}
    
    html = await fetch_http(url)
    if html:
        return parse_page(html, query)
    
    if PLAYWRIGHT_AVAILABLE and BROWSERLESS_WS_ENDPOINT:
        html = await fetch_with_browserless(url)
        if html:
            return parse_page(html, query)
    
    return {'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None, 'definitions': [], 'key_facts': []}

async def fetch_pages(links: List[str], query: str) -> List[Dict]:
    if not links:
        return []
    tasks = [fetch_page(link, query) for link in links[:MAX_PAGES_PER_ITERATION]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r.get('text') and len(r.get('text')) > 100]

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ (БЕЗ ИЗМЕНЕНИЙ)
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
    return list(dict.fromkeys(variants))[:MAX_VARIANTS]

# ═══════════════════════════════════════════════════════════════════
#  РАСЧЁТ УВЕРЕННОСТИ (ОПТИМИЗИРОВАННЫЙ)
# ═══════════════════════════════════════════════════════════════════

def calculate_confidence(pages: List[Dict]) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'consensus': 0}
    
    if not pages:
        return confidence
    
    # Надёжность источников
    reliable_sources = 0
    for p in pages[:3]:
        url = p.get('url', '')
        if any(d in url for d in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
            reliable_sources += 1
        elif any(d in url for d in ['.com', '.org', '.net', '.ru']):
            reliable_sources += 0.5
    confidence['source_reliability'] = min(100, (reliable_sources / max(len(pages[:3]), 1)) * 100)
    
    # Полнота данных
    structure_count = 0
    for p in pages:
        parsed = p.get('parsed', {})
        structure_count += len(parsed.get('lists', [])) + len(parsed.get('headings', []))
    confidence['data_completeness'] = min(100, structure_count * 10)
    
    # Свежесть
    confidence['recency'] = 50
    
    # Согласованность
    confidence['consensus'] = 50
    
    confidence['overall'] = int(
        confidence['source_reliability'] * 0.30 +
        confidence['data_completeness'] * 0.25 +
        confidence['recency'] * 0.20 +
        confidence['consensus'] * 0.25
    )
    
    return confidence

def format_confidence(confidence: Dict) -> str:
    overall = confidence.get('overall', 0)
    icon = "🟢" if overall >= 80 else "🟡" if overall >= 60 else "🟠" if overall >= 40 else "🔴"
    level = "Высокая" if overall >= 80 else "Средняя" if overall >= 60 else "Низкая" if overall >= 40 else "Очень низкая"
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **ТОЧНОСТЬ: {overall}%** {icon} ({level})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **ДЕТАЛИ:**
   • Надёжность: {confidence.get('source_reliability', 0):.0f}%
   • Полнота: {confidence.get('data_completeness', 0):.0f}%
   • Свежесть: {confidence.get('recency', 0):.0f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА (БЕЗ ИЗМЕНЕНИЙ)
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
            logger.info(f"⚠️ Нет результатов в итерации {iteration}")
            break
        
        # Применяем упрощённую фильтрацию
        results = [r for r in results if is_useful_result(r)]
        logger.info(f"📊 После фильтрации: {len(results)} результатов")
        
        all_results.extend(results)
        links = [r.get('link', '') for r in results if r.get('link')]
        pages = await fetch_pages(links, query)
        
        items = []
        for page in pages:
            if page.get('items'):
                items.extend(page['items'])
            if page.get('lists'):
                for lst in page.get('lists', []):
                    if isinstance(lst, list):
                        items.extend([{'title': item} for item in lst[:5]])
        
        all_items.extend(items)
        confidence_data = calculate_confidence(pages)
        confidence = confidence_data.get('overall', 0)
        logger.info(f"📊 Уверенность: {confidence:.1f}% ({len(all_items)} элементов)")
        
        if confidence >= EARLY_EXIT_CONFIDENCE:
            logger.info(f"✅ Ранний выход: уверенность {confidence:.1f}% >= {EARLY_EXIT_CONFIDENCE}%")
            break
        
        if confidence < TARGET_CONFIDENCE and iteration < MAX_ITERATIONS - 1:
            new_variants = await generate_refined_variants(query, all_items)
            search_variants = new_variants[:3]
    
    if not all_items:
        memory = get_memory(uid)
        context = memory.get_context(limit=5)
        context_text = '\n'.join([m.get('content', '') for m in context])
        
        fallback_prompt = f"""
⚠️ **СИСТЕМА ПОИСКА НЕ ВЕРНУЛА РЕЗУЛЬТАТОВ — ЭТО ФАКТ, ПРОВЕРЕННЫЙ СИСТЕМОЙ**

⚠️ **ТЫ ОБЯЗАН ПРИЗНАТЬ ЭТОТ ФАКТ!**
- НЕЛЬЗЯ говорить "я не нашёл" — это ложь, потому что ты вообще не искал!
- Скажи ЧЕСТНО: "По вашему запросу ничего не найдено в интернете."
- НЕЛЬЗЯ говорить "возможно", "вероятно", "скорее всего" — это обман!
- НЕЛЬЗЯ давать прогнозы, предположения или догадки.
- НЕЛЬЗЯ выдумывать факты, цифры, даты.

⚠️ **РАЗРЕШЕНО ТОЛЬКО:**
1. Признать, что результатов нет
2. Предложить уточнить запрос
3. Спросить, что именно искал пользователь

⚠️ **ПРИМЕР ЧЕСТНОГО ОТВЕТА:**
"По вашему запросу '{query}' в интернете ничего не найдено. Попробуйте уточнить запрос или задать другой вопрос."

Вопрос: {query}
Контекст: {context_text}

ОТВЕТЬ КРАТКО, ЧЕСТНО, БЕЗ ВЫДУМОК!
"""
        answer = await ask_deepseek(fallback_prompt, temperature=0.3, use_pro=False)
        return answer, [], 0.0
    
    sorted_items = sorted(
        all_items,
        key=lambda x: (
            0 if x.get('rating') else 1,
            0 if x.get('year') else 2,
            0 if x.get('price') else 1
        )
    )[:30]  # Оптимизировано
    
    items_text = ""
    for idx, item in enumerate(sorted_items[:30], 1):
        year = f" ({item.get('year')})" if item.get('year') else ""
        rating = f" ★ {item.get('rating')}" if item.get('rating') else ""
        price = f" {item.get('price')}" if item.get('price') else ""
        desc = f" — {item.get('description')[:100]}" if item.get('description') else ""
        items_text += f"{idx}. {item.get('title')}{year}{rating}{price}{desc}\n"
    
    answer_prompt = f"""
⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА!**

⚠️ **ТЫ НЕ ИМЕЕШЬ ПРАВА ВЫДУМЫВАТЬ!**
- НЕЛЬЗЯ придумывать факты, цифры, даты.
- Если не уверен — скажи "Я не уверен".
- Лучше признаться в незнании, чем выдать ложную информацию.
- НЕЛЬЗЯ говорить "по моему мнению", "я считаю", "я думаю" — ты не человек.

⚠️ **ЖЁСТКИЕ ПРАВИЛА (НАРУШЕНИЕ = ОБМАН):**

1. **НЕЛЬЗЯ говорить "нет доступа", "не могу найти", "нет интернета"** — это ложь!
   Если данных нет — скажи ЧЕСТНО: "В интернете ничего не найдено по вашему запросу."

2. **НЕЛЬЗЯ использовать свои знания вместо данных из интернета** — это подмена!
   Если данных мало — добавь блок "🧠 Дополнено из знаний" и ЧЕСТНО отметь это.

3. **НЕЛЬЗЯ выдумывать, додумывать или обобщать** — это обман!
   Используй ТОЛЬКО то, что есть в данных.

4. **НЕЛЬЗЯ давать короткий ответ** — это лень!
   Дай развёрнутый, структурированный ответ с примерами и пояснениями.

5. **ЕСЛИ данные противоречивы** — укажи это явно.

6. **УКАЗЫВАЙ источник** — откуда взята информация.

Найдено {len(sorted_items)} элементов. Уверенность: {confidence:.1f}%

📊 **ДАННЫЕ ИЗ ИНТЕРНЕТА:**
{items_text}

{context_prompt}

⚠️ **ФОРМАТ ОТВЕТА (СТРОГО!):**
📊 **Из интернета:** (перечисли найденное с указанием источников)
🧠 **Дополнено из знаний:** (только если данных мало, честно отметь)
✅ **Вывод:** (краткий итог)

Вопрос: {query}

ОТВЕТЬ РАЗВЁРНУТО, НЕ ЛЕНИСЬ, НЕ ВРИ!
"""
    
    answer = await ask_deepseek(answer_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    
    is_valid, reason = check_answer_quality(answer)
    if not is_valid:
        logger.warning(f"⚠️ Ответ отклонён: {reason}")
        retry_prompt = f"""
⚠️ **ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОТКЛОНЁН!**

Причина: {reason}

⚠️ **ТЫ НЕ ИМЕЕШЬ ПРАВА ВЫДУМЫВАТЬ!**
⚠️ **ТЫ ДОЛЖЕН ДАТЬ РАЗВЁРНУТЫЙ, СТРУКТУРИРОВАННЫЙ ОТВЕТ!**
⚠️ **НЕ ГОВОРИ "по моему мнению", "я считаю"!**

📊 **ДАННЫЕ ИЗ ИНТЕРНЕТА:**
{items_text[:2000]}

Вопрос: {query}

ОТВЕТЬ РАЗВЁРНУТО, НЕ ЛЕНИСЬ, НЕ ВРИ!
"""
        answer = await ask_deepseek(retry_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    
    return answer, all_results, confidence

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ (БЕЗ ИЗМЕНЕНИЙ)
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
- НЕЛЬЗЯ давать советы в областях, где ты не компетентен (медицина, финансы, право).
- ЕСЛИ тебя спросили о факте — скажи: "Я не знаю, но могу поискать в интернете."

⚠️ **РАЗРЕШЕНО:**
- Общаться на общие темы
- Делиться известными фактами (проверенными)
- Задавать уточняющие вопросы
- Предлагать поискать в интернете

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
- НЕЛЬЗЯ давать советы в областях, где ты не компетентен (медицина, финансы, право).
- ЕСЛИ тебя спросили о факте — скажи: "Я не знаю, но могу поискать в интернете."

⚠️ **РАЗРЕШЕНО:**
- Общаться на общие темы
- Делиться известными фактами (проверенными)
- Задавать уточняющие вопросы
- Предлагать поискать в интернете

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
#  ФОРМАТИРОВАНИЕ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

def format_answer_clean(answer: str, confidence: float, sources_count: int) -> str:
    internet_block = ""
    knowledge_block = ""
    conclusion_block = ""
    
    if "📊 **Из интернета**" in answer or "🌐 **Из интернета**" in answer:
        parts = answer.split("🧠 **Дополнено из знаний**" if "🧠 **Дополнено из знаний**" in answer else "✅ **Вывод**")
        if len(parts) > 0:
            internet_block = parts[0].strip()
        if len(parts) > 1:
            knowledge_block = parts[1].strip()
    elif "✅ **Вывод**" in answer:
        parts = answer.split("✅ **Вывод**")
        if len(parts) > 0:
            internet_block = parts[0].strip()
        if len(parts) > 1:
            conclusion_block = parts[1].strip()
    
    if not internet_block and not knowledge_block and not conclusion_block:
        internet_block = answer
    
    sources_label = "источник" if sources_count == 1 else "источника" if sources_count < 5 else "источников"
    
    formatted = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **ИЗ ИНТЕРНЕТА** ({sources_count} {sources_label})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{internet_block if internet_block else '• Данные из интернета не найдены'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧠 **ИЗ ЗНАНИЙ** (дополнено)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{knowledge_block if knowledge_block else '• Дополнений из знаний нет'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ **ВЫВОД**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{conclusion_block if conclusion_block else '• Вывод сформирован на основе данных'}

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
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 ЗАПУСК BROWAIX BOT v2.1 (ИСПРАВЛЕННАЯ ВЕРСИЯ)")
    logger.info("=" * 60)
    
    # Проверка API ключей
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'} (ОСНОВНОЙ)")
    logger.info(f"   Serper: {'✅' if SERPER_API_KEY else '❌'} (РЕЗЕРВ)")
    logger.info(f"   Browserless: {'✅' if BROWSERLESS_WS_ENDPOINT else '❌'}")
    logger.info("=" * 60)
    logger.info("✅ ИСПРАВЛЕН ПАРСИНГ APISERPENT (organic_results)")
    logger.info("✅ ОПТИМИЗИРОВАНА СКОРОСТЬ (SEARCH_RESULTS=15, MAX_PAGES=3)")
    logger.info("✅ УПРОЩЕНА ФИЛЬТРАЦИЯ")
    logger.info("✅ СОХРАНЕНЫ ВСЕ ФУНКЦИИ")
    
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
