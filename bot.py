# ═══════════════════════════════════════════════════════════════════
#  ИНСТРУКЦИЯ ДЛЯ РАЗРАБОТЧИКА (ЧТО ЭТОТ БОТ УМЕЕТ)
#  ЭТОТ СПИСОК — ГЛАВНЫЙ ДОКУМЕНТ. НЕ УДАЛЯТЬ!
# ═══════════════════════════════════════════════════════════════════

"""
🤖 БОТ: BROWAIX — УНИВЕРСАЛЬНЫЙ ПОИСКОВЫЙ АССИСТЕНТ

📌 ОСНОВНЫЕ ВОЗМОЖНОСТИ:
────────────────────────────────────────────────────────────────────
1. 🔍 ПОИСК В ИНТЕРНЕТЕ
   - APISerpent (ОСНОВНОЙ И ЕДИНСТВЕННЫЙ) с правильным парсингом
   - Глубокий поиск (deep=true) с локализацией (ru)
   - Параллельный поиск по вариантам запросов
   - Итеративный поиск (до 2 итераций)
   - Ранний выход при уверенности ≥ 85%

2. 🧠 ПАМЯТЬ (5 УРОВНЕЙ)
   - Краткосрочная, профиль, эпизодическая, обучающая, граф знаний

3. 🎯 РЕЖИМЫ РАБОТЫ
   - 🔍 Поиск — полноценный поиск в интернете
   - 📝 Уточнить — уточнение предыдущего запроса
   - 💬 Беседа — общение без интернета (из знаний и памяти)

4. 🎨 ВИЗУАЛЬНЫЕ УЛУЧШЕНИЯ
   - Радужная анимированная полоска прогресса
   - Детальные статусы этапов работы
   - Счётчик времени
   - Кнопка "Показать источники"

5. 🛡️ ЗАЩИТА ОТ ОБМАНА (УСИЛЕННАЯ)
   - Запрет фраз: "нет доступа", "не могу найти", "нет интернета", "я не могу", "нет информации", "не знаю", "не удалось", "по моему мнению", "я считаю", "я думаю", "возможно", "вероятно", "скорее всего", "примерно", "около", "приблизительно", "как мне кажется", "наверное"
   - Запрет смешивать знания с интернетом
   - Запрет выдумывать (усиленный)
   - Проверка структуры ответа (наличие маркеров)
   - Проверка уникальности слов
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
#  КОНФИГ (ОПТИМИЗИРОВАННЫЙ ПОД КАЧЕСТВО И БЮДЖЕТ НА 4 МЕСЯЦА)
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
# ⚡ SERPER_API_KEY удалён — больше не используется
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

PAGE_TIMEOUT = 3
SEARCH_RESULTS = 15                      # ⚡ Увеличено до 15 для полноты
DEEPSEEK_MODEL_FLASH = "deepseek-v4-flash"
DEEPSEEK_MODEL_PRO = "deepseek-v4-pro"
CACHE_TTL = 900
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 20                  # ⚡ Увеличен до 20 сек (глубокий поиск требует времени)
MAX_TOKENS_OUTPUT = 4000                 # ⚡ Качество
MAX_TOKENS_VARIANTS = 300                # ⚡ Качество
MAX_ITERATIONS = 2
TARGET_CONFIDENCE = 95
EARLY_EXIT_CONFIDENCE = 85
MAX_PAGES_PER_ITERATION = 3              # ⚡ Полнота
MAX_VARIANTS = 3                         # ⚡ Качество
BROWSER_TIMEOUT = 5

# REST-эндпоинт Playwright (если есть)
BROWSER_WS_ENDPOINT = os.getenv("BROWSER_WS_ENDPOINT", "")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

def now():
    return datetime.now(TZ)

# ═══════════════════════════════════════════════════════════════════
#  КНОПКИ (БЕЗ ИЗМЕНЕНИЙ)
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
#  DEEPSEEK (С КЭШИРОВАНИЕМ И УСИЛЕННОЙ ПРОВЕРКОЙ)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_answer_quality(answer: str, min_length: int = 200) -> Tuple[bool, str]:
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
    markers = ["**", "📊", "✅", "🧠", "🌐", "📋"]
    if not any(marker in answer for marker in markers):
        return False, "Ответ не структурирован (нет маркеров)"
    words = answer.split()
    if len(words) > 30:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.35:
            return False, "Ответ содержит слишком много повторов"
    return True, "OK"

async def ask_deepseek(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = MAX_TOKENS_OUTPUT,
    use_pro: bool = True
) -> str:
    key = cache_key(prompt)
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        is_valid, _ = check_answer_quality(cached, min_length=150)
        if is_valid:
            logger.info("♻️ Ответ DeepSeek из кэша")
            return cached
        else:
            del answer_cache[key]

    model = DEEPSEEK_MODEL_PRO if use_pro else DEEPSEEK_MODEL_FLASH
    logger.info(f"🧠 DeepSeek: {model} {'(Pro)' if use_pro else '(Flash)'}")
    logger.debug(f"📝 Промпт (первые 300): {prompt[:300]}...")

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
                timeout=60
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    content = data["choices"][0]["message"]["content"]
                    logger.debug(f"📥 Ответ (первые 300): {content[:300]}...")
                    if content and len(content) > 50:
                        is_valid, reason = check_answer_quality(content)
                        if is_valid:
                            answer_cache[key] = {'data': content, 'time': time.time()}
                            return content
                        else:
                            logger.warning(f"⚠️ Ответ отклонён: {reason}")
                            if attempt == 2:
                                return f"⚠️ {content}"
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
    return ""

# ═══════════════════════════════════════════════════════════════════
#  ПАМЯТЬ (5 УРОВНЕЙ) — БЕЗ ИЗМЕНЕНИЙ
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
                text[:3000] + "\n\n... (ответ обрезан)",
                reply_markup=reply_markup
            )
        except:
            pass

# ═══════════════════════════════════════════════════════════════════
#  РАДУЖНАЯ ПОЛОСКА (ОПТИМИЗИРОВАННАЯ)
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        stages = [
            {"emoji": "🧠", "name": "Анализ запроса", "duration": 4},
            {"emoji": "🔍", "name": "Поиск в интернете", "duration": 8},
            {"emoji": "📄", "name": "Загрузка страниц", "duration": 10},
            {"emoji": "🤔", "name": "Формирование ответа", "duration": 8},
        ]
        rainbow_colors = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣"]
        message = await context.bot.send_message(
            chat_id,
            "🧠 **Анализ запроса**\n`░░░░░░░░░░░░░░░░░░░░ 0%`\n⏱️ 0 сек"
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
                        f"✅ **Готово!** Формирую ответ...\n⏱️ {int(time.time() - start_time)} сек"
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
                    text = f"{stage['emoji']} **{stage['name']}**\n`{bar}` 🟣\n⏱️ {elapsed} сек"
                try:
                    await message.edit_text(text, parse_mode='Markdown')
                except Exception:
                    pass
            if elapsed > 180:
                break
    except Exception as e:
        logger.error(f"❌ Ошибка прогресса: {e}")

# ═══════════════════════════════════════════════════════════════════
#  ⭐ ПОИСК — ТОЛЬКО APISERPENT (SERPER УДАЛЁН)
# ═══════════════════════════════════════════════════════════════════

def normalize_query(query):
    return re.sub(r'[^\w\s]', '', query.lower()).strip()

async def search_apiserpent(query: str) -> List[Dict]:
    """
    Поиск через APISerpent (ОСНОВНОЙ И ЕДИНСТВЕННЫЙ).
    Использует глубокий поиск с локализацией.
    """
    if not APISERPENT_API_KEY:
        logger.error("❌ APISERPENT_API_KEY не задан!")
        return []
    
    try:
        session = await get_session()
        logger.info(f"🔍 APISerpent: {query[:50]}...")
        
        # ⚡ Параметры для глубокого поиска с локализацией
        params = {
            "q": query,
            "engine": "google",              # можно заменить на "bing" или "ddg" для скорости
            "num": SEARCH_RESULTS,           # 15 результатов
            "deep": "true",                  # глубокий поиск (обход защиты)
            "country": "ru",                 # локализация для России
            "language": "ru",                # язык результатов
        }
        
        logger.debug(f"📤 Параметры APISerpent: {params}")
        
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY, "Accept": "application/json"},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            logger.info(f"📡 APISerpent статус: {r.status}")
            response_text = await r.text()
            logger.debug(f"📄 APISerpent RAW JSON: {response_text[:2000]}...")
            
            if r.status == 200:
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    logger.error("❌ Ошибка парсинга JSON")
                    return []
                
                results = []
                
                # ⭐ ПРАВИЛЬНЫЙ ПУТЬ: data → results → organic
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
                
                # Если organic не найден, пробуем другие блоки
                for block in ["ai_overview", "featured_snippet", "people_also_ask", "knowledge_graph"]:
                    if block in data.get("results", {}):
                        block_data = data["results"][block]
                        if isinstance(block_data, dict):
                            snippet = block_data.get("snippet") or block_data.get("answer") or block_data.get("description") or ""
                            if snippet:
                                results.append({
                                    "title": block_data.get("title", block),
                                    "snippet": snippet,
                                    "link": block_data.get("url", ""),
                                    "source": block
                                })
                                logger.info(f"✅ Найден блок '{block}'")
                                return results
                
                logger.warning("⚠️ Результатов не найдено")
                return []
            else:
                logger.error(f"❌ APISerpent HTTP {r.status}: {response_text[:200]}")
                return []
                
    except asyncio.TimeoutError:
        logger.error(f"⏰ Таймаут APISerpent ({APISERPENT_TIMEOUT} сек)")
        # При таймауте пробуем ещё раз с упрощёнными параметрами
        try:
            logger.info("🔄 Повторная попытка с engine='bing' и без deep...")
            params = {"q": query, "engine": "bing", "num": 10}
            async with session.get(
                "https://apiserpent.com/api/search",
                params=params,
                headers={"X-API-Key": APISERPENT_API_KEY},
                timeout=15
            ) as r2:
                if r2.status == 200:
                    data = await r2.json()
                    if "results" in data and isinstance(data["results"], dict):
                        organic = data["results"].get("organic", [])
                        if organic:
                            results = []
                            for item in organic:
                                if isinstance(item, dict):
                                    results.append({
                                        "title": item.get("title", "") or item.get("name", ""),
                                        "snippet": item.get("snippet", "") or item.get("description", "") or item.get("text", ""),
                                        "link": item.get("url", "") or item.get("link", ""),
                                        "source": "organic"
                                    })
                            logger.info(f"✅ APISerpent (Bing) нашёл {len(results)} результатов")
                            return results
        except Exception as e2:
            logger.warning(f"⚠️ Повторная попытка не удалась: {e2}")
    except Exception as e:
        logger.error(f"💥 Ошибка APISerpent: {e}")
        logger.error(traceback.format_exc())
    
    return []

# ═══════════════════════════════════════════════════════════════════
#  ⭐ SEARCH_WITH_CACHE — ТОЛЬКО APISERPENT
# ═══════════════════════════════════════════════════════════════════

async def search_with_cache(query: str) -> List[Dict]:
    """Поиск с кэшем - ТОЛЬКО APISerpent (Serper удалён)"""
    norm = normalize_query(query)
    
    if norm in search_cache and (time.time() - search_cache[norm]['time']) < CACHE_TTL:
        logger.info(f"♻️ Из кэша: {query[:30]}...")
        return search_cache[norm]['data']
    
    logger.info(f"🔍 Поиск через APISerpent: {query[:50]}...")
    results = await search_apiserpent(query)
    
    # ⭐ Serper НЕ ИСПОЛЬЗУЕТСЯ — полностью удалён
    
    search_cache[norm] = {'data': results, 'time': time.time()}
    logger.info(f"📊 ИТОГО результатов: {len(results)}")
    return results

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРАЦИЯ (ТОЛЬКО СПАМ)
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

async def search_parallel(variants: List[str], query: str) -> List[Dict]:
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
    return all_results

# ═══════════════════════════════════════════════════════════════════
#  ЗАГРУЗКА СТРАНИЦ (REST + HTTP)
# ═══════════════════════════════════════════════════════════════════

async def fetch_page_rest(url: str) -> Optional[str]:
    if not BROWSER_WS_ENDPOINT:
        return None
    try:
        base_url = BROWSER_WS_ENDPOINT.rstrip('/')
        endpoints = [
            f"{base_url}/api/scrape",
            f"{base_url}/scrape",
            f"{base_url}/v1/scrape",
        ]
        session = await get_session()
        for endpoint in endpoints:
            try:
                async with session.post(
                    endpoint,
                    json={"url": url},
                    timeout=15
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        html = data.get("html") or data.get("content") or data.get("data")
                        if html:
                            logger.debug(f"✅ REST загрузил страницу (длина {len(html)})")
                            return html
                    elif r.status == 404:
                        continue
                    else:
                        break
            except:
                continue
        return None
    except Exception as e:
        logger.debug(f"⚠️ REST ошибка: {e}")
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

def empty_page_result():
    return {'text': '', 'lists': [], 'headings': [], 'items': [], 'date': None,
            'definitions': [], 'key_facts': [], 'metrics': [], 'tables': [],
            'full_text': '', 'json_data': []}

async def fetch_page(url: str, query: str) -> Dict:
    if not url:
        return empty_page_result()
    html = None
    if BROWSER_WS_ENDPOINT:
        html = await fetch_page_rest(url)
    if not html:
        logger.debug(f"🌐 HTTP: {url[:100]}...")
        html = await fetch_http(url)
    if html:
        return parse_page(html, query)
    return empty_page_result()

# ═══════════════════════════════════════════════════════════════════
#  ⭐ ПАРСИНГ (С ИЗВЛЕЧЕНИЕМ JSON-LD)
# ═══════════════════════════════════════════════════════════════════

def parse_page(html: str, query: str) -> Dict:
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
        # Извлечение JSON-LD
        for script in soup.find_all('script', type=['application/ld+json', 'application/json']):
            try:
                if script.string:
                    data = json.loads(script.string)
                    if isinstance(data, (dict, list)):
                        result['json_data'].append(json.dumps(data, ensure_ascii=False)[:1000])
            except:
                pass
        # Извлечение метрик
        metric_patterns = [
            r'([-+]?\d{1,4}\s*[°C℃]?)',
            r'([-+]?\d{1,4}\s*м/с|км/ч|mph)',
            r'(\d{3,4}\s*мм рт\. ст\.|гПа|мбар|hPa)',
            r'(\d{1,3}\s*мм|дюйм|in|%)',
            r'(\d{1,3}\s*м|км|миль|ft)',
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

async def fetch_pages(links: List[str], query: str) -> List[Dict]:
    if not links:
        return []
    tasks = [fetch_page(link, query) for link in links[:MAX_PAGES_PER_ITERATION]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r.get('full_text') and len(r.get('full_text')) > 200]

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ВАРИАНТОВ (FLASH)
# ═══════════════════════════════════════════════════════════════════

async def generate_variants(query: str) -> List[str]:
    variants = [query]
    if MAX_VARIANTS <= 1:
        return variants
    try:
        prompt = f"Сгенерируй {MAX_VARIANTS} вариантов поискового запроса для:\n{query}\nОтветь списком, каждый с новой строки."
        result = await ask_deepseek(prompt, temperature=0.2, max_tokens=MAX_TOKENS_VARIANTS, use_pro=False)
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
#  РАСЧЁТ УВЕРЕННОСТИ (УЛУЧШЕННЫЙ)
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
    
    # Полнота данных (учитываем количество элементов)
    structure_count = 0
    text_length = 0
    for p in pages:
        structure_count += len(p.get('lists', [])) + len(p.get('headings', [])) + len(p.get('tables', []))
        text_length += len(p.get('full_text', ''))
    confidence['data_completeness'] = min(100, structure_count * 10 + min(20, text_length // 1000))
    
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
#  ⭐ ОСНОВНАЯ ЛОГИКА (СБОР ВСЕХ ДАННЫХ + LLM АНАЛИЗ)
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer(query: str, uid: int, context_prompt: str = "") -> Tuple[str, List[Dict], float]:
    logger.info(f"🛡️ ЗАПРОС: {query[:50]}")
    time_start = time.time()
    time_variants = 0
    time_search = 0
    time_fetch = 0
    time_answer = 0
    
    all_items = []
    all_results = []
    confidence = 0.0
    iteration = 0
    
    variants = await generate_variants(query)
    time_variants = time.time() - time_start
    logger.info(f"⏱️ Генерация вариантов: {time_variants:.2f} сек")
    search_variants = variants[:MAX_VARIANTS]
    
    while confidence < TARGET_CONFIDENCE and iteration < MAX_ITERATIONS:
        iteration += 1
        logger.info(f"🔍 Итерация {iteration}")
        t_search_start = time.time()
        results = await search_parallel(search_variants, query)
        t_search = time.time() - t_search_start
        time_search += t_search
        logger.info(f"⏱️ Поиск итерации {iteration}: {t_search:.2f} сек")
        if not results:
            logger.info(f"⚠️ Нет результатов в итерации {iteration}")
            break
        all_results.extend(results)
        links = [r.get('link', '') for r in results if r.get('link')]
        t_fetch_start = time.time()
        pages = await fetch_pages(links, query)
        t_fetch = time.time() - t_fetch_start
        time_fetch += t_fetch
        logger.info(f"⏱️ Загрузка страниц итерации {iteration}: {t_fetch:.2f} сек")
        
        # Сбор всех данных
        for page in pages:
            full_text = page.get('full_text', '')
            if full_text:
                all_items.append({
                    'title': '📄 Полный текст страницы',
                    'snippet': full_text[:4000],
                    'source': 'page_full_text'
                })
            for jdata in page.get('json_data', []):
                if jdata:
                    all_items.append({
                        'title': '📊 Структурированные данные (JSON-LD)',
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
        
        confidence_data = calculate_confidence(pages)
        confidence = confidence_data.get('overall', 0)
        logger.info(f"📊 Уверенность: {confidence:.1f}% (всего элементов: {len(all_items)})")
        
        if confidence >= EARLY_EXIT_CONFIDENCE:
            logger.info(f"✅ Ранний выход: уверенность {confidence:.1f}%")
            break
        
        if confidence < TARGET_CONFIDENCE and iteration < MAX_ITERATIONS - 1:
            new_variants = await generate_refined_variants(query, all_items)
            search_variants = new_variants[:MAX_VARIANTS]
    
    logger.info(f"📊 ИТОГО собрано элементов: {len(all_items)}")
    
    if not all_items:
        logger.warning("⚠️ Нет данных")
        fallback_prompt = f"""
⚠️ **ПО ВАШЕМУ ЗАПРОСУ НИЧЕГО НЕ НАЙДЕНО**

Это проверенный факт. Я честно сообщаю, что в интернете нет информации по вашему запросу.

Вопрос: {query}
Контекст: {context_prompt}

ОТВЕТЬ КРАТКО, ЧЕСТНО, БЕЗ ВЫДУМОК.
"""
        answer = await ask_deepseek(fallback_prompt, temperature=0.3, use_pro=False)
        return answer, [], 0.0
    
    # Формируем дамп
    text_parts = []
    full_texts = [item for item in all_items if item.get('source') == 'page_full_text']
    other_items = [item for item in all_items if item.get('source') != 'page_full_text']
    
    for idx, item in enumerate(full_texts[:3], 1):
        snippet = item.get('snippet', '')
        if snippet:
            text_parts.append(f"=== СТРАНИЦА {idx} (ПОЛНЫЙ ТЕКСТ) ===\n{snippet}\n")
    
    for item in other_items[:40]:
        title = item.get('title', '')
        snippet = item.get('snippet', '')
        source = item.get('source', '')
        if snippet and snippet != title:
            text_parts.append(f"[{source}] {title}: {snippet[:300]}")
        elif title:
            text_parts.append(f"[{source}] {title}")
    
    items_text = "\n".join(text_parts)
    
    answer_prompt = f"""
⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА!**

📊 **ВСЕ ДАННЫЕ С ВЕБ-СТРАНИЦ (текст, структурированные данные, таблицы, метрики):**

{items_text}

🧠 **КОНТЕКСТ ДИАЛОГА (из памяти):**
{context_prompt if context_prompt else "Нет контекста"}

⚠️ **ТВОЯ ЗАДАЧА — ПРОАНАЛИЗИРОВАТЬ ЭТИ ДАННЫЕ И ДАТЬ ОТВЕТ НА ВОПРОС.**

**ЖЁСТКИЕ ПРАВИЛА (НАРУШЕНИЕ = ОБМАН):**

1. **НЕ ВЫДУМЫВАЙ!** Используй ТОЛЬКО то, что есть в данных.
2. **ЕСЛИ ДАННЫХ МАЛО** — честно скажи: "В найденных данных мало информации, но вот что удалось извлечь..."
3. **НЕЛЬЗЯ** говорить "по моему мнению", "я считаю", "я думаю".
4. **УКАЗЫВАЙ ИСТОЧНИКИ** — откуда взята информация.
5. **СТРУКТУРИРУЙ ОТВЕТ** — выдели основные факты, детали, цифры.
6. **ЕСЛИ ЦИФРЫ БЕЗ КОНТЕКСТА** — честно скажи об этом.
7. **ИСПОЛЬЗУЙ МАРКЕРЫ**: **, 📊, ✅, 🧠, 🌐, 📋 для структуры.

Вопрос: {query}

ОТВЕТЬ РАЗВЁРНУТО, ИНФОРМАТИВНО, НО БЕЗ ВЫДУМОК!
"""
    
    t_answer_start = time.time()
    answer = await ask_deepseek(answer_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    time_answer = time.time() - t_answer_start
    logger.info(f"⏱️ Формирование ответа: {time_answer:.2f} сек")
    
    is_valid, reason = check_answer_quality(answer)
    if not is_valid:
        logger.warning(f"⚠️ Ответ отклонён: {reason}")
        retry_prompt = f"""
⚠️ ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОТКЛОНЁН. Причина: {reason}

📊 ДАННЫЕ:
{items_text[:3000]}

Вопрос: {query}

ОТВЕТЬ РАЗВЁРНУТО, НЕ ВРИ, НЕ ВЫДУМЫВАЙ. ИСПОЛЬЗУЙ МАРКЕРЫ.
"""
        answer = await ask_deepseek(retry_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
    
    total_time = time.time() - time_start
    logger.info(f"⏱️ ОБЩЕЕ ВРЕМЯ: {total_time:.2f} сек")
    logger.info(f"⏱️ Детали: варианты={time_variants:.2f}, поиск={time_search:.2f}, загрузка={time_fetch:.2f}, ответ={time_answer:.2f}")
    
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
            await query.edit_message_text("⚠️ Сначала напишите вопрос в чат.", reply_markup=ACTION_BUTTONS)
            return
        context.user_data['awaiting_input'] = False
        await query.edit_message_text("🔍 Начинаю поиск...")
        start_time = time.time()
        context.user_data['found_answer'] = False
        progress_task = asyncio.create_task(send_progress_updates(update.effective_chat.id, context, start_time))
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
💬 **Ты — дружелюбный собеседник, но НЕ ИСТОЧНИК ФАКТОВ.**

⚠️ **ТЫ НЕ ИМЕЕШЬ ПРАВА ВЫДУМЫВАТЬ!**
- Если не знаешь — скажи "Я не знаю".
- НЕЛЬЗЯ выдумывать факты, цифры, даты.
- НЕЛЬЗЯ говорить "по моему мнению", "я считаю".
- ЕСЛИ спросили о факте — скажи: "Я не знаю, но могу поискать."

Контекст: {full_context}
Сообщение: {pending_text}

ОТВЕТЬ ЕСТЕСТВЕННО, НО ЧЕСТНО!
"""
        answer = await ask_deepseek(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        if not answer:
            answer = "😊 Я здесь! Чем могу помочь?"
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        await query.edit_message_text(f"💬 **Режим беседы**\n\n{answer}", reply_markup=EXIT_CHAT_BUTTON)

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
        sources_formatted = format_sources(sources)
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
💬 **Ты — дружелюбный собеседник, но НЕ ИСТОЧНИК ФАКТОВ.**

⚠️ **ТЫ НЕ ИМЕЕШЬ ПРАВА ВЫДУМЫВАТЬ!**
- Если не знаешь — скажи "Я не знаю".
- НЕЛЬЗЯ выдумывать факты, цифры, даты.
- НЕЛЬЗЯ говорить "по моему мнению", "я считаю".
- ЕСЛИ спросили о факте — скажи: "Я не знаю, но могу поискать."

Контекст: {full_context}
Сообщение: {user_message}

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
        start_time = time.time()
        context.user_data['found_answer'] = False
        progress_task = asyncio.create_task(send_progress_updates(update.effective_chat.id, context, start_time))
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
        "🧹 **Всё забыто!**\n\nПамять очищена. Начинаем с чистого листа.",
        reply_markup=ACTION_BUTTONS
    )


# ═══════════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ
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
    logger.info("🚀 ЗАПУСК BROWAIX v5.0 (APISERPENT ТОЛЬКО, БЕЗ SERPER)")
    logger.info("=" * 60)
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'} (ОСНОВНОЙ И ЕДИНСТВЕННЫЙ)")
    logger.info(f"   Serper: ❌ (ОТКЛЮЧЁН — больше не используется)")
    logger.info(f"   Playwright REST: {'✅' if BROWSER_WS_ENDPOINT else '❌'}")
    logger.info("=" * 60)
    logger.info("⚡ ПАРАМЕТРЫ:")
    logger.info(f"   • Модели: Flash для вариантов, Pro для ответа")
    logger.info(f"   • Итераций: {MAX_ITERATIONS}")
    logger.info(f"   • Страниц за итерацию: {MAX_PAGES_PER_ITERATION}")
    logger.info(f"   • Макс. токенов ответа: {MAX_TOKENS_OUTPUT}")
    logger.info(f"   • Вариантов запросов: {MAX_VARIANTS}")
    logger.info(f"   • Парсинг JSON-LD: включён")
    logger.info(f"   • Кэширование промптов: включено")
    logger.info(f"   • APISerpent таймаут: {APISERPENT_TIMEOUT} сек (глубокий поиск)")
    logger.info("=" * 60)
    
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не задан!")
        return
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DEEPSEEK_API_KEY не задан!")
        return
    if not APISERPENT_API_KEY:
        logger.error("❌ APISERPENT_API_KEY не задан! Поиск не будет работать!")
        return
    
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
