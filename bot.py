# ═══════════════════════════════════════════════════════════════════
#  BROWAIX — УНИВЕРСАЛЬНЫЙ ПОИСКОВЫЙ АССИСТЕНТ
#  Версия: 8.0 (ТОЧНОСТЬ + СКОРОСТЬ + ЧЕСТНОСТЬ)
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
from typing import Optional, Dict, List, Tuple, Any, AsyncGenerator
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

PAGE_TIMEOUT = 3
SEARCH_RESULTS = 15
DEEPSEEK_MODEL_FLASH = "deepseek-v4-flash"
DEEPSEEK_MODEL_PRO = "deepseek-v4-pro"
CACHE_TTL = 900
ANSWER_CACHE_TTL = 3600
APISERPENT_TIMEOUT = 25
MAX_TOKENS_OUTPUT = 4000
MAX_TOKENS_VARIANTS = 300
MAX_ITERATIONS = 2
TARGET_CONFIDENCE = 95
EARLY_EXIT_CONFIDENCE = 85
MAX_PAGES_PER_ITERATION = 8
MAX_VARIANTS = 5
BROWSER_TIMEOUT = 5
PAGE_SNIPPET_LENGTH = 3000
MAX_ITEMS_IN_PROMPT = 50

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
#  DEEPSEEK (СТРИМИНГ, КЭШ, ГИБКИЙ ПРОМПТ)
# ═══════════════════════════════════════════════════════════════════

def cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.encode('utf-8')).hexdigest()

def check_answer_quality(answer: str, min_length: int = 300) -> Tuple[bool, str]:
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

async def ask_deepseek_stream(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = MAX_TOKENS_OUTPUT,
    use_pro: bool = True
) -> AsyncGenerator[str, None]:
    key = cache_key(prompt)
    if key in answer_cache and (time.time() - answer_cache[key]['time']) < ANSWER_CACHE_TTL:
        cached = answer_cache[key]['data']
        is_valid, _ = check_answer_quality(cached, min_length=200)
        if is_valid:
            logger.info("♻️ Ответ DeepSeek из кэша")
            yield cached
            return
        else:
            del answer_cache[key]

    model = DEEPSEEK_MODEL_PRO if use_pro else DEEPSEEK_MODEL_FLASH
    logger.info(f"🧠 DeepSeek (stream, {model})")
    logger.debug(f"📝 Промпт (первые 300): {prompt[:300]}...")

    for attempt in range(3):
        try:
            session = await get_session()
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=60
            ) as r:
                if r.status == 200:
                    full_content = ""
                    chunk_counter = 0
                    last_update_time = time.time()
                    async for line in r.content:
                        line = line.decode('utf-8').strip()
                        if not line or line.startswith(':'):
                            continue
                        if line.startswith('data: '):
                            data_str = line[6:]
                            if data_str == '[DONE]':
                                break
                            try:
                                chunk = json.loads(data_str)
                                if 'choices' in chunk and len(chunk['choices']) > 0:
                                    delta = chunk['choices'][0].get('delta', {})
                                    content = delta.get('content', '')
                                    if content:
                                        full_content += content
                                        chunk_counter += 1
                                        if chunk_counter % 20 == 0 or (time.time() - last_update_time) >= 2.0:
                                            yield content
                                            last_update_time = time.time()
                            except json.JSONDecodeError:
                                continue
                    if full_content and chunk_counter % 20 != 0:
                        yield full_content[-200:]
                    if full_content and len(full_content) > 200:
                        is_valid, _ = check_answer_quality(full_content, min_length=200)
                        if is_valid:
                            answer_cache[key] = {'data': full_content, 'time': time.time()}
                    return
                else:
                    logger.warning(f"⚠️ DeepSeek попытка {attempt+1}: HTTP {r.status}")
                    if attempt == 2 and r.status == 429:
                        await asyncio.sleep(5)
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ DeepSeek таймаут попытка {attempt+1}")
            if attempt == 2:
                yield "⚠️ Превышено время ожидания ответа от DeepSeek. Попробуйте позже."
                return
        except Exception as e:
            logger.warning(f"⚠️ DeepSeek ошибка попытка {attempt+1}: {e}")
            if attempt == 2:
                yield f"⚠️ Ошибка при получении ответа: {e}"
                return
        if attempt < 2:
            await asyncio.sleep(1 + attempt * 2)
    
    yield "⚠️ Не удалось получить ответ от DeepSeek."

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
#  ОТПРАВКА СТРИМИНГ-ОТВЕТА (С РАЗРЕЖЕНИЕМ)
# ═══════════════════════════════════════════════════════════════════

async def send_streaming_response(
    update: Update,
    generator: AsyncGenerator[str, None],
    reply_markup=None,
    prefix: str = ""
) -> str:
    full_text = ""
    message = None
    chunk_counter = 0
    last_update_time = time.time()
    try:
        async for chunk in generator:
            if not chunk:
                continue
            full_text += chunk
            chunk_counter += 1
            if chunk_counter % 15 == 0 or (time.time() - last_update_time) >= 3.0:
                try:
                    if message is None:
                        display_text = f"{prefix}{full_text}..."
                        message = await update.effective_message.reply_text(
                            display_text,
                            reply_markup=None
                        )
                    else:
                        display_text = f"{prefix}{full_text}..."
                        if len(display_text) > 4000:
                            display_text = display_text[:4000] + "..."
                        await message.edit_text(display_text)
                    last_update_time = time.time()
                except Exception as e:
                    logger.debug(f"⚠️ Ошибка обновления стриминга: {e}")
                    if "message is too long" in str(e).lower():
                        if message:
                            await message.edit_text(f"{prefix}{full_text[:3500]}... (продолжение)")
                        break
                    if "Too Many Requests" in str(e):
                        logger.warning("⏳ Слишком много запросов, ждём 5 секунд...")
                        await asyncio.sleep(5)
                    continue
        if message and full_text:
            try:
                is_valid, reason = check_answer_quality(full_text, min_length=200)
                if not is_valid and len(full_text) < 300:
                    logger.warning(f"⚠️ Стриминг-ответ отклонён: {reason}")
                    if "короткий" in reason:
                        full_text += "\n\n⚠️ Ответ был слишком кратким, но вот что удалось сгенерировать."
                final_text = f"{prefix}{full_text}"
                if len(final_text) > 4000:
                    final_text = final_text[:4000] + "..."
                await message.edit_text(final_text, reply_markup=reply_markup)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка финализации стриминга: {e}")
                await message.edit_text(f"{prefix}{full_text[:3000]}... (ответ обрезан)", reply_markup=reply_markup)
        elif not full_text:
            fallback = "⚠️ Не удалось получить ответ от DeepSeek. Попробуйте позже."
            if message:
                await message.edit_text(f"{prefix}{fallback}", reply_markup=reply_markup)
            else:
                await update.effective_message.reply_text(f"{prefix}{fallback}", reply_markup=reply_markup)
            return fallback
    except Exception as e:
        logger.error(f"❌ Ошибка стриминг-отправки: {e}")
        if full_text:
            try:
                await update.effective_message.reply_text(
                    f"{prefix}{full_text[:3000]}...",
                    reply_markup=reply_markup
                )
            except:
                pass
        else:
            await update.effective_message.reply_text(
                "⚠️ Ошибка при формировании ответа.",
                reply_markup=reply_markup
            )
    return full_text

# ═══════════════════════════════════════════════════════════════════
#  РАДУЖНАЯ ПОЛОСКА
# ═══════════════════════════════════════════════════════════════════

async def send_progress_updates(chat_id, context, start_time):
    message = None
    try:
        stages = [
            {"emoji": "🧠", "name": "Анализ запроса", "duration": 4},
            {"emoji": "🔍", "name": "Поиск в интернете", "duration": 15},
            {"emoji": "📄", "name": "Загрузка страниц", "duration": 10},
            {"emoji": "🤔", "name": "Формирование ответа", "duration": 12},
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
            if elapsed > 200:
                break
    except Exception as e:
        logger.error(f"❌ Ошибка прогресса: {e}")

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК (APISerpent) — ОСНОВНОЙ И ЕДИНСТВЕННЫЙ
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
        logger.debug(f"📤 Параметры APISerpent (Google): {params}")
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=APISERPENT_TIMEOUT
        ) as r:
            logger.info(f"📡 APISerpent статус: {r.status}")
            if r.status == 200:
                data = await r.json()
                logger.debug(f"📄 APISerpent ответ: {json.dumps(data, ensure_ascii=False)[:1000]}...")
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
                                logger.info(f"✅ Найден блок '{block}'")
                                return results
                logger.warning("⚠️ Результатов не найдено")
                return []
            else:
                logger.error(f"❌ APISerpent HTTP {r.status}")
                return []
    except asyncio.TimeoutError:
        logger.error(f"⏰ Таймаут APISerpent (Google, {APISERPENT_TIMEOUT} сек)")
        logger.info("🔄 Повторная попытка с engine='bing' и без deep...")
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
                            logger.info(f"✅ APISerpent (Bing) нашёл {len(organic)} результатов")
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
        logger.error(traceback.format_exc())
        return []

async def search_with_cache(query: str) -> List[Dict]:
    norm = normalize_query(query)
    if norm in search_cache and (time.time() - search_cache[norm]['time']) < CACHE_TTL:
        logger.info(f"♻️ Из кэша: {query[:30]}...")
        return search_cache[norm]['data']
    logger.info(f"🔍 Поиск через APISerpent: {query[:50]}...")
    results = await search_apiserpent(query)
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
#  ПАРСИНГ (УНИВЕРСАЛЬНЫЙ)
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
        prompt = f"Сгенерируй {MAX_VARIANTS} разных вариантов поискового запроса для:\n{query}\nОтветь списком, каждый с новой строки. Варианты должны быть разнообразными: синонимы, перефразировки, уточнения."
        generator = ask_deepseek_stream(prompt, temperature=0.3, max_tokens=MAX_TOKENS_VARIANTS, use_pro=False)
        full = ""
        async for chunk in generator:
            full += chunk
        if full:
            for line in full.strip().split('\n'):
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
            parsed = p.get('parsed', {})
            structure_count += len(parsed.get('lists', [])) + len(parsed.get('headings', []))
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
   • Согласованность: {confidence.get('consensus', 0):.0f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА (УНИВЕРСАЛЬНЫЙ ПРОМПТ)
# ═══════════════════════════════════════════════════════════════════

async def search_and_answer_stream(
    query: str,
    uid: int,
    context_prompt: str = "",
    update: Update = None
) -> Tuple[str, List[Dict], float]:
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
        
        for page in pages:
            full_text = page.get('full_text', '')
            if full_text:
                all_items.append({
                    'title': '📄 Полный текст страницы',
                    'snippet': full_text[:PAGE_SNIPPET_LENGTH],
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
        
        confidence_data = calculate_confidence(pages, len(all_items), all_items)
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
⚠️ **СИСТЕМА ПОИСКА НЕ ВЕРНУЛА РЕЗУЛЬТАТОВ — ЭТО ФАКТ**

Это проверенный факт. Я честно сообщаю, что в интернете нет информации по вашему запросу.

Вопрос: {query}
Контекст: {context_prompt}

ОТВЕТЬ КРАТКО, ЧЕСТНО, БЕЗ ВЫДУМОК.
"""
        generator = ask_deepseek_stream(fallback_prompt, temperature=0.3, use_pro=False)
        full_answer = await send_streaming_response(update, generator, reply_markup=ACTION_BUTTONS)
        return full_answer, [], 0.0
    
    # Формируем универсальный промпт
    text_parts = []
    full_texts = [item for item in all_items if item.get('source') == 'page_full_text']
    other_items = [item for item in all_items if item.get('source') != 'page_full_text']
    
    for idx, item in enumerate(full_texts[:MAX_PAGES_PER_ITERATION], 1):
        snippet = item.get('snippet', '')
        if snippet:
            text_parts.append(f"=== СТРАНИЦА {idx} (ПОЛНЫЙ ТЕКСТ) ===\n{snippet}\n")
    
    for item in other_items[:MAX_ITEMS_IN_PROMPT]:
        title = item.get('title', '')
        snippet = item.get('snippet', '')
        source = item.get('source', '')
        if snippet and snippet != title:
            text_parts.append(f"[{source}] {title}: {snippet[:300]}")
        elif title:
            text_parts.append(f"[{source}] {title}")
    
    items_text = "\n".join(text_parts)
    
    # ⭐ УНИВЕРСАЛЬНЫЙ ПРОМПТ (БЕЗ ХАРДКОДА)
    answer_prompt = f"""
⚠️ **ТЫ ПОЛУЧИЛ РЕАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА!**

📊 **ВСЕ ДАННЫЕ С ВЕБ-СТРАНИЦ:**
{items_text}

🧠 **КОНТЕКСТ ДИАЛОГА:**
{context_prompt if context_prompt else "Нет контекста"}

⚠️ **ТВОЯ ЗАДАЧА — ОТВЕТИТЬ НА ВОПРОС, ИСПОЛЬЗУЯ ТОЛЬКО ЭТИ ДАННЫЕ.**

**ПРАВИЛА:**

1. **Проанализируй запрос** — что именно хочет узнать пользователь?
2. **Извлеки из данных только то, что относится к вопросу.**
3. **Если данных много — выбери самое важное.** Если мало — скажи честно, что удалось найти.
4. **Игнорируй шум:** рекламу, навигационные элементы, технические метаданные.
5. **Структурируй ответ** в зависимости от запроса:
   - Если просят прогноз → выдели даты, цифры, тенденции.
   - Если просят сравнение → сделай таблицу или списки.
   - Если просят инструкцию → опиши шаги.
   - Если просят факт → дай чёткий ответ.
6. **Используй маркеры:** **, ✅, 📊, 📋, 🌐 — но только там, где это уместно.
7. **НЕ ВЫДУМЫВАЙ!** Если данных нет — скажи: "В найденных данных нет информации по вашему запросу."
8. **НЕЛЬЗЯ** говорить "по моему мнению", "я считаю", "возможно".
9. **Выделяй важные числа и факты жирным шрифтом.**
10. **Указывай источник**, если это возможно.

**Вопрос:** {query}

**ОТВЕТЬ РАЗВЁРНУТО, НО ТОЛЬКО ПО ДАННЫМ. МИНИМУМ 500 СИМВОЛОВ.**
"""
    
    t_answer_start = time.time()
    generator = ask_deepseek_stream(
        answer_prompt,
        temperature=0.2,
        max_tokens=MAX_TOKENS_OUTPUT,
        use_pro=True
    )
    full_answer = await send_streaming_response(
        update,
        generator,
        reply_markup=ACTION_WITH_SOURCES_BUTTONS,
        prefix=""
    )
    time_answer = time.time() - t_answer_start
    logger.info(f"⏱️ Формирование ответа (стриминг, Pro): {time_answer:.2f} сек")
    
    is_valid, reason = check_answer_quality(full_answer, min_length=300)
    if not is_valid and len(full_answer) < 300:
        logger.warning(f"⚠️ Ответ отклонён: {reason}")
        retry_prompt = f"""
⚠️ ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ СЛИШКОМ КОРОТКИМ. Причина: {reason}

📊 ДАННЫЕ:
{items_text[:3000]}

Вопрос: {query}

ОТВЕТЬ РАЗВЁРНУТО, НЕ ВРИ, НЕ ВЫДУМЫВАЙ. ИСПОЛЬЗУЙ МАРКЕРЫ. МИНИМУМ 400 СИМВОЛОВ.
"""
        generator2 = ask_deepseek_stream(retry_prompt, temperature=0.2, max_tokens=MAX_TOKENS_OUTPUT, use_pro=True)
        full_answer = await send_streaming_response(
            update,
            generator2,
            reply_markup=ACTION_WITH_SOURCES_BUTTONS,
            prefix="⚠️ Ответ был слишком кратким, вот дополненная версия:\n\n"
        )
    
    total_time = time.time() - time_start
    logger.info(f"⏱️ ОБЩЕЕ ВРЕМЯ: {total_time:.2f} сек")
    logger.info(f"⏱️ Детали: варианты={time_variants:.2f}, поиск={time_search:.2f}, загрузка={time_fetch:.2f}, ответ={time_answer:.2f}")
    
    return full_answer, all_results, confidence

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИКИ (АДАПТИРОВАНЫ ПОД СТРИМИНГ)
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
        await query.edit_message_text("🔍 Начинаю поиск и подготовку данных...")
        start_time = time.time()
        context.user_data['found_answer'] = False
        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )
        context_text = memory.get_full_context()
        answer, sources, confidence = await search_and_answer_stream(
            pending_text, user_id, context_text, update
        )
        context.user_data['found_answer'] = True
        await progress_task
        elapsed = int(time.time() - start_time)
        memory.add_message('user', pending_text)
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = pending_text
        context.user_data['last_answer'] = answer
        context.user_data['pending_text'] = ''
        context.user_data['last_sources'] = sources[:10]
        context.user_data['last_formatted_answer'] = answer
        await update.effective_message.reply_text(
            f"⏱️ Полное время ответа: {elapsed} сек",
            reply_markup=None
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
        generator = ask_deepseek_stream(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        answer = await send_streaming_response(
            update,
            generator,
            reply_markup=EXIT_CHAT_BUTTON,
            prefix="💬 "
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
        generator = ask_deepseek_stream(chat_prompt, temperature=0.8, max_tokens=MAX_TOKENS_OUTPUT, use_pro=False)
        answer = await send_streaming_response(
            update,
            generator,
            reply_markup=EXIT_CHAT_BUTTON,
            prefix="💬 "
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
        start_time = time.time()
        context.user_data['found_answer'] = False
        progress_task = asyncio.create_task(
            send_progress_updates(update.effective_chat.id, context, start_time)
        )
        full_context = memory.get_full_context()
        answer, sources, confidence = await search_and_answer_stream(
            combined_query, user_id, full_context, update
        )
        context.user_data['found_answer'] = True
        await progress_task
        elapsed = int(time.time() - start_time)
        memory.add_message('user', f"Уточнение: {clarification}")
        memory.add_message('assistant', answer)
        context.user_data['last_query'] = combined_query
        context.user_data['last_answer'] = answer
        context.user_data['last_sources'] = sources[:10]
        context.user_data['last_formatted_answer'] = answer
        await update.effective_message.reply_text(
            f"⏱️ Полное время ответа: {elapsed} сек",
            reply_markup=None
        )
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
    logger.info("🚀 ЗАПУСК BROWAIX v8.0 (ТОЧНОСТЬ + СКОРОСТЬ + ЧЕСТНОСТЬ)")
    logger.info("=" * 60)
    logger.info("🔑 Проверка API ключей:")
    logger.info(f"   Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"   DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"   APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"   Playwright REST: {'✅' if BROWSER_WS_ENDPOINT else '❌'}")
    logger.info("=" * 60)
    logger.info("⚡ ПАРАМЕТРЫ:")
    logger.info(f"   • Модели: Flash для вариантов, Pro для ответа")
    logger.info(f"   • Итераций: {MAX_ITERATIONS}")
    logger.info(f"   • Страниц за итерацию: {MAX_PAGES_PER_ITERATION}")
    logger.info(f"   • Макс. токенов ответа: {MAX_TOKENS_OUTPUT}")
    logger.info(f"   • Вариантов запросов: {MAX_VARIANTS}")
    logger.info(f"   • Стриминг: ВКЛЮЧЁН (с защитой от спама)")
    logger.info(f"   • Промпт: универсальный (без хардкода)")
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
