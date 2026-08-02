# ===================================================================
#  BroWaix Bot — ФИНАЛЬНАЯ ВЕРСИЯ
#  ТОЛЬКО ПОИСК В ИНТЕРНЕТЕ с гибридным дополнением
#  Супер-память + Защита от вранья
# ===================================================================

import logging
import os
import json
import sys
import re
import asyncio
import aiohttp
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from logging.handlers import RotatingFileHandler

load_dotenv()

# ==================== ЛОГГЕР ====================
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

console = logging.StreamHandler()
console.setFormatter(formatter)
logger.addHandler(console)

try:
    file_handler = RotatingFileHandler("bot.log", maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except:
    pass

# ==================== КОНФИГ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or 0)
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
if ADMIN_USER_ID and ADMIN_USER_ID not in ALLOWED_USERS:
    ALLOWED_USERS.append(ADMIN_USER_ID)

MODEL_DEFAULT = os.getenv("MODEL_DEFAULT", "deepseek-v4-flash")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

SEARCH_RESULTS_NUM = 25
MAX_HTML_LEN = 6000
MAX_TOKENS_ANSWER = 7000
CACHE_TTL = 3600

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

def now(): return datetime.now(TZ)
def get_current_date(): return now().strftime("%d.%m.%Y")

# ==================== ПУТИ ====================
DATA_DIR = "data"
BACKUP_DIR = "data/backups"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

def memory_path(uid): return os.path.join(DATA_DIR, f"memory_{uid}.json")
def profile_path(uid): return os.path.join(DATA_DIR, f"profile_{uid}.json")
def episodic_path(uid): return os.path.join(DATA_DIR, f"episodic_{uid}.json")
def learning_path(uid): return os.path.join(DATA_DIR, f"learning_{uid}.json")
def counter_path(uid): return os.path.join(DATA_DIR, f"counter_{uid}.json")

# ==================== СУПЕР-ПАМЯТЬ ====================
class SuperMemory:
    """Многоуровневая память для личного помощника"""
    
    def __init__(self, uid):
        self.uid = uid
        self.data_dir = DATA_DIR
        self.backup_dir = BACKUP_DIR
        
        self.short_term = self._safe_load(memory_path(uid), [])
        self.profile = self._safe_load(profile_path(uid), {})
        self.episodic = self._safe_load(episodic_path(uid), [])
        self.learning = self._safe_load(learning_path(uid), {})
        self.counter = self._safe_load(counter_path(uid), {"count": 0}).get("count", 0)
        
        logger.info(f"🧠 Память для {uid} загружена")
    
    def _safe_load(self, filename, default):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    
    def _safe_save(self, filename, data):
        try:
            tmp = filename + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, filename)
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения {filename}: {e}")
            return False
    
    def add_message(self, role: str, content: str):
        """Добавляет сообщение в память"""
        message = {
            "role": role,
            "content": content,
            "timestamp": now().isoformat()
        }
        
        self.short_term.append(message)
        
        if len(self.short_term) > 100:
            old = self.short_term[:-100]
            self._compress_memory(old)
            self.short_term = self.short_term[-100:]
        
        self.counter += 1
        self._extract_info(content)
        self.save()
    
    def _compress_memory(self, messages):
        """Сжимает старые сообщения"""
        for msg in messages:
            content = msg.get('content', '')
            if len(content) < 20:
                continue
            
            # Важные факты
            if any(kw in content.lower() for kw in ['это', 'является', 'состоит', 'находится']):
                self.episodic.append({
                    'content': content[:200],
                    'timestamp': now().isoformat(),
                    'priority': 5
                })
        
        if len(self.episodic) > 200:
            self.episodic = self.episodic[-200:]
    
    def _extract_info(self, text: str):
        """Извлекает личную информацию"""
        patterns = {
            'name': r'(?:меня зовут|зовут|я)\s+([А-Яа-яA-Za-z\s]{2,30})',
            'age': r'(?:мне|возраст)\s+(\d{1,3})\s*(?:лет|года)',
            'city': r'(?:я живу|живу в|из города)\s+([А-Яа-яA-Za-z\s]{2,30})',
            'work': r'(?:я работаю|работаю)\s+([А-Яа-яA-Za-z\s]{2,50})',
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if not self.profile.get(key):
                    self.profile[key] = value
                    logger.info(f"📝 {key}: {value}")
    
    def get_context(self, limit: int = 10) -> list:
        """Возвращает контекст для диалога"""
        return self.short_term[-limit:] if self.short_term else []
    
    def get_personalized_context(self) -> str:
        """Персонализированный контекст"""
        if not self.profile:
            return ""
        
        lines = ["👤 О пользователе:"]
        for key, value in self.profile.items():
            lines.append(f"• {key}: {value}")
        return '\n'.join(lines)
    
    def save(self):
        """Сохраняет все уровни памяти"""
        self._safe_save(memory_path(self.uid), self.short_term)
        self._safe_save(profile_path(self.uid), self.profile)
        self._safe_save(episodic_path(self.uid), self.episodic)
        self._safe_save(learning_path(self.uid), self.learning)
        self._safe_save(counter_path(self.uid), {"count": self.counter})
    
    def get_stats(self) -> dict:
        return {
            'messages': len(self.short_term),
            'profile': len(self.profile),
            'episodic': len(self.episodic),
            'total': self.counter
        }

# Глобальный кэш памяти
_memory_cache = {}

def get_memory(uid) -> SuperMemory:
    if uid not in _memory_cache:
        _memory_cache[uid] = SuperMemory(uid)
    return _memory_cache[uid]

# ==================== HTTP СЕССИЯ ====================
_http_session = None

async def get_http_session():
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        _http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _http_session

# ==================== BROWSERLESS ====================
PLAYWRIGHT_AVAILABLE = False
if BROWSERLESS_WS_ENDPOINT:
    try:
        from playwright.async_api import async_playwright
        PLAYWRIGHT_AVAILABLE = True
        logger.info("✅ Playwright подключен")
    except:
        logger.warning("⚠️ Playwright не установлен")

# ==================== КЭШИ ====================
html_cache = {}
search_cache = {}
answer_cache = {}

def normalize_query(query):
    if not isinstance(query, str):
        return ""
    return re.sub(r'[^\w\s]', '', query.lower())[:100]

# ==================== ПАРСИНГ ====================
def clean_html_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    
    lines = []
    for line in text.split('. '):
        line = line.strip()
        if not line or len(line) < 30:
            continue
        # Пропускаем код и мусор
        if line.startswith(('{', '}', '/*', '.', '#', 'function', 'var ', 'let ', 'const ')):
            continue
        lines.append(line)
    
    return '. '.join(lines[:20])

def extract_date_from_html(html: str) -> str:
    patterns = [
        r'"datePublished":"(\d{4}-\d{2}-\d{2})"',
        r'"date":"(\d{4}-\d{2}-\d{2})"',
        r'(\d{2}\.\d{2}\.\d{4})',
        r'(\d{4})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            date = match.group(1)
            if re.match(r'^\d{4}$', date):
                year = int(date)
                if 2000 <= year <= 2030:
                    return date
            return date
    return "дата не указана"

# ==================== ЗАГРУЗКА КОНТЕНТА ====================
async def fetch_content(url: str, timeout: int = 15):
    if url in html_cache:
        cached = html_cache[url]
        return cached.get("text", ""), cached.get("date", "дата не указана")
    
    result = ""
    pub_date = "дата не указана"
    
    if PLAYWRIGHT_AVAILABLE and BROWSERLESS_WS_ENDPOINT:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(BROWSERLESS_WS_ENDPOINT)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                html = await page.content()
                await page.close()
                
                result = clean_html_text(html)
                pub_date = extract_date_from_html(html)
                
                if result:
                    logger.info(f"✅ Browserless: {url[:50]}")
        except Exception as e:
            logger.warning(f"⚠️ Browserless: {str(e)[:50]}")
    
    if not result:
        session = await get_http_session()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    result = clean_html_text(html)
                    pub_date = extract_date_from_html(html)
                    if result:
                        logger.info(f"✅ HTTP: {url[:50]}")
        except Exception as e:
            logger.warning(f"⚠️ HTTP: {str(e)[:50]}")
    
    if result:
        html_cache[url] = {"text": result, "date": pub_date}
        if len(html_cache) > 50:
            oldest = list(html_cache.keys())[0]
            del html_cache[oldest]
        return result, pub_date
    
    return "", "дата не указана"

async def fetch_multiple_pages(links, max_pages=10):
    if not links:
        return []
    
    semaphore = asyncio.Semaphore(5)
    
    async def fetch_one(url):
        async with semaphore:
            text, date = await fetch_content(url)
            if text and len(text) > 50:
                return {"url": url, "text": text, "date": date}
            return None
    
    tasks = [fetch_one(url) for url in links[:max_pages]]
    fetched = await asyncio.gather(*tasks)
    return [r for r in fetched if r is not None]

# ==================== ПОИСК ====================
async def search_apiserpent(query):
    if not APISERPENT_API_KEY:
        return []
    session = await get_http_session()
    try:
        params = {"q": query, "engine": "google", "num": SEARCH_RESULTS_NUM}
        async with session.get(
            "https://apiserpent.com/api/search",
            params=params,
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=15
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            organic = data.get("results", {}).get("organic", []) if isinstance(data.get("results"), dict) else data.get("organic_results", [])
            for x in organic[:SEARCH_RESULTS_NUM]:
                if isinstance(x, dict):
                    results.append({
                        "title": str(x.get("title", ""))[:120],
                        "snippet": str(x.get("snippet", ""))[:300],
                        "link": str(x.get("url", x.get("link", "#")))[:120]
                    })
            return results
    except:
        return []

async def search_serper(query):
    if not SERPER_API_KEY:
        return []
    session = await get_http_session()
    try:
        async with session.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": SEARCH_RESULTS_NUM},
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            timeout=10
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            for item in data.get("organic", [])[:SEARCH_RESULTS_NUM]:
                results.append({
                    "title": item.get("title", "")[:120],
                    "snippet": item.get("snippet", "")[:300],
                    "link": item.get("link", "#")[:120]
                })
            return results
    except:
        return []

async def search_primary(query):
    norm = normalize_query(query)
    if norm in search_cache:
        cached = search_cache[norm]
        if (datetime.now() - cached['time']).total_seconds() < CACHE_TTL:
            return cached['data']
    
    results = await search_apiserpent(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
        return results
    
    results = await search_serper(query)
    if results:
        search_cache[norm] = {'data': results, 'time': datetime.now()}
    
    return results

# ==================== АНАЛИЗ ====================
def detect_intent(query: str) -> str:
    query_lower = query.lower()
    intents = {
        'best': ['лучшие', 'топ', 'рейтинг', 'список', 'классный'],
        'howto': ['инструкция', 'руководство', 'как сделать', 'как настроить'],
        'problem': ['ошибка', 'проблема', 'не работает', 'исправить'],
        'info': ['что такое', 'кто такой', 'сколько', 'описание', 'обзор'],
        'news': ['новости', 'новый', 'свежий', 'актуальный', 'сегодня'],
    }
    for intent, keywords in intents.items():
        if any(kw in query_lower for kw in keywords):
            return intent
    return 'general'

def calculate_relevance(url: str, text: str, query: str) -> int:
    score = 0
    query_words = set(query.lower().split())
    text_lower = text.lower()
    
    for word in query_words:
        if len(word) > 3 and word in text_lower:
            score += 2
    
    current_year = now().year
    year_match = re.search(r'\b(20[2-5][0-9])\b', text)
    if year_match:
        year = int(year_match.group(1))
        if year >= current_year:
            score += 15
        elif year >= current_year - 1:
            score += 10
        else:
            score -= 5
    
    if len(text) > 500:
        score += 5
    
    return max(0, score)

# ==================== DEEPSEEK API ====================
async def ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER, attempt=0):
    if attempt >= 3:
        return None, "max_retries"
    
    session = await get_http_session()
    try:
        payload = {
            "model": MODEL_DEFAULT,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        async with session.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json=payload,
            timeout=45
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("choices"):
                    return data["choices"][0]["message"]["content"], None
            
            if resp.status == 429:
                await asyncio.sleep(2 ** attempt)
                return await ask_deepseek(messages, temperature, max_tokens, attempt + 1)
            
            return None, f"HTTP {resp.status}"
    except Exception as e:
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
            return await ask_deepseek(messages, temperature, max_tokens, attempt + 1)
        return None, str(e)

# ==================== ГЛАВНАЯ ФУНКЦИЯ - ПОИСК ====================
async def search_and_answer(uid, user_message, history):
    """Основная функция поиска с гибридным дополнением"""
    
    memory = get_memory(uid)
    personal_context = memory.get_personalized_context()
    
    # Генерируем запросы
    variants = [user_message]
    intent = detect_intent(user_message)
    
    if intent == 'best':
        variants.append(f"рейтинг {user_message}")
    elif intent == 'howto':
        variants.append(f"инструкция {user_message}")
    elif intent == 'problem':
        variants.append(f"решение {user_message}")
    
    variants.append(f"{user_message} {now().year}")
    
    # Поиск
    all_results = []
    for variant in variants[:3]:
        results = await search_primary(variant)
        if results:
            all_results.extend(results)
            if len(all_results) >= 20:
                break
    
    if not all_results:
        return "❌ В интернете ничего не найдено. Попробуйте переформулировать запрос."
    
    # Оценка
    scored = []
    for res in all_results[:20]:
        if isinstance(res, dict):
            text = f"{res.get('title', '')} {res.get('snippet', '')}"
            score = calculate_relevance(res.get('link', ''), text, user_message)
            if score > 0:
                scored.append({**res, 'score': score})
    
    scored.sort(key=lambda x: x['score'], reverse=True)
    scored = scored[:15]
    
    if not scored:
        return "❌ Не найдено релевантных источников."
    
    # Загрузка страниц
    links = [r['link'] for r in scored[:10]]
    pages = await fetch_multiple_pages(links, max_pages=8)
    
    if not pages:
        pages = []
        for r in scored[:8]:
            pages.append({
                'url': r['link'],
                'text': f"{r.get('title', '')} {r.get('snippet', '')}",
                'date': 'дата не указана'
            })
    
    # Формируем промпт
    source_text = "\n\n".join([
        f"--- ИСТОЧНИК {i+1} ---\nURL: {p['url']}\nДата: {p.get('date', 'дата не указана')}\n{p['text'][:1500]}"
        for i, p in enumerate(pages)
    ])
    
    system_prompt = f"""
Ты — аналитик. Проанализируй источники и дай точный ответ.

{personal_context}

Запрос: {user_message}

⚠️ ПРАВИЛА:
1. Если знаешь точно - отвечай
2. Если не уверен - напиши "⚠️ [НЕ 100%]"
3. Обязательно перечисли все источники
4. Если данных нет - скажи честно

⚠️ ФОРМАТ ОТВЕТА:
📊 **Использованные источники:** (каждый с кратким содержанием)
📊 **Общие факты:**
⚠️ **Противоречия:** (если есть)
✅ **Вывод:**

ДАННЫЕ:
{source_text}
"""
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer or not re.search(r'Источник \d+', answer):
        # Простой ответ с сырыми данными
        simple = "🔍 Результаты поиска:\n\n"
        for i, r in enumerate(scored[:10], 1):
            simple += f"{i}. {r.get('title', 'Без названия')}\n"
            simple += f"   {r.get('snippet', '')[:150]}\n"
            simple += f"   🔗 {r.get('link', '')}\n\n"
        simple += f"📅 {get_current_date()}"
        return simple
    
    # Проверка на предположения
    speculation = ['возможно', 'вероятно', 'скорее всего', 'должно быть', 'похоже что']
    if any(p in answer.lower() for p in speculation):
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    # Сохраняем в память
    memory.add_message('assistant', answer[:500])
    
    return answer

# ==================== КНОПКИ ====================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🔍 Новый поиск", "❓ Помощь"],
        ["🔄 Сброс", "⏹️ Стоп"]
    ], resize_keyboard=True)

def get_after_answer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"),
         InlineKeyboardButton("✏️ Уточнить", callback_data="refine")]
    ])

# ==================== ОБРАБОТЧИКИ ====================
async def handle_message(update, context):
    """Главный обработчик"""
    try:
        uid = update.effective_user.id
        if ALLOWED_USERS and uid not in ALLOWED_USERS:
            return
        
        user_message = update.effective_message.text[:1000]
        if not user_message:
            return
        
        # Кнопки меню
        if user_message == "🔍 Новый поиск":
            context.user_data.clear()
            await safe_reply(update, "🔍 Задай вопрос для поиска в интернете.")
            return
        
        elif user_message == "❓ Помощь":
            await safe_reply(update,
                "❓ **Помощь**\n\n"
                "🔍 **Новый поиск** - задай вопрос, я найду в интернете\n"
                "🔄 **Сброс** - очистить диалог\n"
                "⏹️ **Стоп** - остановить\n\n"
                "Просто напиши вопрос и я найду ответ!"
            )
            return
        
        elif user_message == "🔄 Сброс":
            context.user_data.clear()
            await safe_reply(update, "🔄 Диалог сброшен.")
            return
        
        elif user_message == "⏹️ Стоп":
            context.user_data.clear()
            await safe_reply(update, "⏹️ Остановлено.")
            return
        
        if user_message.startswith('/'):
            return
        
        # Уточнение
        if context.user_data.get('awaiting_followup'):
            answer = await handle_followup(update, context, user_message)
            if answer:
                await safe_reply(update, answer)
            return
        
        # НОВЫЙ ВОПРОС
        uid = update.effective_user.id
        memory = get_memory(uid)
        history = memory.get_context(limit=10)
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['start_time'] = time.time()
        context.user_data['query'] = user_message
        
        await safe_reply(update, "⏳ Ищу информацию...")
        
        # Поиск
        answer = await search_and_answer(uid, user_message, history)
        
        # Таймер
        elapsed = int(time.time() - context.user_data['start_time'])
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        # Сохраняем для уточнений
        context.user_data['last_answer'] = answer
        context.user_data['awaiting_followup'] = True
        
        await safe_reply(update, answer, reply_markup=get_after_answer_keyboard())
    
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await safe_reply(update, "⚠️ Ошибка. Попробуйте еще раз.")

async def handle_after_answer_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "new_search":
        context.user_data.clear()
        try:
            await query.edit_message_text("🔍 Новый поиск. Напиши вопрос.")
        except:
            await query.message.reply_text("🔍 Новый поиск. Напиши вопрос.")
    
    elif query.data == "refine":
        last_query = context.user_data.get('query', '')
        if not last_query:
            await query.edit_message_text("⏳ Нет активного вопроса.")
            return
        
        context.user_data['awaiting_followup'] = True
        try:
            await query.edit_message_text(
                f"✏️ Уточни по запросу:\n\n**{last_query}**\n\nНапиши что именно уточнить."
            )
        except:
            await query.message.reply_text(
                f"✏️ Уточни по запросу:\n\n**{last_query}**\n\nНапиши что именно уточнить."
            )

async def handle_followup(update, context, user_message):
    """Обработка уточнения"""
    last_answer = context.user_data.get('last_answer', '')
    
    system_prompt = f"""
Пользователь уточняет по предыдущему ответу.

Предыдущий ответ: {last_answer[:500]}

Уточнение: {user_message}

Ответь на уточнение кратко и по делу. Если нужно - обнови информацию из интернета.
"""
    messages = [{"role": "system", "content": system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=2000)
    
    if err or not answer:
        return "⚠️ Не удалось обработать уточнение."
    
    # Проверка на предположения
    speculation = ['возможно', 'вероятно', 'скорее всего', 'должно быть']
    if any(p in answer.lower() for p in speculation):
        answer = f"⚠️ [НЕ 100%]\n\n{answer}"
    
    return answer

async def safe_reply(update, text, reply_markup=None):
    if not text:
        text = "⚠️ Пустой ответ."
    msg = update.effective_message
    if not msg:
        return
    try:
        await msg.reply_text(text, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")

# ==================== КОМАНДЫ ====================
async def start(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    
    await safe_reply(
        update,
        "👋 Привет! Я поисковый ассистент.\n\n"
        "🔍 **Просто напиши вопрос** - я найду ответ в интернете\n"
        "📊 Покажу источники и дам точный вывод\n"
        "🧠 Запоминаю тебя и твои предпочтения\n\n"
        "Попробуй спросить что-нибудь!",
        reply_markup=get_main_keyboard()
    )

async def stats_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    
    memory = get_memory(uid)
    stats = memory.get_stats()
    
    await safe_reply(
        update,
        f"📊 **Статистика**\n\n"
        f"💬 В памяти: {stats['messages']} сообщений\n"
        f"👤 В профиле: {stats['profile']} полей\n"
        f"⭐ Важных фактов: {stats['episodic']}\n"
        f"📝 Всего сообщений: {stats['total']}"
    )

async def forget_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    
    if uid in _memory_cache:
        del _memory_cache[uid]
    
    # Удаляем файлы
    for path in [memory_path(uid), profile_path(uid), episodic_path(uid), learning_path(uid), counter_path(uid)]:
        try:
            os.remove(path)
        except:
            pass
    
    context.user_data.clear()
    await safe_reply(update, "🧹 Всё забыто! Начинаем с чистого листа.")

# ==================== ЗАПУСК ====================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("forget", forget_command))
    
    app.add_handler(CallbackQueryHandler(handle_after_answer_callback, pattern="^(new_search|refine)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 БОТ ЗАПУЩЕН - ТОЛЬКО ПОИСК!")
    logger.info("🔍 Ищу в интернете, не вру, запоминаю пользователя")
    app.run_polling()

if __name__ == "__main__":
    main()
