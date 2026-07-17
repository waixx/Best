# ================================================================
#  BroWaix Bot — ИСПРАВЛЕННАЯ ВЕРСИЯ (сборка из всех источников)
#  - Теперь DeepSeek получает ВСЕ загруженные страницы
#  - Сортировка по информативности
#  - Нумерация источников
# ================================================================

import logging
import os
import json
import sys
import re
import asyncio
import aiohttp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from logging.handlers import RotatingFileHandler

load_dotenv()

# ==================== ЛОГГЕР ====================
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=2)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
console = logging.StreamHandler()
console.setFormatter(formatter)
logger.addHandler(console)

# ==================== ПЕРЕМЕННЫЕ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT", "")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or 0)
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
if ADMIN_USER_ID and ADMIN_USER_ID not in ALLOWED_USERS:
    ALLOWED_USERS.append(ADMIN_USER_ID)

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")


def now():
    return datetime.now(TZ)


def get_current_date():
    return now().strftime("%d.%m.%Y")


# ==================== ОПТИМАЛЬНЫЕ НАСТРОЙКИ ====================
MODEL_DEFAULT = os.getenv("MODEL_DEFAULT", "deepseek-v4-flash")
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "deepseek-v4-pro")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

SEARCH_RESULTS_NUM = 30
TOP_RESULTS_SHOW = 5
MAX_HTML_LEN = 8000
MAX_TOKENS_ANSWER = 3000
CACHE_TTL = 86400

MODE_MODEL = "model_only"
MODE_HYBRID = "hybrid"
MODE_INTERNET = "internet_only"

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

# ==================== ПУТИ ====================
DATA_DIR, BACKUP_DIR = "data", "data/backups"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)


def memory_path(uid):
    return os.path.join(DATA_DIR, f"memory_{uid}.json")


def profile_path(uid):
    return os.path.join(DATA_DIR, f"profile_{uid}.json")


def counter_path(uid):
    return os.path.join(DATA_DIR, f"counter_{uid}.json")


# ==================== ФАЙЛОВЫЕ ОПЕРАЦИИ ====================
def atomic_write(filename, data, as_json=True):
    tmp = filename + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            if as_json:
                json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filename)
        return True
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except:
                pass
        return False


def atomic_read(filename, default=None, as_json=True):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f) if as_json else f.read()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def load_profile(uid):
    return atomic_read(profile_path(uid), default={})


def save_profile(uid, profile, backup=True):
    profile["updated"] = now().strftime("%d.%m.%Y %H:%M:%S")
    if not atomic_write(profile_path(uid), profile):
        return False
    if backup:
        create_backup(uid, "profile")
    return True


def load_counter(uid):
    return atomic_read(counter_path(uid), default={"count": 0}).get("count", 0)


def save_counter(uid, count):
    atomic_write(counter_path(uid), {"count": count})


def load_memory_raw(uid):
    return atomic_read(memory_path(uid), default=[])


def create_backup(uid, data_type):
    try:
        ts = now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.join(BACKUP_DIR, f"{data_type}_{uid}_{ts}.json")
        if data_type == "profile":
            atomic_write(fname, load_profile(uid))
        elif data_type == "memory":
            atomic_write(fname, load_memory_raw(uid))
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith(f"{data_type}_{uid}_")])
        for old in backups[:-5]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except:
                pass
        return True
    except Exception:
        return False


async def restore_backup(uid, data_type):
    try:
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith(f"{data_type}_{uid}_")])
        if not backups:
            return False
        with open(os.path.join(BACKUP_DIR, backups[-1]), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data_type == "profile":
            save_profile(uid, data, backup=False)
        elif data_type == "memory":
            await save_memory(uid, data, backup=False)
        return True
    except Exception:
        return False


# ==================== АВТОВОССТАНОВЛЕНИЕ ====================
async def auto_restore_all_users():
    logger.info("🔄 Проверка данных при старте...")
    try:
        if not os.path.exists(BACKUP_DIR):
            return
        user_ids = set()
        for fname in os.listdir(BACKUP_DIR):
            parts = fname.split('_')
            if len(parts) >= 2 and parts[0] in ('profile', 'memory'):
                try:
                    user_ids.add(int(parts[1]))
                except ValueError:
                    continue
        for uid in user_ids:
            mem_data = atomic_read(memory_path(uid), default=None)
            prof_data = atomic_read(profile_path(uid), default=None)
            need_restore = False
            if mem_data is None or (isinstance(mem_data, list) and len(mem_data) == 0):
                need_restore = True
            if prof_data is None or (isinstance(prof_data, dict) and len(prof_data) == 0):
                need_restore = True
            if need_restore:
                pr = await restore_backup(uid, "profile")
                mr = await restore_backup(uid, "memory")
                if pr or mr:
                    logger.info(f"✅ Пользователь {uid} автоматически восстановлен")
    except Exception as ex:
        logger.error(f"Ошибка auto_restore: {ex}")


# ==================== ПАМЯТЬ ====================
STOP_WORDS = {'это', 'так', 'вот', 'ну', 'просто', 'очень', 'что', 'как', 'где', 'когда', 'для', 'без', 'по'}


def extract_key_points(text, max_len=40):
    if not text or len(text) <= max_len:
        return str(text)[:max_len]
    imp = [w for w in text.split() if w.lower() not in STOP_WORDS and len(w) > 2]
    result = ' '.join(imp[:8])[:max_len]
    return result + "..." if len(result) == max_len else result


def compress_history(history):
    if not isinstance(history, list):
        return []
    if len(history) <= 50:
        return history
    recent = history[-5:]
    old = history[:-5]
    summary = []
    for m in old[-10:]:
        if not isinstance(m, dict):
            continue
        r, c = m.get("role", ""), m.get("content", "")
        if r == "user":
            summary.append(f"Q: {extract_key_points(c, 50)}")
        elif r == "assistant":
            summary.append(f"A: {extract_key_points(c, 50)}")
    if summary:
        return [{"role": "system", "content": "📚 История:\n" + "\n".join(summary[-5:])}] + recent
    return recent


def _update_level_2(uid, messages):
    try:
        profile = load_profile(uid)
        profile.setdefault("level_2", [])
        ts = now().strftime("%d.%m")
        for m in messages[-20:]:
            if not isinstance(m, dict):
                continue
            r, c = m.get("role", ""), m.get("content", "")
            if r == "user":
                profile["level_2"].append(f"[{ts}] Q: {extract_key_points(c, 40)}")
            elif r == "assistant":
                profile["level_2"].append(f"[{ts}] A: {extract_key_points(c, 40)}")
        if len(profile["level_2"]) > 30:
            profile["level_2"] = profile["level_2"][-30:]
        save_profile(uid, profile, backup=False)
    except Exception as ex:
        logger.error(f"Ошибка _update_level_2: {ex}")


def load_memory(uid):
    raw = load_memory_raw(uid)
    if len(raw) > 50:
        _update_level_2(uid, raw[:-5])
        return raw[-5:]
    return compress_history(raw)


async def save_memory(uid, history, backup=True):
    if not isinstance(history, list):
        return False
    if not atomic_write(memory_path(uid), compress_history(history)):
        return False
    if backup:
        create_backup(uid, "memory")
    save_counter(uid, load_counter(uid) + 1)
    return True


# ==================== HTTP ====================
_http_session = None
_session_lock = asyncio.Lock()


async def get_http_session():
    global _http_session
    async with _session_lock:
        if _http_session is None:
            connector = aiohttp.TCPConnector(
                limit=15,
                limit_per_host=8,
                ttl_dns_cache=300
            )
            timeout = aiohttp.ClientTimeout(
                total=60,
                connect=15,
                sock_read=30
            )
            _http_session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            )
            logger.info("✅ Единый aiohttp.ClientSession создан")
        return _http_session


async def cleanup_http_session():
    global _http_session
    if _http_session:
        await _http_session.close()
        _http_session = None


# ==================== BROWSERLESS ====================
PLAYWRIGHT_AVAILABLE = False
if BROWSERLESS_WS_ENDPOINT:
    try:
        from playwright.async_api import async_playwright
        PLAYWRIGHT_AVAILABLE = True
        logger.info(f"✅ Playwright подключен к Browserless: {BROWSERLESS_WS_ENDPOINT}")
    except ImportError:
        logger.warning("⚠️ Playwright не установлен, только HTTP")

html_cache = {}
search_cache = {}
answer_cache = {}


def normalize_query(query):
    if not isinstance(query, str):
        return ""
    return re.sub(r'[^\w\s]', '', query.lower())[:100]


def get_cached_search(query):
    norm = normalize_query(query)
    if norm in search_cache:
        cached = search_cache[norm]
        if (datetime.now() - cached['time']).total_seconds() < CACHE_TTL:
            return cached['data']
    return None


def set_cached_search(query, data):
    norm = normalize_query(query)
    search_cache[norm] = {'data': data, 'time': datetime.now()}
    if len(search_cache) > 50:
        oldest = min(search_cache.keys(), key=lambda k: search_cache[k]['time'])
        del search_cache[oldest]


def get_cached_answer(query):
    norm = normalize_query(query)
    if norm in answer_cache:
        cached = answer_cache[norm]
        if (datetime.now() - cached['time']).total_seconds() < CACHE_TTL:
            return cached['data']
    return None


def set_cached_answer(query, data):
    norm = normalize_query(query)
    answer_cache[norm] = {'data': data, 'time': datetime.now()}
    if len(answer_cache) > 100:
        oldest = min(answer_cache.keys(), key=lambda k: answer_cache[k]['time'])
        del answer_cache[oldest]


async def fetch_content(url: str) -> str:
    if url in html_cache:
        return html_cache[url]

    result = ""

    if PLAYWRIGHT_AVAILABLE and BROWSERLESS_WS_ENDPOINT:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(BROWSERLESS_WS_ENDPOINT)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                html = await page.content()
                await page.close()
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 500:
                    result = text[:MAX_HTML_LEN]
                    logger.info(f"✅ Browserless: {url[:50]}")
        except Exception as e:
            logger.warning(f"Browserless ошибка: {e}")

    if not result:
        session = await get_http_session()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    text = re.sub(r'<[^>]+>', ' ', html)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if len(text) > 500:
                        result = text[:MAX_HTML_LEN]
                        logger.info(f"✅ HTTP: {url[:50]}")
        except Exception as e:
            logger.warning(f"HTTP ошибка: {e}")

    if result:
        html_cache[url] = result
        if len(html_cache) > 100:
            oldest = list(html_cache.keys())[0]
            del html_cache[oldest]
        return result

    logger.warning(f"❌ Не удалось загрузить {url[:50]}")
    return ""


async def fetch_multiple_pages(links, max_pages=SEARCH_RESULTS_NUM, top_k=TOP_RESULTS_SHOW):
    if not links:
        return []
    results = []
    semaphore = asyncio.Semaphore(5)

    async def fetch_one(url):
        async with semaphore:
            content = await fetch_content(url)
            if content and len(content) > 100:
                return {"url": url, "text": content}
            return None

    tasks = [fetch_one(url) for url in links[:max_pages]]
    fetched = await asyncio.gather(*tasks)
    valid = [r for r in fetched if r is not None]
    valid.sort(key=lambda x: len(x["text"]), reverse=True)
    return valid[:top_k]


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
            timeout=30
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            results = []
            organic = data.get("results", {}).get("organic", []) if isinstance(data.get("results"),
                                                                               dict) else data.get("organic_results", [])
            for x in organic[:SEARCH_RESULTS_NUM]:
                if isinstance(x, dict):
                    results.append({
                        "title": str(x.get("title", ""))[:120],
                        "snippet": str(x.get("snippet", ""))[:300],
                        "link": str(x.get("url", x.get("link", "#")))[:120]
                    })
            return results
    except Exception as e:
        logger.warning(f"APISerpent ошибка: {e}")
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
            timeout=15
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
    except Exception as e:
        logger.warning(f"Serper ошибка: {e}")
        return []


async def search_primary(query):
    cached = get_cached_search(query)
    if cached:
        return cached
    results = await search_apiserpent(query)
    if results:
        set_cached_search(query, results)
        return results
    results = await search_serper(query)
    if results:
        set_cached_search(query, results)
    return results


def extract_year_from_text(text):
    if not isinstance(text, str):
        return None
    match = re.search(r'\b(20[2-9][0-9])\b', text)
    return int(match.group(1)) if match else None


def assess_relevance(results, query):
    if not results or not isinstance(results, list):
        return []

    query_year = None
    year_match = re.search(r'\b(20[2-9][0-9])\b', query)
    if year_match:
        query_year = int(year_match.group(1))

    requires_year = any(word in query.lower() for word in ['новинк', 'последн', 'свеж', 'актуальн'])
    stop_words = {'найди', 'пожалуйста', 'помоги', 'мне', 'лучшие', 'скажи', 'расскажи', 'покажи', 'найти'}
    keywords = [w.lower() for w in re.sub(r'[^\w\s]', '', query).split()
                if w.lower() not in stop_words and len(w) > 3]

    scored = []
    for res in results:
        if not isinstance(res, dict):
            continue
        text = (res.get('title', '') or '') + ' ' + (res.get('snippet', '') or '')
        text_lower = text.lower()
        link = res.get('link', '').lower()

        keyword_score = sum(3 for kw in keywords if kw in text_lower)

        domain_score = 0
        if any(zone in link for zone in ['.gov', '.edu', '.org', 'wikipedia.org']):
            domain_score += 5

        spam = ['ozon', 'wildberries', 'aliexpress', 'avito', 'amazon', 'ebay', 'taobao']
        if any(s in link for s in spam):
            domain_score -= 8

        year = extract_year_from_text(text)
        year_score = 0
        if year:
            if query_year and year == query_year:
                year_score = 10
            elif year >= 2025:
                year_score = 8
        else:
            if requires_year:
                year_score = -2

        total = keyword_score + year_score + domain_score
        scored.append({**res, 'score': total, 'year': year})

    relevant = [r for r in scored if r['score'] > 0]
    relevant.sort(key=lambda x: x['score'], reverse=True)
    return relevant


def mark_source(mode: str, text: str, is_cached: bool = False, is_speculation: bool = False) -> str:
    markers = {
        "model_only": "🧠 [ЗНАНИЯ МОДЕЛИ]",
        "hybrid": "🔍 [ГИБРИД]",
        "internet_only": "🌐 [ИНТЕРНЕТ]",
        "local_memory": "💾 [ЛОКАЛЬНАЯ ПАМЯТЬ]"
    }

    marker = markers.get(mode, "📌 [ИСТОЧНИК НЕИЗВЕСТЕН]")

    if is_cached:
        marker = f"📦 [КЭШ] {marker}"

    if is_speculation:
        return f"⚠️ [НЕ 100%]\n\n⚠️ ВНИМАНИЕ: Это предположение, не подтвержденный факт.\n\n{text}"

    return f"{marker}\n\n{text}"


async def ask_deepseek(messages, temperature=1.0, max_tokens=MAX_TOKENS_ANSWER):
    session = await get_http_session()
    try:
        payload = {
            "model": MODEL_DEFAULT,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": 0.95
        }
        async with session.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json=payload,
            timeout=30
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("choices"):
                    return data["choices"][0]["message"]["content"], None
            if resp.status == 429:
                await asyncio.sleep(2)
                return await ask_deepseek(messages, temperature, max_tokens)
            return None, f"HTTP {resp.status}"
    except Exception as e:
        return None, str(e)


async def generate_search_query(query):
    stop = {'найди', 'пожалуйста', 'помоги', 'мне', 'лучшие', 'скажи', 'расскажи', 'покажи', 'найти'}
    words = [w for w in re.sub(r'[^\w\s]', '', query).split()
             if w.lower() not in stop and len(w) > 2]
    if not words:
        return [query]
    base = " ".join(words[:5])
    if not re.search(r'\b20[2-9][0-9]\b', base):
        base += f" {now().year}"
    return [base]


def build_profile_context(profile):
    parts = []
    for k, v in profile.items():
        if k in ("updated", "level_2"):
            continue
        if isinstance(v, str):
            parts.append(f"{k}: {v[:40]}")
    return ". ".join(parts)[:150]


async def generate_model_only(uid, user_message, history, profile):
    ctx = build_profile_context(profile)
    system_prompt = f"""Ты — честный ассистент. Отвечай ТОЛЬКО из своих знаний.
Если не знаешь — скажи "Я не знаю".
ЗАПРЕЩЕНО использовать фразы: "возможно", "вероятно", "скорее всего", "должно быть".
Если неуверен — напиши "Не 100%, предположение".

Сегодня: {get_current_date()}
Контекст: {ctx}"""

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=0.0)

    if err or not answer:
        return "⚠️ Не удалось получить ответ от модели."

    forbidden = ['возможно', 'вероятно', 'скорее всего', 'должно быть', 'похоже что']
    is_speculation = any(p in answer.lower() for p in forbidden)

    return mark_source("model_only", answer, is_cached=False, is_speculation=is_speculation)


# ==================== ИСПРАВЛЕННАЯ ФУНКЦИЯ — сборка из ВСЕХ источников ====================
async def generate_hybrid(uid, user_message, history, profile):
    cached = get_cached_answer(user_message)
    if cached:
        return mark_source("hybrid", cached, is_cached=True, is_speculation=False)

    variants = await generate_search_query(user_message)
    results = await search_primary(variants[0])

    if not results:
        return await generate_model_only(uid, user_message, history, profile)

    scored = assess_relevance(results, user_message)
    links = [r['link'] for r in (scored or results)[:SEARCH_RESULTS_NUM]]
    pages = await fetch_multiple_pages(links, max_pages=SEARCH_RESULTS_NUM, top_k=TOP_RESULTS_SHOW)

    # ⭐ СОБИРАЕМ ВСЕ СТРАНИЦЫ С НУМЕРАЦИЕЙ
    if pages:
        pages_sorted = sorted(pages, key=lambda x: len(x["text"]), reverse=True)
        stext = "\n\n".join([f"--- ИСТОЧНИК {i+1}: {p['url']} ---\n{p['text']}" for i, p in enumerate(pages_sorted[:TOP_RESULTS_SHOW])])
        logger.info(f"📊 Гибрид: использовано {len(pages_sorted[:TOP_RESULTS_SHOW])} источников")
    else:
        stext = "\n\n".join([
            f"--- ИСТОЧНИК {i+1}: {r['link']} ---\nЗаголовок: {r.get('title', '')}\nОписание: {r.get('snippet', '')}"
            for i, r in enumerate((scored or results)[:TOP_RESULTS_SHOW])
        ])

    system_prompt = f"""Ты — честный ассистент. Приоритет — данные из интернета.
Если данных нет — используй свои знания, но отметь это.
Если не знаешь — скажи "Я не знаю".
ЗАПРЕЩЕНО использовать фразы: "возможно", "вероятно", "скорее всего".

Запрос: {user_message}
Сегодня: {get_current_date()}
Контекст: {build_profile_context(profile)}

ДАННЫЕ ИЗ ИНТЕРНЕТА (ВСЕ ИСТОЧНИКИ):
{stext}"""

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=1.0)

    if err or not answer:
        return await generate_internet_only(uid, user_message, history, profile)

    forbidden = ['возможно', 'вероятно', 'скорее всего', 'должно быть', 'похоже что']
    is_speculation = any(p in answer.lower() for p in forbidden)

    has_links = 'http' in answer or 'ссылка' in answer.lower() or 'источник' in answer.lower()
    if not has_links and not is_speculation:
        is_speculation = True

    result = mark_source("hybrid", answer, is_cached=False, is_speculation=is_speculation)
    set_cached_answer(user_message, result)
    return result


# ==================== ИСПРАВЛЕННАЯ ФУНКЦИЯ — сборка из ВСЕХ источников ====================
async def generate_internet_only(uid, user_message, history, profile):
    cached = get_cached_answer(user_message)
    if cached:
        return mark_source("internet_only", cached, is_cached=True, is_speculation=False)

    variants = await generate_search_query(user_message)
    all_results = await search_primary(variants[0])

    if not all_results:
        return "❌ В интернете ничего не найдено. Я не буду выдумывать."

    scored = assess_relevance(all_results, user_message)
    links = [r['link'] for r in (scored or all_results)[:SEARCH_RESULTS_NUM]]
    pages = await fetch_multiple_pages(links, max_pages=SEARCH_RESULTS_NUM, top_k=TOP_RESULTS_SHOW)

    # ⭐ СОБИРАЕМ ВСЕ СТРАНИЦЫ С НУМЕРАЦИЕЙ
    if pages:
        pages_sorted = sorted(pages, key=lambda x: len(x["text"]), reverse=True)
        stext = "\n\n".join([f"--- ИСТОЧНИК {i+1}: {p['url']} ---\n{p['text']}" for i, p in enumerate(pages_sorted[:TOP_RESULTS_SHOW])])
        logger.info(f"📊 Интернет: использовано {len(pages_sorted[:TOP_RESULTS_SHOW])} источников")
    else:
        stext = "\n\n".join([
            f"--- ИСТОЧНИК {i+1}: {r['link']} ---\nЗаголовок: {r.get('title', '')}\nОписание: {r.get('snippet', '')}"
            for i, r in enumerate((scored or all_results)[:TOP_RESULTS_SHOW])
        ])

    system_prompt = f"""Ты — честный ассистент. Твой ЕДИНСТВЕННЫЙ источник — данные из интернета.
НЕ используй свои знания.
НЕ додумывай.
Если в данных нет ответа — напиши "В данных нет ответа".
Каждый факт сопровождай ссылкой.
СОБИРАЙ ИНФОРМАЦИЮ ИЗ ВСЕХ ИСТОЧНИКОВ, А НЕ ТОЛЬКО ИЗ ПЕРВОГО.

Запрос: {user_message}
Сегодня: {get_current_date()}
Контекст: {build_profile_context(profile)}

ДАННЫЕ ИЗ ИНТЕРНЕТА (ВСЕ ИСТОЧНИКИ):
{stext}"""

    messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": user_message}]
    answer, err = await ask_deepseek(messages, temperature=1.0)

    if err or not answer:
        ans = "🔍 Результаты поиска:\n\n"
        for i, r in enumerate((scored or all_results)[:TOP_RESULTS_SHOW], 1):
            ans += f"{i}. {r.get('title', 'Без названия')}\n"
            ans += f"   {r.get('snippet', 'Нет описания')[:150]}\n"
            if r.get('link') and r['link'] != '#':
                ans += f"   Ссылка: {r['link']}\n"
            ans += "\n"
        ans += f"📅 {get_current_date()}"
        return mark_source("internet_only", ans, is_cached=False, is_speculation=False)

    forbidden = ['возможно', 'вероятно', 'скорее всего', 'должно быть', 'похоже что']
    is_speculation = any(p in answer.lower() for p in forbidden)

    has_links = 'http' in answer or 'ссылка' in answer.lower() or 'источник' in answer.lower()
    if not has_links:
        is_speculation = True

    result = mark_source("internet_only", answer, is_cached=False, is_speculation=is_speculation)
    set_cached_answer(user_message, result)
    return result


def get_mode_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🧠 Только знания", callback_data=f"mode_{MODE_MODEL}"),
            InlineKeyboardButton("🔍 Гибрид", callback_data=f"mode_{MODE_HYBRID}"),
        ],
        [
            InlineKeyboardButton("🌐 Только интернет", callback_data=f"mode_{MODE_INTERNET}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    await safe_reply(update,
                     "👋 Привет! Я честный ассистент с интернетом.\n\n"
                     "📋 Команды:\n"
                     "/profile — мои знания о тебе\n"
                     "/memory — поиск в истории\n"
                     "/stats — статистика\n"
                     "/forget — забыть всё\n"
                     "/restore — восстановить из бэкапа\n\n"
                     "Просто напиши вопрос — я покажу кнопки с выбором режима."
                     )


async def profile_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    p = load_profile(uid)
    if not p:
        await safe_reply(update, "📭 Я пока ничего не знаю о тебе.")
        return
    lines = ["🧠 Память:", f"• сообщений: {len(load_memory_raw(uid))}"]
    lines.append("\n👤 Личное:")
    exclude = {'updated', 'level_2'}
    personal = {k: v for k, v in p.items() if k not in exclude}
    if personal:
        for k, v in personal.items():
            lines.append(f"• {k}: {v}")
    else:
        lines.append("• Пока ничего не запомнил")
    lines.append(f"\n🔄 Обновлено: {p.get('updated', 'неизвестно')}")
    await safe_reply(update, "\n".join(lines))


async def memory_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    if not context.args:
        await safe_reply(update, "🔍 Поиск: /memory что искать")
        return
    query = ' '.join(context.args)
    q = query.lower()
    res = []
    for m in load_memory_raw(uid)[-50:]:
        if not isinstance(m, dict):
            continue
        c = m.get("content", "")
        if q in c.lower():
            role = "👤" if m.get("role") == "user" else "🤖"
            res.append(f"{role} {extract_key_points(c, 80)}")
    if not res:
        await safe_reply(update, f"📭 Ничего не найдено: '{query}'")
        return
    lines = [f"🔍 Результаты '{query}':"] + [f"{i}. {r}" for i, r in enumerate(res[:10], 1)]
    await safe_reply(update, "\n".join(lines))


async def stats_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    p = load_profile(uid)
    raw = load_memory_raw(uid)
    lines = ["📊 Статистика:"]
    lines.append(f"• Обработано сообщений: {load_counter(uid)}")
    lines.append(f"• В истории: {len(raw)}")
    lines.append(f"• Сжатых пунктов: {len(p.get('level_2', []))}")
    bc = len([f for f in os.listdir(BACKUP_DIR) if f.startswith(f"profile_{uid}_")])
    lines.append(f"💾 Бэкапов: {bc}")
    await safe_reply(update, "\n".join(lines))


async def forget_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    save_profile(uid, {})
    await save_memory(uid, [], backup=True)
    save_counter(uid, 0)
    await safe_reply(update, "🧹 Я забыл всё, что знал о тебе!")


async def restore_command(update, context):
    uid = update.effective_user.id
    if ALLOWED_USERS and uid not in ALLOWED_USERS:
        return
    pr = await restore_backup(uid, "profile")
    mr = await restore_backup(uid, "memory")
    if pr or mr:
        await safe_reply(update, "✅ Восстановлено!\n" + ("📋 Профиль\n" if pr else "") + ("💬 История" if mr else ""))
    else:
        await safe_reply(update, "❌ Нет бэкапов.")


async def handle_remember(update, context):
    uid = update.effective_user.id
    text = update.effective_message.text[8:].strip()
    p = load_profile(uid)
    if ":" in text:
        k, v = text.split(":", 1)
        k, v = k.strip(), v.strip()
        p[k] = v
        if save_profile(uid, p):
            await safe_reply(update, f"✅ Запомнил: {k} = {v}")
        else:
            await safe_reply(update, "❌ Не удалось сохранить.")
    else:
        p.setdefault("факты", []).append(text)
        if save_profile(uid, p):
            await safe_reply(update, f"✅ Запомнил факт: {text}")
        else:
            await safe_reply(update, "❌ Не удалось сохранить факт.")


async def safe_reply(update: Update, text: str, reply_markup=None):
    if not text or not isinstance(text, str):
        text = "⚠️ Пустой ответ."
    msg = update.effective_message
    if msg is None:
        return

    try:
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await msg.reply_text(text[i:i + 4096], disable_web_page_preview=True, reply_markup=reply_markup)
        else:
            await msg.reply_text(text, disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as ex:
        logger.error(f"Ошибка safe_reply: {ex}")
        try:
            await msg.reply_text(text[:4096], reply_markup=reply_markup)
        except Exception:
            pass


async def handle_message(update, context):
    try:
        uid = update.effective_user.id
        if ALLOWED_USERS and uid not in ALLOWED_USERS:
            return

        user_message = update.effective_message.text[:1000]
        if not user_message:
            return

        if user_message.lower().startswith("запомни "):
            await handle_remember(update, context)
            return

        context.user_data['last_query'] = user_message
        context.user_data['uid'] = uid
        context.user_data['history'] = load_memory(uid)
        context.user_data['profile'] = load_profile(uid)

        await safe_reply(
            update,
            "🤔 Как мне ответить на твой вопрос?",
            reply_markup=get_mode_keyboard()
        )

    except Exception as e:
        logger.error(f"Ошибка handle_message: {e}")
        await safe_reply(update, "⚠️ Произошла ошибка. Попробуйте еще раз.")


async def handle_mode_selection(update, context):
    query = update.callback_query
    await query.answer()

    try:
        uid = context.user_data.get('uid')
        user_message = context.user_data.get('last_query')
        history = context.user_data.get('history', [])
        profile = context.user_data.get('profile', {})

        if not user_message:
            await query.edit_message_text("⏳ Вопрос утерян, напиши заново.")
            return

        mode = query.data.replace("mode_", "")
        await query.edit_message_text(f"⏳ Обрабатываю в режиме: {mode}...")

        if mode == MODE_MODEL:
            answer = await generate_model_only(uid, user_message, history, profile)
        elif mode == MODE_HYBRID:
            answer = await generate_hybrid(uid, user_message, history, profile)
        else:
            answer = await generate_internet_only(uid, user_message, history, profile)

        if answer and len(answer) > 10:
            clean_answer = re.sub(r'<[^>]+>', '', answer)
            history.append({"role": "assistant", "content": clean_answer,
                            "timestamp": now().strftime("%Y-%m-%d %H:%M:%S")})
            await save_memory(uid, history)

        try:
            await query.message.delete()
        except Exception:
            pass

        if len(answer) > 4096:
            for i in range(0, len(answer), 4096):
                await query.message.reply_text(answer[i:i + 4096], disable_web_page_preview=True)
        else:
            await query.message.reply_text(answer, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Ошибка handle_mode_selection: {e}")
        await query.message.reply_text("⚠️ Произошла ошибка. Попробуйте еще раз.")


async def cleanup_caches_periodic():
    while True:
        await asyncio.sleep(3600)
        try:
            if len(html_cache) > 100:
                keys = list(html_cache.keys())[:20]
                for k in keys:
                    del html_cache[k]
            if len(search_cache) > 50:
                keys = list(search_cache.keys())[:10]
                for k in keys:
                    del search_cache[k]
            if len(answer_cache) > 100:
                keys = list(answer_cache.keys())[:20]
                for k in keys:
                    del answer_cache[k]
        except Exception as e:
            logger.error(f"Ошибка очистки кэша: {e}")


async def post_init(application):
    asyncio.create_task(cleanup_caches_periodic())
    logger.info("🚀 Бот запущен")


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(auto_restore_all_users())
    except Exception as e:
        logger.error(f"Ошибка авто-восстановления: {e}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("restore", restore_command))
    app.add_handler(CallbackQueryHandler(handle_mode_selection, pattern="^mode_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 БОТ ЗАПУЩЕН (исправлена сборка из всех источников)")
    app.run_polling()


if __name__ == "__main__":
    main()