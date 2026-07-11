print("🚀 [0] bot.py загружается...", flush=True)
import logging
import csv
import io
import aiohttp
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ConversationHandler, ContextTypes
)
from config import BOT_TOKEN
from database import Database

# --- Состояния ---
print("🚀 [0.1] Импорты выполнены, создаю объект Database...", flush=True)
(
    SELECT_MEAL, ENTER_PRODUCT, ENTER_WEIGHT, 
    MANUAL_ENTRY, SELECT_PRODUCT_FROM_LIST
) = range(5)

db = Database()
print("🚀 [0.2] Объект Database создан.", flush=True)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
print("🚀 [0.3] Логирование настроено.", flush=True)
print("🚀 [0.4] Начинаю определять функции...", flush=True)
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def format_nutrition(name: str, weight: float, calories: float, 
                     protein: float, fat: float, carbs: float) -> str:
    return (
        f"✅ Добавлено: {name} ({weight} г)\n"
        f"🔥 {calories:.1f} ккал\n"
        f"🥩 {protein:.1f} г белков\n"
        f"🧈 {fat:.1f} г жиров\n"
        f"🍞 {carbs:.1f} г углеводов"
    )

def format_history(entries):
    if not entries:
        return "📭 За этот день записей нет."
    
    result = ""
    current_meal = None
    meal_total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
    grand_total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
    
    for entry in entries:
        if entry['meal_type'] != current_meal:
            if current_meal:
                result += (
                    f"   Итого: {meal_total['calories']:.0f} ккал, "
                    f"{meal_total['protein']:.1f}г б, "
                    f"{meal_total['fat']:.1f}г ж, "
                    f"{meal_total['carbs']:.1f}г у\n\n"
                )
            current_meal = entry['meal_type']
            result += f"🥣 {current_meal}:\n"
            meal_total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
        
        result += (
            f"   {entry['product_name']} ({entry['weight_grams']} г) — "
            f"{entry['calories']:.0f} ккал\n"
        )
        meal_total['calories'] += entry['calories']
        meal_total['protein'] += entry['protein']
        meal_total['fat'] += entry['fat']
        meal_total['carbs'] += entry['carbs']
        grand_total['calories'] += entry['calories']
        grand_total['protein'] += entry['protein']
        grand_total['fat'] += entry['fat']
        grand_total['carbs'] += entry['carbs']
    
    if current_meal:
        result += (
            f"   Итого: {meal_total['calories']:.0f} ккал, "
            f"{meal_total['protein']:.1f}г б, "
            f"{meal_total['fat']:.1f}г ж, "
            f"{meal_total['carbs']:.1f}г у\n\n"
        )
    
    result += (
        f"📊 Всего за день: {grand_total['calories']:.0f} ккал | "
        f"Б: {grand_total['protein']:.1f}г | "
        f"Ж: {grand_total['fat']:.1f}г | "
        f"У: {grand_total['carbs']:.1f}г"
    )
    
    return result

# --- КНОПКИ И МЕНЮ ---

async def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить продукт", callback_data="menu_add")],
        [InlineKeyboardButton("📊 История за сегодня", callback_data="menu_history")],
        [InlineKeyboardButton("📈 Статистика за неделю", callback_data="menu_week")],
        [InlineKeyboardButton("📁 Экспорт CSV", callback_data="menu_export")],
        [InlineKeyboardButton("❌ Отменить действие", callback_data="menu_cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = await main_menu_keyboard()
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("📋 Главное меню:", reply_markup=keyboard)
    else:
        await update.message.reply_text("📋 Главное меню:", reply_markup=keyboard)

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action = query.data
    
    # menu_back обрабатывается в ConversationHandler — игнорируем здесь
    if action == "menu_back":
        return
    
    if action == "menu_add":
        await add_start(update, context)
    elif action == "menu_history":
        await history(update, context)
    elif action == "menu_week":
        await week(update, context)
    elif action == "menu_export":
        await export_csv(update, context)
    elif action == "menu_cancel":
        await cancel(update, context)

# --- КОМАНДЫ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.register_user(user.id, user.username or "без username")
    await show_main_menu(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("❌ Действие отменено.")
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("❌ Действие отменено.")
        await show_main_menu(update, context)
    return ConversationHandler.END

# --- ПОИСК В ВКУСВИЛЛ ---

async def search_vkusvill_by_barcode(barcode: str):
    """Ищет продукт по штрих-коду через MCP-сервер ВкусВилл."""
    print(f"🔍 ПОИСК В ВКУСВИЛЛ ПО ШТРИХ-КОДУ: {barcode}")
    
    try:
        search_url = "https://mcp001.vkusvill.ru/mcp"
        
        # Пробуем другой формат JSON-RPC
        payload = {
            "jsonrpc": "2.0",
            "method": "vkusvill_products_search",
            "params": [barcode],
            "id": 1
        }
        
        print(f"📤 Отправка запроса к ВкусВилл: {search_url}")
        print(f"📤 Payload: {payload}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                search_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Nutricheckbot/1.0"
                }
            ) as response:
                print(f"📥 Статус ответа ВкусВилл: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    print(f"📥 Ответ ВкусВилл: {data}")
                    products = data.get('result', [])
                    
                    if products and len(products) > 0:
                        print(f"📦 Найдено продуктов: {len(products)}")
                        product = products[0]
                        product_id = product.get('id')
                        
                        if product_id:
                            detail_payload = {
                                "jsonrpc": "2.0",
                                "method": "vkusvill_product_details",
                                "params": [product_id],
                                "id": 2
                            }
                            
                            async with session.post(search_url, json=detail_payload, headers={"Content-Type": "application/json"}) as detail_response:
                                if detail_response.status == 200:
                                    detail_data = await detail_response.json()
                                    product_detail = detail_data.get('result', {})
                                    
                                    attributes = product_detail.get('attributes', {})
                                    nutriments = {
                                        'calories': attributes.get('calories') or attributes.get('energy_value') or 0,
                                        'protein': attributes.get('protein') or 0,
                                        'fat': attributes.get('fat') or 0,
                                        'carbs': attributes.get('carbohydrates') or 0
                                    }
                                    
                                    return [{
                                        'id': str(product_id),
                                        'name': product_detail.get('name', 'Без названия'),
                                        'calories': float(nutriments['calories']),
                                        'protein': float(nutriments['protein']),
                                        'fat': float(nutriments['fat']),
                                        'carbs': float(nutriments['carbs']),
                                        'barcode': barcode,
                                        'source': 'vkusvill'
                                    }]
                    else:
                        print(f"❌ Продукты не найдены")
                        return []
                else:
                    print(f"❌ Ошибка HTTP ВкусВилл: {response.status}")
                    # Пробуем прочитать тело ошибки
                    try:
                        error_body = await response.text()
                        print(f"❌ Тело ошибки: {error_body}")
                    except:
                        pass
                    return []
    except Exception as e:
        print(f"❌ Ошибка при поиске во ВкусВилл: {e}")
        import traceback
        traceback.print_exc()
        return []

# --- НАЧАЛО ФУНКЦИИ: search_vkusvill_by_name ---
async def search_vkusvill_by_name(product_name: str):
    """Ищет продукты по названию через MCP-сервер ВкусВилл.
    Возвращает список продуктов с id, названием и КБЖУ."""
    print(f"🔍 ПОИСК В ВКУСВИЛЛ ПО НАЗВАНИЮ: {product_name}")
    
    try:
        search_url = "https://mcp001.vkusvill.ru/mcp"
        
        # 1. Ищем продукты по названию (согласно Tool Definition)
        search_payload = {
            "jsonrpc": "2.0",
            "method": "vkusvill_products_search",
            "params": {
                "q": product_name,
                "page": 1,
                "sort": "popularity",
                "vvonly": 1
            },
            "id": 1
        }
        
        print(f"📤 Поисковый запрос: {search_payload}")
        
        results = []
        import re
        import json
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                search_url,
                json=search_payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
            ) as response:
                print(f"📥 Статус ответа: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    print(f"📥 Ответ API: {data}")
                    
                    # Проверяем наличие ошибки в стандартном JSON-RPC формате
                    if 'error' in data:
                        print(f"❌ Ошибка API: {data['error']}")
                        return []
                    
                    # Извлекаем items из структуры ответа (как в MCP Inspector)
                    # Ответ может быть в формате {'content': [{'type': 'text', 'text': '...'}]}
                    items = []
                    
                    if 'result' in data:
                        # Стандартный JSON-RPC формат
                        result_data = data.get('result', {})
                        items = result_data.get('items', [])
                    elif 'content' in data:
                        # Формат MCP Inspector
                        for content_item in data.get('content', []):
                            if content_item.get('type') == 'text':
                                try:
                                    # Парсим JSON внутри text
                                    inner_data = json.loads(content_item.get('text', '{}'))
                                    if inner_data.get('ok'):
                                        items = inner_data.get('data', {}).get('items', [])
                                        break
                                except json.JSONDecodeError:
                                    print(f"❌ Ошибка парсинга JSON из content")
                                    continue
                    
                    if not items:
                        print(f"❌ Продукты не найдены")
                        return []
                    
                    print(f"📦 Найдено продуктов: {len(items)}")
                    
                    # 2. Для каждого продукта получаем детали
                    for idx, item in enumerate(items[:5]):
                        product_id = item.get('id')
                        if not product_id:
                            continue
                        
                        # Запрашиваем детали продукта
                        detail_payload = {
                            "jsonrpc": "2.0",
                            "method": "vkusvill_product_details",
                            "params": {
                                "id": product_id
                            },
                            "id": idx + 2
                        }
                        
                        print(f"📤 Запрос деталей для ID {product_id}")
                        
                        async with session.post(
                            search_url,
                            json=detail_payload,
                            headers={
                                "Content-Type": "application/json",
                                "Accept": "application/json"
                            }
                        ) as detail_response:
                            if detail_response.status == 200:
                                detail_data = await detail_response.json()
                                
                                # Аналогично обрабатываем ответ для деталей
                                product_detail = {}
                                if 'result' in detail_data:
                                    product_detail = detail_data.get('result', {})
                                elif 'content' in detail_data:
                                    for content_item in detail_data.get('content', []):
                                        if content_item.get('type') == 'text':
                                            try:
                                                inner_data = json.loads(content_item.get('text', '{}'))
                                                if inner_data.get('ok'):
                                                    product_detail = inner_data.get('data', {})
                                                    break
                                            except json.JSONDecodeError:
                                                continue
                                
                                # Извлекаем КБЖУ из properties
                                calories = 0
                                protein = 0
                                fat = 0
                                carbs = 0
                                
                                properties = product_detail.get('properties', [])
                                for prop in properties:
                                    if prop.get('name') == "Пищевая и энергетическая ценность в 100 г":
                                        nutrition_text = prop.get('value', '')
                                        print(f"📊 Найден текст с КБЖУ: {nutrition_text[:100]}...")
                                        
                                        kcal_match = re.search(r'(\d+[.,]?\d*)\s*ккал', nutrition_text)
                                        if kcal_match:
                                            calories = float(kcal_match.group(1).replace(',', '.'))
                                        
                                        protein_match = re.search(r'белки\s*(\d+[.,]?\d*)', nutrition_text, re.IGNORECASE)
                                        if protein_match:
                                            protein = float(protein_match.group(1).replace(',', '.'))
                                        
                                        fat_match = re.search(r'жиры\s*(\d+[.,]?\d*)', nutrition_text, re.IGNORECASE)
                                        if fat_match:
                                            fat = float(fat_match.group(1).replace(',', '.'))
                                        
                                        carbs_match = re.search(r'углеводы\s*(\d+[.,]?\d*)', nutrition_text, re.IGNORECASE)
                                        if carbs_match:
                                            carbs = float(carbs_match.group(1).replace(',', '.'))
                                        
                                        print(f"✅ Распарсено КБЖУ: {calories} ккал, {protein}г б, {fat}г ж, {carbs}г у")
                                        break
                                
                                results.append({
                                    'id': str(product_id),
                                    'name': item.get('name', 'Без названия'),
                                    'calories': calories,
                                    'protein': protein,
                                    'fat': fat,
                                    'carbs': carbs,
                                    'barcode': '',
                                    'source': 'vkusvill'
                                })
                            else:
                                print(f"❌ Ошибка при получении деталей для ID {product_id}: {detail_response.status}")
                    
                    return results
                else:
                    try:
                        error_body = await response.text()
                        print(f"❌ Тело ошибки: {error_body}")
                    except:
                        pass
                    return []
    except Exception as e:
        print(f"❌ Ошибка при поиске во ВкусВилл: {e}")
        import traceback
        traceback.print_exc()
        return []
# --- КОНЕЦ ФУНКЦИИ: search_vkusvill_by_name ---


# --- НАЧАЛО ФУНКЦИИ: search_product_by_barcode_with_suggestion ---
async def search_product_by_barcode_with_suggestion(update: Update, context: ContextTypes.DEFAULT_TYPE, barcode: str):
    """Поиск по штрих-коду с предложением поискать во ВкусВилл."""
    print(f"🔍 ПОИСК ПО ШТРИХ-КОДУ С ПРЕДЛОЖЕНИЕМ: {barcode}")
    
    # Сохраняем штрих-код для повторного поиска
    context.user_data['current_barcode'] = barcode
    
    # 1. Ищем в локальной базе
    product = await db.find_product_by_barcode(barcode)
    if product:
        print(f"✅ Найден в локальной БД: {product['name']}")
        context.user_data['product_id'] = product['id']
        
        keyboard = [
            [InlineKeyboardButton("⚖️ Ввести вес", callback_data=f"weight_{product['id']}")],
            [InlineKeyboardButton("🗑️ Удалить и искать заново", callback_data=f"delete_{product['id']}")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"📦 Найдено в вашей базе данных\n"
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Что хочешь сделать?",
            reply_markup=reply_markup
        )
        return SELECT_PRODUCT_FROM_LIST
    
    # 2. Ищем название в Open Food Facts
    await update.message.reply_text("🌐 Ищу название продукта в Open Food Facts...")
    off_products = await db.search_product_by_barcode(barcode)
    
    if off_products and len(off_products) > 0:
        product_name = off_products[0].get('name', '')
        context.user_data['off_product_name'] = product_name
        
        print(f"✅ Найдено название в Open Food Facts: {product_name}")
        
        # 3. Предлагаем поискать во ВкусВилл
        keyboard = [
            [InlineKeyboardButton("✅ Да, искать во ВкусВилл", callback_data=f"vkusvill_search_{barcode}")],
            [InlineKeyboardButton("❌ Нет, ввести вручную", callback_data="manual_entry")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🔍 В Open Food Facts найден продукт: {product_name}\n\n"
            "Хотите найти его КБЖУ во ВкусВилл?",
            reply_markup=reply_markup
        )
        return SELECT_PRODUCT_FROM_LIST
    
    # 4. Если ничего не найдено
    await update.message.reply_text(
        f"❌ Продукт со штрих-кодом {barcode} не найден.\n"
        "Попробуй ввести название продукта текстом."
    )
    return ENTER_PRODUCT
# --- КОНЕЦ ФУНКЦИИ: search_product_by_barcode_with_suggestion ---
# --- НАЧАЛО ФУНКЦИИ: handle_vkusvill_search ---
async def handle_vkusvill_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Искать во ВкусВилл'."""
    query = update.callback_query
    await query.answer()
    
    barcode = query.data.split('_')[2]
    product_name = context.user_data.get('off_product_name')
    
    if not product_name:
        await query.edit_message_text("❌ Не удалось найти название продукта.")
        return ConversationHandler.END
    
    # Поиск во ВкусВилл по названию
    await query.edit_message_text(f"🛒 Ищу КБЖУ продукта '{product_name}' во ВкусВилл...")
    vkusvill_products = await search_vkusvill_by_name(product_name)
    
    if vkusvill_products:
        # Показываем найденные продукты
        context.user_data['api_products'] = vkusvill_products
        await query.edit_message_text(
            f"🛒 Найдено во ВкусВилл:",
            reply_markup=None
        )
        # Показываем список продуктов
        return await show_product_list(update, context, vkusvill_products, "vkusvill")
    else:
        await query.edit_message_text("❌ Продукт не найден во ВкусВилл. Введите КБЖУ вручную.")
        return MANUAL_ENTRY
# --- КОНЕЦ ФУНКЦИИ: handle_vkusvill_search ---

# --- ПОИСК ПО ШТРИХ-КОДУ ---
async def search_by_barcode(update: Update, context: ContextTypes.DEFAULT_TYPE, barcode: str):
    print(f"🔍 ПОИСК ПО ШТРИХ-КОДУ: {barcode}")
    
    # <-- ИСПРАВЛЕНО: Сохраняем штрих-код для повторного поиска после удаления
    context.user_data['current_barcode'] = barcode
    
    # 1. Ищем в локальной базе
    product = await db.find_product_by_barcode(barcode)
    if product:
        print(f"✅ Найден в локальной БД: {product['name']}")
        context.user_data['product_id'] = product['id']
        
        keyboard = [
            [InlineKeyboardButton("⚖️ Ввести вес", callback_data=f"weight_{product['id']}")],
            [InlineKeyboardButton("🗑️ Удалить и искать заново", callback_data=f"delete_{product['id']}")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"📦 Найдено в вашей базе данных\n"
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Что хочешь сделать?",
            reply_markup=reply_markup
        )
        return SELECT_PRODUCT_FROM_LIST
    
    # 2. Ищем во ВкусВилл
    await update.message.reply_text("🛒 Ищу во ВкусВилл...")
    vkusvill_products = await search_vkusvill_by_barcode(barcode)
    
    if vkusvill_products:
        product_data = vkusvill_products[0]
        print(f"✅ Найден во ВкусВилл: {product_data['name']}")
        
        product = await db.add_product(
            name=product_data['name'],
            barcode=barcode,
            calories=product_data['calories'],
            protein=product_data['protein'],
            fat=product_data['fat'],
            carbs=product_data['carbs'],
            is_custom=False
        )
        async with db.pool.acquire() as conn:
            product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product['id'])
        
        context.user_data['product_id'] = product['id']
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🛒 Найдено во ВкусВилл\n"
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Сколько граммов ты съел?",
            reply_markup=reply_markup
        )
        return ENTER_WEIGHT
    
    # 3. Ищем через DeepSeek
    await update.message.reply_text("🤖 Ищу через DeepSeek...")
    deepseek_products = await db.search_product_by_deepseek(f"штрих-код {barcode} продукт")
    
    if deepseek_products:
        product_data = deepseek_products[0]
        print(f"✅ Найден через DeepSeek: {product_data['name']}")
        
        product = await db.add_product(
            name=product_data['name'],
            barcode=barcode,
            calories=product_data['calories'],
            protein=product_data['protein'],
            fat=product_data['fat'],
            carbs=product_data['carbs'],
            is_custom=False
        )
        async with db.pool.acquire() as conn:
            product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product['id'])
        
        context.user_data['product_id'] = product['id']
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🤖 Найдено через DeepSeek (примерные данные)\n"
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Сколько граммов ты съел?",
            reply_markup=reply_markup
        )
        return ENTER_WEIGHT
    
    # 4. Не найдено
    await update.message.reply_text(
        f"❌ Продукт со штрих-кодом {barcode} не найден.\n"
        "Попробуй ввести название продукта текстом."
    )
    return ENTER_PRODUCT


# --- ОСНОВНЫЕ ФУНКЦИИ ДОБАВЛЕНИЯ ---

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    
    meal_types = await db.get_meal_types()
    keyboard = [
        [InlineKeyboardButton(mt['name'], callback_data=f"meal_{mt['id']}")]
        for mt in meal_types
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("🍽️ Выбери приём пищи:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("🍽️ Выбери приём пищи:", reply_markup=reply_markup)
    return SELECT_MEAL

async def select_meal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_back":
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    meal_id = int(query.data.split('_')[1])
    context.user_data['meal_type_id'] = meal_id
    print(f"🍽️ meal_type_id сохранён: {meal_id}")
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📷 Отправь фото штрих-кода или напиши название продукта.\n\n"
        "Если продукт не найдётся, я предложу ввести КБЖУ вручную.",
        reply_markup=reply_markup
    )
    return ENTER_PRODUCT

# --- НАЧАЛО ФУНКЦИИ: enter_product ---
async def enter_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("📩 ПОЛУЧЕНО СООБЩЕНИЕ")
    
    if update.callback_query and update.callback_query.data == "menu_back":
        query = update.callback_query
        await query.answer()
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    if update.message.photo:
        print("📩 Это ФОТО")
        await update.message.reply_text("📷 Распознаю штрих-код...")
        
        try:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            
            from pyzbar.pyzbar import decode
            from PIL import Image
            import io
            
            image = Image.open(io.BytesIO(photo_bytes))
            decoded_objects = decode(image)
            
            if not decoded_objects:
                await update.message.reply_text("❌ Не удалось распознать штрих-код.")
                return ENTER_PRODUCT
            
            barcode = decoded_objects[0].data.decode('utf-8')
            print(f"📷 Распознан штрих-код: {barcode}")
            return await search_product_by_barcode_with_suggestion(update, context, barcode)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            await update.message.reply_text("❌ Ошибка обработки фото.")
            return ENTER_PRODUCT
    
    product_name = update.message.text.strip()
    print(f"📩 Текст: {product_name}")
    
    # Сохраняем запрос для повторного поиска
    context.user_data['current_search_query'] = product_name

    # 1. Локальная БД
    print("🔍 ПОИСК В ЛОКАЛЬНОЙ БАЗЕ")
    local_products = await db.find_products_by_name(product_name)
    print(f"🔍 Найдено: {len(local_products)}")
    if local_products:
        result = await show_product_list(update, context, local_products, "local")
        return result

    # 2. ВкусВилл (по названию)
    print("🛒 ПОИСК В ВКУСВИЛЛ")
    await update.message.reply_text("🛒 Ищу во ВкусВилл...")
    vkusvill_products = await search_vkusvill_by_name(product_name)
    
    if vkusvill_products:
        print(f"🛒 Найдено во ВкусВилл: {len(vkusvill_products)}")
        context.user_data['api_products'] = vkusvill_products
        result = await show_product_list(update, context, vkusvill_products, "vkusvill")
        return result

    # 3. Open Food Facts (как резерв)
    print("🌐 ПОИСК В OPEN FOOD FACTS")
    await update.message.reply_text("🌐 Ищу в Open Food Facts...")
    off_products = await db.search_product_by_name(product_name)
    
    if off_products:
        print(f"🌐 Найдено в Open Food Facts: {len(off_products)}")
        context.user_data['api_products'] = off_products
        result = await show_product_list(update, context, off_products, "openfoodfacts")
        return result

    # 4. DeepSeek (как последний резерв)
    print("🤖 ПОИСК В DEEPSEEK")
    await update.message.reply_text("🤖 Ищу через DeepSeek...")
    deepseek_products = await db.search_product_by_deepseek(product_name)
    
    if deepseek_products:
        context.user_data['api_products'] = deepseek_products
        result = await show_product_list(update, context, deepseek_products, "deepseek")
        return result
    
    # 5. Ручной ввод
    print("❌ ПРОДУКТ НЕ НАЙДЕН")
    keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "❌ Продукт не найден ни в одном источнике.\n"
        "Введи КБЖУ на 100 г через запятую:\n"
        "Пример: 45, 1.2, 0.3, 8.5",
        reply_markup=reply_markup
    )
    context.user_data['product_name'] = product_name
    return MANUAL_ENTRY
# --- КОНЕЦ ФУНКЦИИ: enter_product ---

# --- НАЧАЛО ФУНКЦИИ: show_product_list ---
async def show_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE, products, source):
    print(f"📋 ПОКАЗ СПИСКА ({source})")
    
    # Определяем текст источника
    source_text = {
        "local": "📦 Найдено в вашей базе данных",
        "vkusvill": "🛒 Найдено во ВкусВилл",
        "openfoodfacts": "🌐 Найдено в Open Food Facts",
        "deepseek": "🤖 Найдено через DeepSeek (примерные данные)"
    }.get(source, "📦 Найден продукт")
    
    if len(products) == 1:
        context.user_data['product_id'] = products[0]['id']
        print(f"📋 Единственный продукт: ID={products[0]['id']}")
        
        # Показываем источник и КБЖУ
        await update.message.reply_text(
            f"{source_text}\n"
            f"📦 {products[0]['name']}\n"
            f"🔥 {products[0]['calories']} ккал | "
            f"🥩 {products[0]['protein']}г | "
            f"🧈 {products[0]['fat']}г | "
            f"🍞 {products[0]['carbs']}г на 100 г\n"
        )
        
        # Если источник — локальная БД, показываем кнопки с удалением
        if source == "local":
            keyboard = [
                [InlineKeyboardButton("⚖️ Ввести вес", callback_data=f"weight_{products[0]['id']}")],
                [InlineKeyboardButton("🗑️ Удалить и искать заново", callback_data=f"delete_{products[0]['id']}")],
                [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Что хочешь сделать?",
                reply_markup=reply_markup
            )
            return SELECT_PRODUCT_FROM_LIST
        else:
            # Для других источников — только ввод веса
            back_button = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
            reply_markup = InlineKeyboardMarkup(back_button)
            await update.message.reply_text(
                "Сколько граммов ты съел?",
                reply_markup=reply_markup
            )
            return ENTER_WEIGHT

    # Если несколько продуктов — список для выбора
    keyboard = []
    for idx, product in enumerate(products):
        btn_text = f"{idx+1}. {product['name']} ({product['calories']} ккал/100г)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"prod_{product['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"{source_text}. Выбери продукт:",
        reply_markup=reply_markup
    )
    return SELECT_PRODUCT_FROM_LIST
# --- КОНЕЦ ФУНКЦИИ: show_product_list ---
# --- НАЧАЛО ФУНКЦИИ: select_product ---
async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("🎯 ВЫБОР ПРОДУКТА")
    
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_back":
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Обработка кнопки "Ввести вес"
    if query.data.startswith("weight_"):
        product_id = int(query.data.split('_')[1])
        context.user_data['product_id'] = product_id
        
        back_button = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(back_button)
        await query.edit_message_text(
            "Сколько граммов ты съел?",
            reply_markup=reply_markup
        )
        return ENTER_WEIGHT
    
    # Обработка кнопки "Удалить" с повторным поиском
    if query.data.startswith("delete_"):
        product_id = int(query.data.split('_')[1])
        print(f"🗑️ Удаление продукта ID={product_id}")
        
        try:
            async with db.pool.acquire() as conn:
                await conn.execute('DELETE FROM products WHERE id = $1', product_id)
            print(f"✅ Продукт удалён из БД")
            
            barcode = context.user_data.get('current_barcode')
            query_text = context.user_data.get('current_search_query')
            
            if barcode:
                await query.edit_message_text(
                    f"🗑️ Продукт удалён из вашей базы.\n"
                    "🔍 Ищем заново по штрих-коду..."
                )
                context.user_data.clear()
                return await search_product_by_barcode_with_suggestion(update, context, barcode)
            elif query_text:
                await query.edit_message_text(
                    f"🗑️ Продукт удалён из вашей базы.\n"
                    "🔍 Ищем заново по названию..."
                )
                context.user_data.clear()
                # Запускаем повторный поиск по названию (вызываем enter_product)
                return await enter_product(update, context)
            else:
                await query.edit_message_text(
                    f"🗑️ Продукт удалён из вашей базы.\n"
                    "Начни поиск заново через /add"
                )
                context.user_data.clear()
                await show_main_menu(update, context)
        except Exception as e:
            print(f"❌ Ошибка удаления: {e}")
            await query.edit_message_text(f"❌ Ошибка удаления: {e}")
        return ConversationHandler.END
    
    # Обычный выбор продукта из списка
    product_id_str = query.data.split('_')[1]
    product_id = int(product_id_str)
    print(f"🎯 product_id: {product_id}")
    
    context.user_data['product_id'] = product_id
    print(f"✅ product_id сохранён: {product_id}")
    
    # Ищем продукт в локальной БД
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    if product:
        print(f"✅ Найден в локальной БД: {product['name']}")
        
        # Проверяем источник (если продукт из локальной БД)
        keyboard = [
            [InlineKeyboardButton("⚖️ Ввести вес", callback_data=f"weight_{product['id']}")],
            [InlineKeyboardButton("🗑️ Удалить и искать заново", callback_data=f"delete_{product['id']}")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Что хочешь сделать?",
            reply_markup=reply_markup
        )
        return SELECT_PRODUCT_FROM_LIST
    
    # Если нет в локальной БД — ищем в API-продуктах
    api_products = context.user_data.get('api_products', [])
    print(f"🔍 Ищем в API-продуктах ({len(api_products)} шт.)")
    
    for p in api_products:
        if str(p['id']) == product_id_str:
            print(f"✅ Найден в API: {p['name']}")
            
            try:
                product = await db.add_product(
                    name=p['name'],
                    barcode=str(p['barcode']),
                    calories=p['calories'],
                    protein=p['protein'],
                    fat=p['fat'],
                    carbs=p['carbs'],
                    is_custom=False
                )
                print(f"✅ Сохранён в БД с ID: {product['id']}")
                
                async with db.pool.acquire() as conn:
                    product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product['id'])
                
                context.user_data['product_id'] = product['id']
                print(f"✅ product_id обновлён: {product['id']}")
                
                # Для продуктов из API — только ввод веса
                back_button = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
                reply_markup = InlineKeyboardMarkup(back_button)
                await query.edit_message_text(
                    f"📦 {product['name']}\n"
                    f"🔥 {product['calories']} ккал | "
                    f"🥩 {product['protein']}г | "
                    f"🧈 {product['fat']}г | "
                    f"🍞 {product['carbs']}г на 100 г\n\n"
                    "Сколько граммов ты съел?",
                    reply_markup=reply_markup
                )
                return ENTER_WEIGHT
            except Exception as e:
                print(f"❌ Ошибка сохранения: {e}")
                await query.edit_message_text(f"❌ Ошибка: {e}")
                return ConversationHandler.END
    
    print("❌ Продукт не найден")
    await query.edit_message_text("❌ Ошибка: продукт не найден.")
    return ConversationHandler.END
# --- КОНЕЦ ФУНКЦИИ: select_product ---


async def manual_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query and update.callback_query.data == "menu_back":
        query = update.callback_query
        await query.answer()
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    try:
        values = update.message.text.split(',')
        calories, protein, fat, carbs = map(float, values)
    except:
        await update.message.reply_text(
            "❌ Ошибка! Введи четыре числа через запятую.\n"
            "Пример: 45, 1.2, 0.3, 8.5"
        )
        return MANUAL_ENTRY
    
    product_name = context.user_data['product_name']
    product = await db.add_product(
        name=product_name,
        calories=calories,
        protein=protein,
        fat=fat,
        carbs=carbs,
        is_custom=True
    )
    context.user_data['product_id'] = product['id']
    print(f"✅ Ручной ввод: product_id={product['id']}")
    
    keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"✅ Продукт сохранён.\n"
        f"🔥 {calories} ккал | 🥩 {protein}г | 🧈 {fat}г | 🍞 {carbs}г\n\n"
        "Сколько граммов ты съел?",
        reply_markup=reply_markup
    )
    return ENTER_WEIGHT

async def enter_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("⚖️ ПОЛУЧЕН ВЕС")
    print(f"⚖️ Текст: {update.message.text}")
    print(f"⚖️ context.user_data: {context.user_data}")
    
    if update.callback_query and update.callback_query.data == "menu_back":
        query = update.callback_query
        await query.answer()
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    try:
        weight = float(update.message.text.replace(',', '.'))
        print(f"⚖️ Вес: {weight} г")
    except ValueError:
        await update.message.reply_text("❌ Введи число (граммы). Например: 150")
        return ENTER_WEIGHT
    
    product_id = context.user_data.get('product_id')
    meal_type_id = context.user_data.get('meal_type_id')
    
    print(f"⚖️ product_id из контекста: {product_id}")
    print(f"⚖️ meal_type_id из контекста: {meal_type_id}")
    
    if not product_id:
        print("❌ ОШИБКА: product_id не найден в контексте!")
        await update.message.reply_text("❌ Ошибка: продукт не найден. Попробуйте /add")
        return ConversationHandler.END
    
    if not meal_type_id:
        print("❌ ОШИБКА: meal_type_id не найден в контексте!")
        await update.message.reply_text("❌ Ошибка: приём пищи не выбран. Попробуйте /add")
        return ConversationHandler.END
    
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    if not product:
        print("❌ ОШИБКА: продукт не найден в БД!")
        await update.message.reply_text("❌ Ошибка: продукт не найден.")
        return ConversationHandler.END
    
    calories = (product['calories'] / 100) * weight
    protein = (product['protein'] / 100) * weight
    fat = (product['fat'] / 100) * weight
    carbs = (product['carbs'] / 100) * weight
    
    print(f"⚖️ Пересчитано: {calories:.1f} ккал, {protein:.1f}г б, {fat:.1f}г ж, {carbs:.1f}г у")
    
    try:
        await db.add_meal_entry(
            user_id=update.effective_user.id,
            product_id=product_id,
            meal_type_id=meal_type_id,
            weight=weight,
            calories=calories,
            protein=protein,
            fat=fat,
            carbs=carbs
        )
        print("✅ ЗАПИСЬ СОХРАНЕНА В БД!")
        
        await update.message.reply_text(
            format_nutrition(product['name'], weight, calories, protein, fat, carbs)
        )
        
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    except Exception as e:
        print(f"❌ ОШИБКА СОХРАНЕНИЯ: {e}")
        await update.message.reply_text(f"❌ Ошибка сохранения: {e}")
        return ConversationHandler.END

# --- ИСТОРИЯ, НЕДЕЛЯ, ЭКСПОРТ ---

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    if args and len(args) > 0:
        try:
            target_date = datetime.strptime(args[0], '%Y-%m-%d').date()
        except:
            await update.message.reply_text("❌ Неверный формат. Используй: /history YYYY-MM-DD")
            return
    else:
        target_date = (datetime.utcnow() + timedelta(hours=3)).date()
    
    entries = await db.get_daily_summary(user_id, target_date)
    totals = await db.get_daily_totals(user_id, target_date)
    
    response = f"📅 Сводка за {target_date.strftime('%d.%m.%Y')}\n\n"
    response += format_history(entries)
    response += f"\n\n🎯 Норма: 1800 ккал | Осталось: {1800 - totals['total_calories']:.0f} ккал"
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(response)
    else:
        await update.message.reply_text(response)
    await show_main_menu(update, context)

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    end_date = (datetime.utcnow() + timedelta(hours=3)).date()
    start_date = end_date - timedelta(days=6)
    
    response = f"📊 КБЖУ за 7 дней:\n\n"
    for i in range(7):
        current_date = start_date + timedelta(days=i)
        totals = await db.get_daily_totals(user_id, current_date)
        day_name = current_date.strftime('%a').capitalize()
        response += (
            f"{day_name} {current_date.strftime('%d.%m')}: "
            f"{totals['total_calories']:.0f} ккал | "
            f"Б: {totals['total_protein']:.1f}г | "
            f"Ж: {totals['total_fat']:.1f}г | "
            f"У: {totals['total_carbs']:.1f}г\n"
        )
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(response)
    else:
        await update.message.reply_text(response)
    await show_main_menu(update, context)

async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    if args and len(args) > 0:
        try:
            target_date = datetime.strptime(args[0], '%Y-%m-%d').date()
        except:
            await update.message.reply_text("❌ Неверный формат. Используй: /export YYYY-MM-DD")
            return
    else:
        target_date = (datetime.utcnow() + timedelta(hours=3)).date()
    
    entries = await db.get_day_entries(user_id, target_date)
    if not entries:
        await update.message.reply_text(f"📭 За {target_date.strftime('%d.%m.%Y')} записей нет.")
        await show_main_menu(update, context)
        return
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Дата', 'Приём пищи', 'Продукт', 'Вес (г)', 
                     'Калории', 'Белки', 'Жиры', 'Углеводы'])
    for entry in entries:
        writer.writerow([
            entry['date'].strftime('%Y-%m-%d'),
            entry['meal_type'],
            entry['product_name'],
            entry['weight_grams'],
            round(entry['calories'], 1),
            round(entry['protein'], 1),
            round(entry['fat'], 1),
            round(entry['carbs'], 1)
        ])
    
    output.seek(0)
    await update.message.reply_document(
        document=io.BytesIO(output.getvalue().encode('utf-8-sig')),
        filename=f"nutrition_{target_date.strftime('%Y-%m-%d')}.csv"
    )
    await show_main_menu(update, context)
print("🚀 [0.5] Функции и обработчики определены.", flush=True)

# --- ГЛАВНАЯ ФУНКЦИЯ ---
def main():
    print("🚀 [1] Вход в main()", flush=True)
    import sys
    import traceback
    import asyncio
    
    try:
        print("🚀 [1] Бот начинает запуск...", flush=True)
        
        print("🚀 [2] Создаю цикл событий...", flush=True)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print("🚀 [3] Цикл событий создан.", flush=True)
        
        print("🚀 [4] Подключаюсь к базе данных...", flush=True)
        loop.run_until_complete(db.connect())
        print("🚀 [5] Подключение к БД успешно.", flush=True)
        
        print("🚀 [6] Создаю таблицы...", flush=True)
        loop.run_until_complete(db.create_tables())
        print("🚀 [7] Таблицы созданы/проверены.", flush=True)
        
        print("🚀 [8] Создаю приложение Telegram...", flush=True)
        app = Application.builder().token(BOT_TOKEN).build()
        print("🚀 [9] Приложение Telegram создано.", flush=True)
        
        async def set_commands():
            await app.bot.set_my_commands([
                ("add", "➕ Добавить продукт"),
                ("history", "📊 Сводка за сегодня"),
                ("week", "📈 Статистика за 7 дней"),
                ("export", "📁 Выгрузить CSV"),
                ("cancel", "❌ Отменить действие"),
            ])
        print("🚀 [10] Устанавливаю команды бота...", flush=True)
        loop.run_until_complete(set_commands())
        print("🚀 [11] Команды установлены.", flush=True)
        
        print("🚀 [12] Создаю ConversationHandler...", flush=True)
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('add', add_start)],
            states={
                SELECT_MEAL: [CallbackQueryHandler(select_meal, pattern='^(meal_|menu_back)')],
                ENTER_PRODUCT: [
                    CallbackQueryHandler(enter_product, pattern='^menu_back$'),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, enter_product),
                    MessageHandler(filters.PHOTO, enter_product)
                ],
                SELECT_PRODUCT_FROM_LIST: [
                    CallbackQueryHandler(select_product, pattern='^(prod_|menu_back|weight_|delete_)')
                ],
                MANUAL_ENTRY: [
                    CallbackQueryHandler(manual_entry, pattern='^menu_back$'),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, manual_entry)
                ],
                ENTER_WEIGHT: [
                    CallbackQueryHandler(enter_weight, pattern='^menu_back$'),
                    MessageHandler(filters.Regex(r'^[\d.,]+$'), enter_weight)
                ]
            },
            fallbacks=[
                CommandHandler('cancel', cancel),
                CommandHandler('add', add_start)
            ],
            per_message=False,
            name="food_diary"
        )
        print("🚀 [13] ConversationHandler создан.", flush=True)
        
        print("🚀 [14] Добавляю обработчики...", flush=True)
        app.add_handler(conv_handler)
        app.add_handler(CallbackQueryHandler(handle_menu_button, pattern='^menu_'))
        app.add_handler(CallbackQueryHandler(handle_vkusvill_search, pattern='^vkusvill_search_'))
        app.add_handler(CommandHandler('start', start))
        app.add_handler(CommandHandler('history', history))
        app.add_handler(CommandHandler('week', week))
        app.add_handler(CommandHandler('export', export_csv))
        app.add_handler(CommandHandler('cancel', cancel))
        print("🚀 [15] Обработчики добавлены.", flush=True)
        
        print("🤖 Бот запущен!", flush=True)
        
        async def start_web_server():
            from aiohttp import web
            async def health_check(request):
                return web.Response(text="OK")
            app_web = web.Application()
            app_web.router.add_get('/', health_check)
            runner = web.AppRunner(app_web)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', 10000)
            await site.start()
            print("🌐 Web server started on port 10000", flush=True)
        
        print("🚀 [16] Запускаю веб-сервер...", flush=True)
        loop.run_until_complete(start_web_server())
        print("🚀 [17] Веб-сервер запущен.", flush=True)
        
        print("🚀 [18] Запускаю polling...", flush=True)
        app.run_polling()
        
    except Exception as e:
        print(f"🔥 КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
if __name__ == '__main__':
     main()
