import logging
import csv
import io
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ConversationHandler, ContextTypes
)
from config import BOT_TOKEN
from database import Database

# --- Состояния для ConversationHandler ---
(
    SELECT_MEAL, ENTER_PRODUCT, ENTER_WEIGHT, 
    MANUAL_ENTRY, SELECT_PRODUCT_FROM_LIST
) = range(5)

# Инициализация БД
db = Database()

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

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
    
    # Добавляем итоги последнего приёма
    if current_meal:
        result += (
            f"   Итого: {meal_total['calories']:.0f} ккал, "
            f"{meal_total['protein']:.1f}г б, "
            f"{meal_total['fat']:.1f}г ж, "
            f"{meal_total['carbs']:.1f}г у\n\n"
        )
    
    # Общий итог
    result += (
        f"📊 Всего за день: {grand_total['calories']:.0f} ккал | "
        f"Б: {grand_total['protein']:.1f}г | "
        f"Ж: {grand_total['fat']:.1f}г | "
        f"У: {grand_total['carbs']:.1f}г"
    )
    
    return result

# --- КНОПКИ ГЛАВНОГО МЕНЮ ---

async def main_menu_keyboard():
    """Создаёт клавиатуру с главным меню."""
    keyboard = [
        [InlineKeyboardButton("➕ Добавить продукт", callback_data="menu_add")],
        [InlineKeyboardButton("📊 История за сегодня", callback_data="menu_history")],
        [InlineKeyboardButton("📈 Статистика за неделю", callback_data="menu_week")],
        [InlineKeyboardButton("📁 Экспорт CSV", callback_data="menu_export")],
        [InlineKeyboardButton("❌ Отменить действие", callback_data="menu_cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает главное меню с кнопками."""
    keyboard = await main_menu_keyboard()
    
    # Если есть сообщение для редактирования
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "📋 Главное меню:",
            reply_markup=keyboard
        )
    else:
        # Если это новое сообщение
        await update.message.reply_text(
            "📋 Главное меню:",
            reply_markup=keyboard
        )

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на кнопки главного меню."""
    query = update.callback_query
    await query.answer()
    
    action = query.data
    
    if action == "menu_add":
        # Перенаправляем на /add
        await add_start(update, context)
    elif action == "menu_history":
        # Перенаправляем на /history
        await history(update, context)
    elif action == "menu_week":
        # Перенаправляем на /week
        await week(update, context)
    elif action == "menu_export":
        # Перенаправляем на /export
        await export_csv(update, context)
    elif action == "menu_cancel":
        # Перенаправляем на /cancel
        await cancel(update, context)

# --- ОБРАБОТЧИКИ КОМАНД ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.register_user(user.id, user.username or "без username")
    
    await show_main_menu(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    
    # Проверяем, откуда пришёл запрос (из кнопки или текстом)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("❌ Действие отменено.")
        # Показываем главное меню
        keyboard = await main_menu_keyboard()
        await query.edit_message_text(
            "📋 Главное меню:",
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text("❌ Действие отменено.")
        # Показываем главное меню
        keyboard = await main_menu_keyboard()
        await update.message.reply_text(
            "📋 Главное меню:",
            reply_markup=keyboard
        )
    return ConversationHandler.END

# --- ДОБАВЛЕНИЕ ПРОДУКТА ---

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Очищаем контекст перед началом нового диалога
    context.user_data.clear()
    
    meal_types = await db.get_meal_types()
    keyboard = [
        [InlineKeyboardButton(mt['name'], callback_data=f"meal_{mt['id']}")]
        for mt in meal_types
    ]
    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Если это ответ на кнопку "Добавить"
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "🍽️ Выбери приём пищи:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "🍽️ Выбери приём пищи:",
            reply_markup=reply_markup
        )
    return SELECT_MEAL

async def select_meal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_back":
        # Выход из диалога и показ главного меню
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    meal_id = int(query.data.split('_')[1])
    context.user_data['meal_type_id'] = meal_id
    
    # Добавляем кнопку "Назад"
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📷 Отправь фото штрих-кода или напиши название продукта.\n\n"
        "Если продукт не найдётся, я предложу ввести КБЖУ вручную.",
        reply_markup=reply_markup
    )
    return ENTER_PRODUCT

async def search_by_barcode(update: Update, context: ContextTypes.DEFAULT_TYPE, barcode: str):
    """Ищет продукт по штрих-коду (локально, затем в Open Food Facts)."""
    print(f"🔍 [BARCODE] ПОИСК ПО ШТРИХ-КОДУ: {barcode}")
    
    # 1. Ищем в локальной базе
    product = await db.find_product_by_barcode(barcode)
    if product:
        print(f"✅ Найден в локальной БД: {product['name']}")
        context.user_data['product_id'] = product['id']
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Сколько граммов ты съел?",
            reply_markup=reply_markup
        )
        return ENTER_WEIGHT
    
    # 2. Ищем в Open Food Facts
    await update.message.reply_text("🌐 Ищу продукт в Open Food Facts...")
    api_products = await db.search_product_by_barcode(barcode)
    
    if api_products:
        product_data = api_products[0]
        print(f"✅ Найден в Open Food Facts: {product_data['name']}")
        
        # Сохраняем в локальную БД
        product = await db.add_product(
            name=product_data['name'],
            barcode=barcode,
            calories=product_data['calories'],
            protein=product_data['protein'],
            fat=product_data['fat'],
            carbs=product_data['carbs'],
            is_custom=False
        )
        # Перезапрашиваем из БД
        async with db.pool.acquire() as conn:
            product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product['id'])
        
        context.user_data['product_id'] = product['id']
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📦 {product['name']}\n"
            f"🔥 {product['calories']} ккал | "
            f"🥩 {product['protein']}г | "
            f"🧈 {product['fat']}г | "
            f"🍞 {product['carbs']}г на 100 г\n\n"
            "Сколько граммов ты съел?",
            reply_markup=reply_markup
        )
        return ENTER_WEIGHT
    
    # 3. Не найдено
    await update.message.reply_text(
        f"❌ Продукт со штрих-кодом {barcode} не найден.\n"
        "Попробуй ввести название продукта текстом."
    )
    return ENTER_PRODUCT

async def enter_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("📩 [1] ПОЛУЧЕНО СООБЩЕНИЕ")
    
    # Проверяем, не нажали ли "Назад" через callback
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "menu_back":
            context.user_data.clear()
            await show_main_menu(update, context)
            return ConversationHandler.END
    
    if update.message.photo:
        print("📩 Это ФОТО")
    else:
        print(f"📩 Текст: {update.message.text}")
    print("=" * 50)
    
    # Если прислали фото — пытаемся распознать штрих-код
    if update.message.photo:
        print("📷 [1.1] ОБРАБОТКА ФОТО")
        await update.message.reply_text("📷 Распознаю штрих-код...")
        
        try:
            # Скачиваем фото
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            
            # Распознаём штрих-код
            from pyzbar.pyzbar import decode
            from PIL import Image
            import io
            
            image = Image.open(io.BytesIO(photo_bytes))
            decoded_objects = decode(image)
            
            if not decoded_objects:
                await update.message.reply_text(
                    "❌ Не удалось распознать штрих-код на фото.\n"
                    "Попробуй сфотографировать чётче или введи название продукта текстом."
                )
                return ENTER_PRODUCT
            
            # Берём первый найденный штрих-код
            barcode = decoded_objects[0].data.decode('utf-8')
            print(f"📷 Распознан штрих-код: {barcode}")
            
            # Ищем продукт по штрих-коду
            return await search_by_barcode(update, context, barcode)
            
        except Exception as e:
            print(f"❌ Ошибка при распознавании штрих-кода: {e}")
            await update.message.reply_text(
                "❌ Ошибка при обработке фото. Попробуй ввести название продукта текстом."
            )
            return ENTER_PRODUCT
    
    # Если прислали текст — ищем по названию
    product_name = update.message.text.strip()
    context.user_data['search_query'] = product_name

    # 1. Сначала ищем в локальной базе
    print("🔍 [2] ПОИСК В ЛОКАЛЬНОЙ БАЗЕ")
    local_products = await db.find_products_by_name(product_name)
    print(f"🔍 Найдено в локальной БД: {len(local_products)}")

    if local_products:
        print("✅ Использую локальные продукты")
        await show_product_list(update, context, local_products, source="local")
        return SELECT_PRODUCT_FROM_LIST

    # 2. Если локально не нашли, ищем через Open Food Facts API
    print("🌐 [3] ПОИСК В OPEN FOOD FACTS API")
    await update.message.reply_text("🔍 Ищу в глобальной базе Open Food Facts...")
    api_products = await db.search_product_by_name(product_name)
    
    print(f"🌐 Найдено в API: {len(api_products) if api_products else 0}")
    
    if api_products:
        # Выводим ID найденных продуктов
        print("🌐 ID продуктов из API:")
        for idx, p in enumerate(api_products):
            print(f"   {idx+1}. ID: {p['id']}, Название: {p['name']}")
        
        # Сохраняем ВЕСЬ список API-продуктов в контекст
        context.user_data['api_products'] = api_products
        print("✅ [4] API-продукты сохранены в context.user_data['api_products']")
        print(f"✅ Всего сохранено: {len(context.user_data['api_products'])} продуктов")
        
        await show_product_list(update, context, api_products, source="api")
        return SELECT_PRODUCT_FROM_LIST
    
    # 3. Если API не нашёл, пробуем DeepSeek
    print("🤖 [5.1] ПРОБУЕМ НАЙТИ ЧЕРЕЗ DEEPSEEK")
    await update.message.reply_text("🤖 Ищу в базе DeepSeek (примерные данные)...")
    
    deepseek_products = await db.search_product_by_deepseek(product_name)
    
    if deepseek_products:
        print(f"🤖 Найдено через DeepSeek: {len(deepseek_products)}")
        context.user_data['api_products'] = deepseek_products
        await show_product_list(update, context, deepseek_products, source="deepseek")
        return SELECT_PRODUCT_FROM_LIST
    else:
        # 4. Если и DeepSeek не нашёл — предлагаем ввести вручную
        print("❌ [5.2] ПРОДУКТ НЕ НАЙДЕН НИГДЕ")
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Продукт не найден ни в локальной базе, ни в Open Food Facts, ни в DeepSeek.\n\n"
            "📸 Ты можешь отправить фото штрих-кода, и я попробую найти продукт по нему.\n"
            "Либо введи КБЖУ на 100 г вручную через запятую:\n"
            "Пример: 45, 1.2, 0.3, 8.5\n"
            "(Калории, Белки, Жиры, Углеводы)",
            reply_markup=reply_markup
        )
        context.user_data['product_name'] = product_name
        return MANUAL_ENTRY

async def show_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE, products, source="unknown"):
    """Вспомогательная функция для отображения списка продуктов."""
    print(f"📋 [6] ПОКАЗ СПИСКА ПРОДУКТОВ (источник: {source})")
    print(f"📋 Количество продуктов в списке: {len(products)}")
    
    if source == "deepseek":
        await update.message.reply_text("🤖 Данные от DeepSeek (примерные, могут отличаться):")
    
    # Кнопка "Назад в меню"
    back_button = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
    back_markup = InlineKeyboardMarkup(back_button)
    
    if len(products) == 1:
        # Если один продукт — сразу спрашиваем вес
        context.user_data['product_id'] = products[0]['id']
        print(f"📋 Выбран единственный продукт: ID={products[0]['id']}, {products[0]['name']}")
        await update.message.reply_text(
            f"📦 {products[0]['name']}\n"
            f"🔥 {products[0]['calories']} ккал | "
            f"🥩 {products[0]['protein']}г | "
            f"🧈 {products[0]['fat']}г | "
            f"🍞 {products[0]['carbs']}г на 100 г\n\n"
            "Сколько граммов ты съел?",
            reply_markup=back_markup
        )
        return ENTER_WEIGHT

    # Если несколько — показываем список для выбора
    keyboard = []
    for idx, product in enumerate(products):
        btn_text = f"{idx+1}. {product['name']} ({product['calories']} ккал/100г)"
        callback_data = f"prod_{product['id']}"
        print(f"📋 Кнопка {idx+1}: ID={product['id']}, callback={callback_data}")
        keyboard.append([
            InlineKeyboardButton(btn_text, callback_data=callback_data)
        ])
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔍 Найдено несколько продуктов. Выбери нужный:",
        reply_markup=reply_markup
    )
    print("✅ [7] СПИСОК ПОКАЗАН, ОЖИДАЕМ ВЫБОР")
    return SELECT_PRODUCT_FROM_LIST

async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("🎯 [8] ПОЛУЧЕН ВЫБОР ПРОДУКТА")
    
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_back":
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Получаем ID продукта из callback_data
    callback_data = query.data
    product_id_str = callback_data.split('_')[1]
    product_id = int(product_id_str)
    
    print(f"🎯 callback_data: {callback_data}")
    print(f"🎯 Извлечён product_id (строка): {product_id_str}")
    print(f"🎯 Извлечён product_id (число): {product_id}")
    
    context.user_data['product_id'] = product_id
    print(f"🎯 product_id сохранён в context.user_data")
    
    # Пытаемся найти продукт в локальной БД
    print("🔍 [9] ПОИСК В ЛОКАЛЬНОЙ БД ПО ID")
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    if product:
        print(f"✅ [10] Продукт НАЙДЕН в локальной БД: {product['name']}")
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
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
    
    print("⚠️ [10] Продукт НЕ НАЙДЕН в локальной БД")
    
    # Если продукта нет в локальной БД — ищем в сохранённых из API
    api_products = context.user_data.get('api_products', [])
    print(f"🔍 [11] Ищем в API-продуктах (всего: {len(api_products)})")
    print(f"🔍 Ищем ID (строка): {product_id_str}")
    print(f"🔍 Доступные ID: {[str(p['id']) for p in api_products]}")
    
    found_in_api = False
    for p in api_products:
        if str(p['id']) == product_id_str:
            found_in_api = True
            print(f"✅ [12] Продукт НАЙДЕН в API: {p['name']}")
            print(f"📦 Данные продукта: {p}")
            
            print("💾 [13] СОХРАНЕНИЕ В ЛОКАЛЬНУЮ БД")
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
                print(f"✅ [14] Продукт сохранён в локальную БД с ID: {product['id']}")
                
                async with db.pool.acquire() as conn:
                    product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product['id'])
                print(f"✅ [15] Данные из БД: {product['name']}, {product['calories']} ккал")
                
                context.user_data['product_id'] = product['id']
                print(f"✅ [15.1] product_id обновлён в контексте: {product['id']}")
                
                keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
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
                print(f"❌ ОШИБКА при сохранении в БД: {e}")
                await query.edit_message_text(
                    f"❌ Ошибка сохранения продукта: {e}"
                )
                return ConversationHandler.END
    
    if not found_in_api:
        print("❌ [16] Продукт НЕ НАЙДЕН ни в локальной БД, ни в API")
        await query.edit_message_text(
            "❌ Ошибка: продукт не найден. Попробуйте снова /add"
        )
        return ConversationHandler.END

async def manual_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, не нажали ли "Назад"
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "menu_back":
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
    
    keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Продукт {product_name} сохранён.\n"
        f"🔥 {calories} ккал | 🥩 {protein}г | 🧈 {fat}г | 🍞 {carbs}г на 100 г\n\n"
        "Сколько граммов ты съел?",
        reply_markup=reply_markup
    )
    return ENTER_WEIGHT

async def enter_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("⚖️ [17] ПОЛУЧЕН ВЕС")
    print(f"⚖️ Текст: {update.message.text}")
    print(f"⚖️ context.user_data: {context.user_data}")
    
    # Проверяем, не нажали ли "Назад"
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "menu_back":
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
        await update.message.reply_text(
            "❌ Ошибка: продукт не найден. Попробуйте снова /add"
        )
        return ConversationHandler.END
    
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
        print(f"⚖️ Продукт из БД: {product}")
    
    if not product:
        print("❌ ОШИБКА: продукт не найден в БД!")
        await update.message.reply_text(
            "❌ Ошибка: продукт не найден. Попробуйте снова /add"
        )
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
        print("✅ [18] ЗАПИСЬ СОХРАНЕНА В БД!")
        
        await update.message.reply_text(
            format_nutrition(product['name'], weight, calories, protein, fat, carbs)
        )
        
        # Показываем главное меню после сохранения
        await show_main_menu(update, context)
        
        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        print(f"❌ ОШИБКА при сохранении записи: {e}")
        await update.message.reply_text(f"❌ Ошибка сохранения: {e}")
        return ConversationHandler.END

# --- ИСТОРИЯ ---

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    if args and len(args) > 0:
        try:
            target_date = datetime.strptime(args[0], '%Y-%m-%d').date()
        except:
            await update.message.reply_text(
                "❌ Неверный формат. Используй: /history YYYY-MM-DD"
            )
            return
    else:
        # Используем московскую дату (UTC+3)
        target_date = (datetime.utcnow() + timedelta(hours=3)).date()
    
    entries = await db.get_daily_summary(user_id, target_date)
    totals = await db.get_daily_totals(user_id, target_date)
    
    response = f"📅 Сводка за {target_date.strftime('%d.%m.%Y')}\n\n"
    response += format_history(entries)
    response += f"\n\n🎯 Ваша норма (примерная): 1800 ккал | Осталось: {1800 - totals['total_calories']:.0f} ккал"
    
    # Если это ответ на кнопку, редактируем сообщение
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(response)
    else:
        await update.message.reply_text(response)
    
    # Показываем главное меню
    await show_main_menu(update, context)

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    end_date = (datetime.utcnow() + timedelta(hours=3)).date()
    start_date = end_date - timedelta(days=6)
    
    response = f"📊 КБЖУ за последние 7 дней:\n\n"
    
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

# --- ЭКСПОРТ CSV ---

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

# --- ГЛАВНАЯ ФУНКЦИЯ ---

def main():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.connect())
    loop.run_until_complete(db.create_tables())
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация команд для меню Telegram
    async def set_commands():
        await app.bot.set_my_commands([
            ("add", "➕ Добавить продукт"),
            ("history", "📊 Сводка за сегодня"),
            ("week", "📈 Статистика за 7 дней"),
            ("export", "📁 Выгрузить CSV"),
            ("cancel", "❌ Отменить действие"),
        ])
    loop.run_until_complete(set_commands())
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_start)],
        states={
            SELECT_MEAL: [CallbackQueryHandler(select_meal, pattern='^(meal_|menu_back)')],
            ENTER_PRODUCT: [
                CallbackQueryHandler(enter_product, pattern='^menu_back$'),  # <-- Обработка "Назад"
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_product),
                MessageHandler(filters.PHOTO, enter_product)
            ],
            SELECT_PRODUCT_FROM_LIST: [
                CallbackQueryHandler(select_product, pattern='^(prod_|menu_back)')
            ],
            MANUAL_ENTRY: [
                CallbackQueryHandler(manual_entry, pattern='^menu_back$'),  # <-- Обработка "Назад"
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_entry)
            ],
            ENTER_WEIGHT: [
                CallbackQueryHandler(enter_weight, pattern='^menu_back$'),  # <-- Обработка "Назад"
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_weight)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel),
                  CommandHandler('add', add_start)],
        per_message=False  # <-- ВАЖНО!
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('history', history))
    app.add_handler(CommandHandler('week', week))
    app.add_handler(CommandHandler('export', export_csv))
    app.add_handler(CommandHandler('cancel', cancel))
    
    # Обработчики кнопок главного меню
    app.add_handler(CallbackQueryHandler(handle_menu_button, pattern='^menu_'))
    
    print("🤖 Бот запущен!")
    
    # --- ЗАПУСКАЕМ ВЕБ-СЕРВЕР ДЛЯ HEALTH CHECK ---
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
        print("🌐 Веб-сервер для health check запущен на порту 10000")
    
    # Запускаем веб-сервер
    loop.run_until_complete(start_web_server())
    
    # Запускаем бота
    app.run_polling()

if __name__ == '__main__':
    main()
