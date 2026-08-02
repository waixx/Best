# ===================================================================
#  BroWaix Bot — УНИВЕРСАЛЬНАЯ ВЕРСИЯ
#  DeepSeek оценивает релевантность + Таймер с обновлением
# ===================================================================

# ==================== НОВЫЙ ТАЙМЕР С ОБНОВЛЕНИЕМ ====================

async def send_progress_updates(chat_id, context, start_time):
    """Отправляет обновления таймера каждые 3 секунды"""
    message = None
    try:
        # Первое сообщение
        message = await context.bot.send_message(
            chat_id,
            "⏳ Ищу информацию...\n\n⏱️ 0 сек"
        )
        
        elapsed = 0
        while elapsed < 60:  # Максимум 60 секунд
            await asyncio.sleep(3)
            elapsed = int(time.time() - start_time)
            
            # Обновляем сообщение каждые 3 секунды
            try:
                await message.edit_text(
                    f"⏳ Ищу информацию...\n\n⏱️ {elapsed} сек\n"
                    f"{'█' * min(elapsed, 20)}"  # Визуальный прогресс
                )
            except Exception:
                pass
            
            # Если уже нашли ответ - выходим
            if context.user_data.get('found_answer'):
                break
    
    except Exception as e:
        logger.error(f"❌ Ошибка таймера: {e}")
    
    return message

# ==================== УНИВЕРСАЛЬНАЯ ОЦЕНКА ЧЕРЕЗ DEEPSEEK ====================

async def evaluate_relevance_with_ai(query: str, results: list, max_results: int = 10) -> list:
    """Универсальная оценка релевантности через DeepSeek"""
    if not results:
        return []
    
    # Формируем список результатов для оценки
    results_text = ""
    for i, res in enumerate(results[:20], 1):
        results_text += f"""
{i}. Заголовок: {res.get('title', '')}
   Описание: {res.get('snippet', '')[:200]}
   Ссылка: {res.get('link', '')}
"""
    
    system_prompt = f"""
Ты — AI-оценщик релевантности. Оцени каждый результат по запросу.

Запрос пользователя: {query}

⚠️ ТВОЯ ЗАДАЧА:
1. Оцени каждый результат от 0 до 100
2. Учитывай:
   - Соответствие запросу (40%)
   - Полезность информации (30%)
   - Актуальность (дата) (20%)
   - Надежность источника (10%)

⚠️ ФОРМАТ ОТВЕТА (ТОЛЬКО JSON):
{{
    "оценки": [
        {{"номер": 1, "оценка": 85, "причина": "полное соответствие"}},
        {{"номер": 2, "оценка": 30, "причина": "частичное соответствие"}}
    ],
    "лучшие": [1, 3, 5]
}}

Результаты для оценки:
{results_text}
"""
    
    messages = [{"role": "system", "content": system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.0, max_tokens=1500)
    
    if err or not answer:
        # Fallback на простую оценку
        return results[:max_results]
    
    try:
        # Парсим JSON
        import json
        data = json.loads(answer)
        evaluations = {item['номер']: item['оценка'] for item in data.get('оценки', [])}
        best_indices = data.get('лучшие', [])
        
        # Сортируем по оценке
        scored_results = []
        for i, res in enumerate(results, 1):
            score = evaluations.get(i, 0)
            if score > 0:
                scored_results.append({**res, 'ai_score': score})
        
        # Сортируем по убыванию
        scored_results.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
        
        # Возвращаем лучшие
        return scored_results[:max_results]
    
    except Exception as e:
        logger.warning(f"⚠️ Ошибка парсинга AI оценки: {e}")
        return results[:max_results]

# ==================== УМНЫЙ АНАЛИЗ ТЕМЫ ====================

async def analyze_topic_with_ai(query: str) -> dict:
    """Анализирует тему запроса через AI"""
    system_prompt = f"""
Проанализируй запрос и определи:

Запрос: {query}

Ответь в формате JSON:
{{
    "topic": "тема (например: movies, tech, science, finance, general)",
    "intent": "намерение (например: list, howto, info, news, review)",
    "keywords": ["ключевые", "слова"],
    "time_sensitive": true/false,
    "needs_list": true/false
}}
"""
    
    messages = [{"role": "system", "content": system_prompt}]
    answer, err = await ask_deepseek(messages, temperature=0.0, max_tokens=500)
    
    if err or not answer:
        return {
            "topic": "general",
            "intent": "info",
            "keywords": [],
            "time_sensitive": True,
            "needs_list": False
        }
    
    try:
        import json
        return json.loads(answer)
    except:
        return {
            "topic": "general",
            "intent": "info",
            "keywords": [],
            "time_sensitive": True,
            "needs_list": False
        }

# ==================== ИЗВЛЕЧЕНИЕ СПИСКОВ ====================

def extract_list_from_text(text: str) -> list:
    """Универсальное извлечение списков из текста"""
    items = []
    
    # Нумерованные списки
    numbered = re.findall(r'(?m)^\s*(\d+)[\.\)]\s*(.+)$', text, re.MULTILINE)
    for num, item in numbered:
        items.append(item.strip())
    
    # Маркированные списки
    bulleted = re.findall(r'(?m)^\s*[•\-*]\s*(.+)$', text, re.MULTILINE)
    items.extend([item.strip() for item in bulleted])
    
    # Списки в кавычках
    quoted = re.findall(r'["«»]([^"«»]{3,50})["«»]', text)
    for q in quoted[:10]:
        if len(q) > 3:
            items.append(q.strip())
    
    # Убираем дубликаты
    seen = set()
    unique = []
    for item in items:
        if item not in seen and len(item) > 3:
            seen.add(item)
            unique.append(item)
    
    return unique[:30]

# ==================== ОБНОВЛЕННАЯ search_and_answer ====================

async def search_and_answer(uid, user_message, history, context):
    """Обновленная функция поиска с AI оценкой и таймером"""
    
    chat_id = context.user_data.get('chat_id')
    start_time = time.time()
    context.user_data['found_answer'] = False
    
    # Запускаем таймер в фоне
    timer_task = asyncio.create_task(
        send_progress_updates(chat_id, context, start_time)
    )
    
    try:
        # 1. Анализ темы через AI
        analysis = await analyze_topic_with_ai(user_message)
        logger.info(f"🎯 Анализ: {analysis}")
        
        # 2. Генерация запросов
        variants = generate_smart_queries(user_message, analysis)
        
        # 3. Поиск
        all_results = []
        for variant in variants[:3]:
            results = await search_primary(variant)
            if results:
                all_results.extend(results)
                if len(all_results) >= 30:
                    break
        
        if not all_results:
            context.user_data['found_answer'] = True
            await timer_task
            return "❌ В интернете ничего не найдено. Попробуйте переформулировать запрос."
        
        # 4. AI оценка релевантности
        scored = await evaluate_relevance_with_ai(user_message, all_results, max_results=15)
        
        if not scored:
            context.user_data['found_answer'] = True
            await timer_task
            return "❌ Не найдено релевантных источников."
        
        # 5. Загрузка страниц (только лучшие)
        links = [r['link'] for r in scored[:10]]
        pages = await fetch_multiple_pages_optimized(links, max_pages=8)
        
        # 6. Извлечение списков если нужно
        found_items = []
        if analysis.get('needs_list', False):
            for page in pages:
                items = extract_list_from_text(page['text'])
                if items:
                    found_items.extend(items)
            found_items = list(dict.fromkeys(found_items))[:30]
        
        # 7. Формирование ответа через AI
        answer = await generate_smart_answer(
            user_message, 
            pages, 
            scored,
            analysis,
            found_items,
            history
        )
        
        # 8. Таймер финиш
        context.user_data['found_answer'] = True
        elapsed = int(time.time() - start_time)
        
        # Ждем завершения таймера
        await timer_task
        
        # Добавляем финальное время
        answer = f"⏱️ {elapsed} сек\n\n{answer}"
        
        # Проверка на предположения
        speculation = ['возможно', 'вероятно', 'скорее всего', 'должно быть']
        if any(p in answer.lower() for p in speculation):
            answer = f"⚠️ [НЕ 100%]\n\n{answer}"
        
        # Сохраняем в память
        memory = get_memory(uid)
        memory.add_message('assistant', answer[:500])
        
        return answer
    
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        context.user_data['found_answer'] = True
        await timer_task
        return f"⚠️ Ошибка: {str(e)[:100]}"

# ==================== ГЕНЕРАЦИЯ SMART-ЗАПРОСОВ ====================

def generate_smart_queries(query: str, analysis: dict) -> list:
    """Генерирует умные запросы на основе анализа"""
    variants = [query]
    
    # Добавляем ключевые слова
    keywords = analysis.get('keywords', [])
    for kw in keywords[:3]:
        variants.append(f"{kw} {query}")
    
    # Добавляем контекст по намерению
    intent = analysis.get('intent', 'info')
    intent_map = {
        'list': ['список', 'все', 'полный'],
        'howto': ['инструкция', 'руководство', 'как'],
        'review': ['обзор', 'отзыв', 'рейтинг'],
        'news': ['новости', 'последние', 'свежие'],
        'info': ['что такое', 'описание', 'обзор'],
    }
    
    for word in intent_map.get(intent, [])[:2]:
        variants.append(f"{word} {query}")
    
    # Добавляем год для актуальности
    if analysis.get('time_sensitive', True):
        variants.append(f"{query} {now().year}")
        variants.append(f"{query} актуальное")
    
    # Убираем дубликаты
    seen = set()
    unique = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    
    return unique[:10]

# ==================== ГЕНЕРАЦИЯ SMART-ОТВЕТА ====================

async def generate_smart_answer(query: str, pages: list, scored: list, 
                                analysis: dict, found_items: list, history: list) -> str:
    """Генерирует умный ответ на основе анализа"""
    
    # Строим текст источников
    source_text = ""
    for i, p in enumerate(pages[:8], 1):
        source_text += f"""
--- ИСТОЧНИК {i} ---
URL: {p['url']}
Дата: {p.get('date', 'дата не указана')}
Содержание: {p['text'][:1000]}
"""
    
    # Строим информацию о найденных элементах
    items_text = ""
    if found_items:
        items_text = "\n📋 Найденные элементы:\n" + "\n".join([f"• {item}" for item in found_items[:20]])
    
    system_prompt = f"""
Ты — универсальный аналитик. Проанализируй источники и ответь на запрос.

ЗАПРОС: {query}
ТЕМА: {analysis.get('topic', 'general')}
НАМЕРЕНИЕ: {analysis.get('intent', 'info')}

⚠️ ТВОЯ ЗАДАЧА:
1. Проанализируй ВСЕ источники
2. Выдели ключевую информацию
3. Найди общие факты и противоречия
4. Дай четкий вывод

⚠️ ФОРМАТ ОТВЕТА:
📊 **Использованные источники:** (перечисли все с кратким содержанием)
📊 **Общие факты:**
⚠️ **Противоречия:** (если есть)
✅ **Вывод:**
{items_text}

ДАННЫЕ:
{source_text}
"""
    
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": query}]
    answer, err = await ask_deepseek(messages, temperature=0.3, max_tokens=MAX_TOKENS_ANSWER)
    
    if err or not answer or not re.search(r'Источник \d+', answer):
        # Простой ответ
        simple = "🔍 Результаты поиска:\n\n"
        for i, r in enumerate(scored[:10], 1):
            simple += f"{i}. {r.get('title', 'Без названия')}\n"
            simple += f"   {r.get('snippet', '')[:200]}\n"
            simple += f"   🔗 {r.get('link', '')}\n\n"
        simple += f"📅 {get_current_date()}"
        
        if found_items:
            simple += f"\n\n📋 Найдено элементов: {len(found_items)}"
            for item in found_items[:10]:
                simple += f"\n• {item}"
        
        return simple
    
    return answer

# ==================== ОПТИМИЗИРОВАННАЯ ЗАГРУЗКА ====================

async def fetch_multiple_pages_optimized(links, max_pages=10):
    """Оптимизированная загрузка с дедупликацией"""
    if not links:
        return []
    
    # Убираем дубликаты
    seen = set()
    unique_links = []
    for link in links:
        clean = link.split('?')[0]
        if clean not in seen:
            seen.add(clean)
            unique_links.append(link)
    
    # Приоритет для полезных сайтов
    priority_sites = ['wikipedia', 'habr', 'vc.ru', 'kinopoisk', 'imdb', 'film.ru']
    sorted_links = sorted(
        unique_links,
        key=lambda x: 10 if any(site in x for site in priority_sites) else 0,
        reverse=True
    )
    
    semaphore = asyncio.Semaphore(5)
    results = []
    
    async def fetch_one(url):
        async with semaphore:
            timeout = 20 if any(site in url for site in priority_sites) else 10
            text, date = await fetch_content(url, timeout=timeout)
            if text and len(text) > 50:
                return {"url": url, "text": text, "date": date}
            return None
    
    tasks = [fetch_one(url) for url in sorted_links[:max_pages]]
    fetched = await asyncio.gather(*tasks)
    
    return [r for r in fetched if r is not None]

# ==================== ОБНОВЛЕННЫЙ ОБРАБОТЧИК ====================

async def handle_message(update, context):
    """Обновленный обработчик с таймером"""
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
        chat_id = update.effective_chat.id
        
        memory = get_memory(uid)
        history = memory.get_context(limit=10)
        
        context.user_data['uid'] = uid
        context.user_data['history'] = history
        context.user_data['start_time'] = time.time()
        context.user_data['query'] = user_message
        context.user_data['chat_id'] = chat_id
        context.user_data['found_answer'] = False
        
        # Запускаем поиск
        answer = await search_and_answer(uid, user_message, history, context)
        
        # Сохраняем для уточнений
        context.user_data['last_answer'] = answer
        context.user_data['awaiting_followup'] = True
        
        await safe_reply(update, answer, reply_markup=get_after_answer_keyboard())
    
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await safe_reply(update, "⚠️ Ошибка. Попробуйте еще раз.")
