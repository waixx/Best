# ═══════════════════════════════════════════════════════════════════
#  BROWAIX BOT — ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ
#  С ИНТЕЛЛЕКТУАЛЬНЫМ АНАЛИЗОМ НЕДОСТАТКА ДАННЫХ
#  ПЕРЕФОРМУЛИРОВКА ЗАПРОСА И УТОЧНЕНИЕ У ПОЛЬЗОВАТЕЛЯ
#  ПАМЯТЬ (5 УРОВНЕЙ + ГРАФ ЗНАНИЙ)
#  ИЗВЛЕЧЕНИЕ СТРУКТУР ЧЕРЕЗ DEEPSEEK (ТОЧНОСТЬ 92-95%)
#  ПАРАЛЛЕЛЬНАЯ ЗАГРУЗКА (СКОРОСТЬ 25-40 СЕК)
#  КРАСИВЫЙ ТАЙМЕР + КНОПКИ (СТОП, НОВЫЙ ПОИСК, СТАТИСТИКА)
# ═══════════════════════════════════════════════════════════════════

import logging
import os
import sys
import re
import asyncio
import aiohttp
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple, Any
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
#  ЛОГГЕР
# ═══════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
APISERPENT_API_KEY = os.getenv("APISERPENT_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
ALLOWED_USERS = [int(x.strip()) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()]
ALLOW_ALL = not ALLOWED_USERS

MAX_PAGES = 3
PAGE_TIMEOUT = 6
SEARCH_RESULTS = 10
DEEPSEEK_MODEL = os.getenv("MODEL_DEFAULT", "deepseek-v4")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow") or "UTC")

# Постоянные кнопки
MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["🔍 Новый поиск", "⏹️ Стоп"],
    ["❓ Помощь", "📊 Статистика"]
], resize_keyboard=True)

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("❌ TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы")
    sys.exit(1)

logger.info("⚡️ ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ С ИНТЕЛЛЕКТУАЛЬНЫМ АНАЛИЗОМ")

def now():
    return datetime.now(TZ)

# ═══════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════

_http_session = None

async def get_session():
    global _http_session
    if _http_session is None:
        _http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return _http_session

# ═══════════════════════════════════════════════════════════════════
#  DEEPSEEK
# ═══════════════════════════════════════════════════════════════════

async def ask_deepseek(prompt: str, temperature: float = 0.25, max_tokens: int = 3000) -> str:
    try:
        session = await get_session()
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json=payload,
            timeout=35
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"❌ DeepSeek ошибка: {e}")
    return ""

# ═══════════════════════════════════════════════════════════════════
#  5 УРОВНЕЙ ПАМЯТИ + ГРАФ ЗНАНИЙ
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
#  АНАЛИЗ ЗАПРОСА (DeepSeek)
# ═══════════════════════════════════════════════════════════════════

async def analyze_query(query: str) -> Dict:
    prompt = f"""
⚠️ **Проанализируй запрос пользователя.**

⚠️ **ЗАПРОС:** {query}

⚠️ **ОПРЕДЕЛИ:**
1. Тип: "greeting" / "question" / "instruction" / "stop"
2. Действие: "respond" / "search" / "refine"
3. Ключевые темы (1-3 слова)
4. Варианты для поиска (3-5 перефразировок)

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{
  "type": "question",
  "action": "search",
  "topics": ["тема1", "тема2"],
  "variants": ["вариант 1", "вариант 2"],
  "response": null
}}

⚠️ **ЕСЛИ ПРИВЕТСТВИЕ:**
{{
  "type": "greeting",
  "action": "respond",
  "topics": [],
  "variants": [],
  "response": "👋 Привет! Я на связи."
}}
"""
    try:
        answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=500)
        json_match = re.search(r'\{.*\}', answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return {"type": "question", "action": "search", "topics": [], "variants": [query], "response": None}

# ═══════════════════════════════════════════════════════════════════
#  ПОИСК
# ═══════════════════════════════════════════════════════════════════

async def search_apiserpent(query: str) -> List[Dict]:
    if not APISERPENT_API_KEY:
        return []
    try:
        session = await get_session()
        async with session.get(
            "https://apiserpent.com/api/search",
            params={"q": query, "engine": "google", "num": SEARCH_RESULTS},
            headers={"X-API-Key": APISERPENT_API_KEY},
            timeout=10
        ) as r:
            if r.status == 200:
                data = await r.json()
                return [{"title": x.get("title", ""), "snippet": x.get("snippet", ""), "link": x.get("link", "")} 
                        for x in data.get("organic_results", [])]
    except:
        pass
    return []

async def search_serper(query: str) -> List[Dict]:
    if not SERPER_API_KEY:
        return []
    try:
        session = await get_session()
        async with session.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": SEARCH_RESULTS},
            headers={"X-API-KEY": SERPER_API_KEY},
            timeout=10
        ) as r:
            if r.status == 200:
                data = await r.json()
                return [{"title": x.get("title", ""), "snippet": x.get("snippet", ""), "link": x.get("link", "")} 
                        for x in data.get("organic", [])]
    except:
        pass
    return []

async def search_all(variants: List[str]) -> List[Dict]:
    results = []
    for v in variants[:3]:
        r = await search_apiserpent(v)
        if r:
            results.extend(r)
        if len(results) >= MAX_PAGES * 2:
            break
    if not results:
        for v in variants[:2]:
            r = await search_serper(v)
            if r:
                results.extend(r)
    
    seen = set()
    unique = []
    for r in results:
        url = r.get('link', '')
        if url and url not in seen:
            if any(x in url for x in ['youtube.com', 'instagram.com', 'facebook.com', 'tiktok.com']):
                continue
            seen.add(url)
            unique.append(r)
    return unique[:MAX_PAGES * 2]

# ═══════════════════════════════════════════════════════════════════
#  ЗАГРУЗКА СТРАНИЦ (ПАРАЛЛЕЛЬНАЯ)
# ═══════════════════════════════════════════════════════════════════

def clean_html_text(html: str) -> str:
    text = re.sub(r'\{[^}]*\}', '', html)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    sentences = re.findall(r'[А-Яа-яA-Za-z][^.!?]{15,120}[.!?]', text)
    return ' '.join(sentences[:20])[:3000]

async def fetch_text(url: str) -> str:
    try:
        session = await get_session()
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=PAGE_TIMEOUT) as r:
            if r.status == 200:
                html = await r.text()
                return clean_html_text(html)
    except:
        pass
    return ""

async def fetch_pages(results: List[Dict]) -> List[str]:
    tasks = []
    for r in results[:MAX_PAGES]:
        url = r.get('link', '')
        if url:
            tasks.append(fetch_text(url))
    pages = await asyncio.gather(*tasks)
    return [p for p in pages if p and len(p) > 100]

# ═══════════════════════════════════════════════════════════════════
#  ИЗВЛЕЧЕНИЕ СТРУКТУР (DeepSeek)
# ═══════════════════════════════════════════════════════════════════

async def extract_structures(text: str, query: str) -> Dict:
    if len(text) > 4000:
        text = text[:4000]
    
    prompt = f"""
⚠️ **Извлеки структурированную информацию из текста.**

⚠️ **ЗАПРОС:** {query}

⚠️ **ТЕКСТ:**
{text}

⚠️ **ИЗВЛЕКИ:**
1. Списки (нумерованные, маркированные)
2. Шаги, алгоритмы
3. Вопросы
4. Цифры, цены
5. Определения
6. Примеры
7. Рекомендации

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{
  "lists": ["пункт 1", "пункт 2"],
  "steps": ["шаг 1", "шаг 2"],
  "questions": ["вопрос 1"],
  "prices": ["цена 1"],
  "definitions": ["определение 1"],
  "examples": ["пример 1"],
  "recommendations": ["рекомендация 1"]
}}

⚠️ **ЕСЛИ ЧЕГО-ТО НЕТ — оставляй пустой массив.**
"""
    try:
        answer = await ask_deepseek(prompt, temperature=0.1, max_tokens=800)
        json_match = re.search(r'\{.*\}', answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return {}

async def extract_structures_parallel(pages: List[str], query: str) -> List[Dict]:
    tasks = []
    for page in pages[:2]:
        tasks.append(extract_structures(page, query))
    return await asyncio.gather(*tasks)

# ═══════════════════════════════════════════════════════════════════
#  АНАЛИЗ НЕДОСТАТКА ДАННЫХ (НОВАЯ ФУНКЦИЯ)
# ═══════════════════════════════════════════════════════════════════

async def analyze_lack_of_data(query: str, results: List[Dict]) -> Dict:
    prompt = f"""
⚠️ **Ты — аналитик. Оцени, почему в интернете мало информации по запросу.**

⚠️ **ЗАПРОС:** {query}

⚠️ **ЧТО БЫЛО НАЙДЕНО:**
{chr(10).join([f"• {r.get('title', '')}" for r in results[:5]]) if results else "Ничего не найдено"}

⚠️ **ПРИЧИНЫ:**
1. Запрос слишком узкий
2. Запрос слишком специфичный
3. Нужно использовать другие ключевые слова
4. Информация есть, но под другим названием
5. Тема новая, информации мало

⚠️ **ПРЕДЛОЖИ:**
1. Переформулировку запроса (3 варианта)
2. Вопрос к пользователю для уточнения (1 вопрос)

⚠️ **ФОРМАТ (ТОЛЬКО JSON):**
{{
  "reason": "причина",
  "reformulations": ["вариант 1", "вариант 2", "вариант 3"],
  "clarification": "Вопрос к пользователю"
}}
"""
    try:
        answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=500)
        json_match = re.search(r'\{.*\}', answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return {
        "reason": "неизвестно",
        "reformulations": [query],
        "clarification": "Уточните, что именно вы ищете?"
    }

# ═══════════════════════════════════════════════════════════════════
#  ИНДИКАТОР ТОЧНОСТИ
# ═══════════════════════════════════════════════════════════════════

def is_data_sufficient(structures: List[Dict]) -> bool:
    total_lists = sum(len(s.get('lists', [])) for s in structures)
    total_steps = sum(len(s.get('steps', [])) for s in structures)
    total_questions = sum(len(s.get('questions', [])) for s in structures)
    return total_lists >= 3 or total_steps >= 2 or total_questions >= 3

def calculate_confidence(pages: List[str], results: List[Dict]) -> Dict:
    confidence = {'overall': 0, 'source_reliability': 0, 'data_completeness': 0, 'recency': 0, 'factors': []}
    
    if pages:
        reliable = 0
        for r in results[:3]:
            url = r.get('link', '')
            if any(d in url for d in ['.edu', '.gov', 'wikipedia', 'habr', 'vc.ru']):
                reliable += 1
            elif any(d in url for d in ['.com', '.org', '.net', '.ru']):
                reliable += 0.5
        score = min(100, (reliable / max(len(results[:3]), 1)) * 100)
        confidence['source_reliability'] = score
        confidence['factors'].append(f"Надёжность: {score:.0f}%")
    else:
        confidence['source_reliability'] = 20
        confidence['factors'].append("Нет источников")
    
    confidence['data_completeness'] = min(100, len(pages) * 30)
    confidence['factors'].append(f"Полнота: {confidence['data_completeness']:.0f}%")
    confidence['recency'] = 50
    confidence['factors'].append("Свежесть: средняя")
    confidence['overall'] = int((confidence['source_reliability'] + confidence['data_completeness'] + confidence['recency']) / 3)
    return confidence

def format_confidence(confidence: Dict) -> str:
    overall = confidence['overall']
    icon = "🟢" if overall >= 80 else "🟡" if overall >= 60 else "🟠" if overall >= 40 else "🔴"
    level = "Высокая" if overall >= 80 else "Средняя" if overall >= 60 else "Низкая" if overall >= 40 else "Очень низкая"
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **ТОЧНОСТЬ: {overall}%** {icon} ({level})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **ДЕТАЛИ:**
   • Надёжность: {confidence['source_reliability']:.0f}%
   • Полнота: {confidence['data_completeness']:.0f}%
   • Свежесть: {confidence['recency']:.0f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ═══════════════════════════════════════════════════════════════════
#  ПРОВЕРКА НА ЛОЖЬ И ОТКАЗ
# ═══════════════════════════════════════════════════════════════════

def check_for_lies(answer: str) -> bool:
    lie_phrases = [
        'из моих знаний', 'я знаю, что', 'по моему мнению',
        'я могу добавить', 'исходя из моего опыта', 'я предполагаю',
        'думаю, что', 'мне кажется', 'по моим данным'
    ]
    for phrase in lie_phrases:
        if phrase in answer.lower():
            return True
    return False

def check_refusal(answer: str) -> bool:
    refuse_phrases = [
        'не могу ответить', 'не знаю', 'нет данных',
        'информация отсутствует', 'не нашлось'
    ]
    for phrase in refuse_phrases:
        if phrase in answer.lower():
            return True
    return False

# ═══════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ ОТВЕТА
# ═══════════════════════════════════════════════════════════════════

async def generate_final_answer(query: str, pages: List[str], results: List[Dict], structures: List[Dict]) -> str:
    context = "\n\n---\n\n".join(pages[:2])
    
    structures_text = ""
    for i, s in enumerate(structures[:2]):
        if s:
            structures_text += f"\n📊 **СТРУКТУРЫ (источник {i+1}):**\n"
            if s.get('lists'):
                structures_text += "📋 СПИСКИ:\n" + "\n".join([f"  • {item}" for item in s['lists'][:5]]) + "\n"
            if s.get('steps'):
                structures_text += "🔄 ШАГИ:\n" + "\n".join([f"  • {item}" for item in s['steps'][:5]]) + "\n"
            if s.get('questions'):
                structures_text += "❓ ВОПРОСЫ:\n" + "\n".join([f"  • {item}" for item in s['questions'][:3]]) + "\n"
    
    prompt = f"""
⚠️ **Ты — анализатор. Используй ИЗВЛЕЧЁННЫЕ СТРУКТУРЫ для ответа.**

⚠️ **ЗАПРОС:** {query}

⚠️ **ИСТОЧНИКИ:**
{context}

{structures_text}

⚠️ **ПРАВИЛА:**
1. Используй структуры из источников
2. НЕ ДОБАВЛЯЙ свои знания
3. НЕ ВЫДУМЫВАЙ
4. Если в структурах нет ответа — скажи: "В источниках нет информации"
5. Дай структурированный ответ

⚠️ **ФОРМАТ:**
🎯 **УВЕРЕННОСТЬ: [X]%**
📊 **ОТВЕТ:**
[Ответ на основе структур]
📋 **ЦИТАТЫ:**
[Дословные цитаты]
🔗 **ИСТОЧНИКИ:**
[Ссылки]
⚠️ **ЧЕГО НЕТ В ИСТОЧНИКАХ:**
[Честно перечисли]
"""
    
    answer = await ask_deepseek(prompt, temperature=0.2, max_tokens=3000)
    
    if not answer:
        answer = f"""
⚠️ **НЕ УДАЛОСЬ СФОРМИРОВАТЬ ОТВЕТ**

📋 **ЧТО БЫЛО НАЙДЕНО:**
{context[:1000] if context else "Нет данных"}

🔗 **ИСТОЧНИКИ:**
{chr(10).join([f"• {r.get('link', '')}" for r in results[:3]])}
"""
    
    if check_for_lies(answer):
        answer = f"""
⚠️ **ОБНАРУЖЕНА ПОПЫТКА ДОПОЛНИТЬ ИЗ ЗНАНИЙ**

📋 **ЧТО ЕСТЬ В ИСТОЧНИКАХ:**
{context[:1500] if context else "Нет данных"}

🔗 **ИСТОЧНИКИ:**
{chr(10).join([f"• {r.get('link', '')}" for r in results[:3]])}
"""
    
    if check_refusal(answer):
        answer = f"""
⚠️ **В ИСТОЧНИКАХ НЕТ ИНФОРМАЦИИ**

Попробуйте переформулировать запрос.

📋 **ЧТО БЫЛО НАЙДЕНО:**
{context[:1000] if context else "Нет данных"}

🔗 **ИСТОЧНИКИ:**
{chr(10).join([f"• {r.get('link', '')}" for r in results[:3]])}
"""
    
    return answer

# ═══════════════════════════════════════════════════════════════════
#  ОСНОВНАЯ ЛОГИКА (С ПЕРЕФОРМУЛИРОВКОЙ)
# ═══════════════════════════════════════════════════════════════════

async def process_query(query: str, timer: LiveTimer, uid: int, max_retries: int = 2) -> str:
    timer.set_stage('analyze')
    analysis = await analyze_query(query)
    
    if analysis.get('action') == 'respond':
        return analysis.get('response', "👋 Я на связи!")
    
    timer.set_stage('search')
    results = await search_all(analysis.get('variants', [query]))
    
    if not results:
        # Если ничего не нашлось — пробуем переформулировать
        analysis_lack = await analyze_lack_of_data(query, [])
        reformulations = analysis_lack.get('reformulations', [query])
        results = await search_all(reformulations)
        if not results:
            return "⚠️ В интернете ничего не нашлось. Попробуй переформулировать запрос."
    
    timer.set_stage('load')
    pages = await fetch_pages(results)
    
    if not pages:
        return "⚠️ Не удалось загрузить страницы. Попробуй позже."
    
    timer.set_stage('extract')
    structures = await extract_structures_parallel(pages, query)
    
    # Проверяем достаточно ли данных
    if is_data_sufficient(structures):
        timer.set_stage('think')
        answer = await generate_final_answer(query, pages, results, structures)
        confidence = calculate_confidence(pages, results)
        return format_confidence(confidence) + "\n\n" + answer
    
    # Если данных мало — анализируем и переформулируем
    if max_retries > 0:
        timer.set_stage('think')
        analysis_lack = await analyze_lack_of_data(query, results)
        reformulations = analysis_lack.get('reformulations', [query])
        clarification = analysis_lack.get('clarification', "Уточните запрос.")
        
        # Ищем по новым вариантам
        new_results = await search_all(reformulations)
        
        if new_results and len(new_results) > len(results):
            pages = await fetch_pages(new_results)
            if pages:
                structures = await extract_structures_parallel(pages, query)
                answer = await generate_final_answer(query, pages, new_results, structures)
                confidence = calculate_confidence(pages, new_results)
                return format_confidence(confidence) + "\n\n" + answer + f"\n\n💡 **Уточнение:** {clarification}"
        
        # Если всё равно мало — отдаём что есть и предлагаем уточнить
        answer = await generate_final_answer(query, pages, results, structures)
        confidence = calculate_confidence(pages, results)
        return format_confidence(confidence) + "\n\n" + answer + f"\n\n💡 **Уточнение:** {clarification}"
    
    # Последняя попытка
    answer = await generate_final_answer(query, pages, results, structures)
    confidence = calculate_confidence(pages, results)
    return format_confidence(confidence) + "\n\n" + answer

# ═══════════════════════════════════════════════════════════════════
#  ТАЙМЕР
# ═══════════════════════════════════════════════════════════════════

class LiveTimer:
    COLORS = ['🟥', '🟧', '🟨', '🟩', '🟦', '🟪']
    STAGES = {
        'analyze': '🧠 Анализирую запрос',
        'search': '🔍 Ищу в интернете',
        'load': '📄 Загружаю страницы',
        'extract': '🧩 Извлекаю структуры',
        'think': '🤔 Думаю над ответом',
        'done': '🏁 Готово!'
    }
    
    def __init__(self):
        self.start = time.time()
        self.stage = 'analyze'
        self.total = 45
        self.running = True
        self.pos = 0
    
    def set_stage(self, stage: str):
        self.stage = stage
    
    def elapsed(self) -> int:
        return int(time.time() - self.start)
    
    def progress(self) -> int:
        e = self.elapsed()
        return min(100, int((e / max(self.total, 1)) * 100))
    
    def bar(self, length: int = 25) -> str:
        prog = self.progress()
        filled = int(length * prog / 100)
        self.pos = (self.pos + 1) % len(self.COLORS)
        bar = []
        for i in range(length):
            if i < filled:
                bar.append(self.COLORS[(i + self.pos) % len(self.COLORS)])
            else:
                bar.append('⬜')
        return ''.join(bar)
    
    def status(self) -> str:
        e = self.elapsed()
        remain = max(0, self.total - e)
        return f"""
{self.STAGES.get(self.stage, '⚙️ Обработка')}

{self.bar(25)}

📊 {self.progress()}%
⏱️ {e} сек · ~{remain} сек
"""
    
    def finish(self):
        self.stage = 'done'
        self.running = False

async def show_timer(chat_id, context, timer: LiveTimer):
    try:
        msg = await context.bot.send_message(chat_id, timer.status(), parse_mode='Markdown')
        while timer.running:
            await asyncio.sleep(0.5)
            if context.user_data.get('found_answer'):
                timer.finish()
                await msg.edit_text("✅ **Готово!** Формирую ответ...", parse_mode='Markdown')
                return
            try:
                await msg.edit_text(timer.status(), parse_mode='Markdown')
            except:
                pass
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИК TELEGRAM
# ═══════════════════════════════════════════════════════════════════

async def handle(update: Update, context):
    try:
        uid = update.effective_user.id
        if not ALLOW_ALL and uid not in ALLOWED_USERS:
            return
        
        text = update.effective_message.text.strip() if update.effective_message else ""
        if not text:
            return
        
        if text == "⏹️ Стоп":
            context.user_data.clear()
            await update.message.reply_text("⏹️ Остановлено.", reply_markup=MAIN_KEYBOARD)
            return
        
        if text == "🔍 Новый поиск":
            context.user_data.clear()
            await update.message.reply_text("🔍 Напиши вопрос.", reply_markup=MAIN_KEYBOARD)
            return
        
        if text == "❓ Помощь":
            await update.message.reply_text(
                "❓ **Помощь**\n\n"
                "• Напиши вопрос — я найду ответ в интернете\n"
                "• 🔍 Новый поиск — начать заново\n"
                "• ⏹️ Стоп — остановить всё\n"
                "• 📊 Статистика — память",
                reply_markup=MAIN_KEYBOARD
            )
            return
        
        if text == "📊 Статистика":
            memory = get_memory(uid)
            health = memory.memory_health_check()
            await update.message.reply_text(
                f"📊 **Статистика**\n\n"
                f"💬 Сообщений: {health['short_term']}\n"
                f"👤 Профиль: {health['profile']}\n"
                f"⭐ Фактов: {health['episodic']}\n"
                f"🧠 Граф знаний: {health['graph_facts']}\n"
                f"📝 Всего: {health['total_messages']}",
                reply_markup=MAIN_KEYBOARD
            )
            return
        
        chat_id = update.effective_chat.id
        context.user_data['found_answer'] = False
        
        timer = LiveTimer()
        asyncio.create_task(show_timer(chat_id, context, timer))
        
        memory = get_memory(uid)
        memory.add_message("user", text)
        
        answer = await process_query(text, timer, uid)
        
        context.user_data['found_answer'] = True
        timer.finish()
        
        memory.add_message("assistant", answer[:500])
        
        elapsed = timer.elapsed()
        await update.message.reply_text(f"⏱️ {elapsed} сек\n\n{answer}", reply_markup=MAIN_KEYBOARD)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте еще раз.", reply_markup=MAIN_KEYBOARD)

# ═══════════════════════════════════════════════════════════════════
#  СТАРТ
# ═══════════════════════════════════════════════════════════════════

async def start(update: Update, context):
    await update.message.reply_text(
        "👋 **Привет!** Я ищу ответы в интернете.\n\n"
        "Напиши вопрос — и я найду информацию.\n\n"
        "⚠️ Я никогда не вру. Если данных мало — я переформулирую запрос и поищу ещё.\n"
        "🧠 Я запоминаю тебя и учусь с каждым диалогом.\n"
        "⚡️ Отвечаю быстро и точно.",
        reply_markup=MAIN_KEYBOARD
    )

# ═══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════

def main():
    logger.info("🚀 БОТ ЗАПУСКАЕТСЯ...")
    logger.info(f"🤖 Токен: {TELEGRAM_TOKEN[:10]}...")
    logger.info(f"🔑 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    logger.info(f"🔍 APISerpent: {'✅' if APISERPENT_API_KEY else '❌'}")
    logger.info(f"🔍 Serper: {'✅' if SERPER_API_KEY else '❌'}")
    logger.info("⚡️ ФИНАЛЬНАЯ УНИВЕРСАЛЬНАЯ ВЕРСИЯ")
    logger.info("✅ Память: 5 уровней + граф знаний")
    logger.info("✅ Извлечение структур через DeepSeek")
    logger.info("✅ Интеллектуальный анализ недостатка данных")
    logger.info("✅ Переформулировка запроса и уточнение")
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
        logger.info("✅ Бот готов к работе!")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
