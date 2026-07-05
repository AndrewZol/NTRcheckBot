import logging
import csv
import io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ConversationHandler, ContextTypes
)
from config import BOT_TOKEN
from database import Database

# --- Состояния ---
(
    SELECT_MEAL, ENTER_PRODUCT, ENTER_WEIGHT, 
    MANUAL_ENTRY, SELECT_PRODUCT_FROM_LIST
) = range(5)

db = Database()

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
    
    # menu_back обрабатывается в ConversationHandler, игнорируем здесь
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

# --- ДОБАВЛЕНИЕ ПРОДУКТА ---

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

async def search_by_barcode(update: Update, context: ContextTypes.DEFAULT_TYPE, barcode: str):
    print(f"🔍 ПОИСК ПО ШТРИХ-КОДУ: {barcode}")
    
    product = await db.find_product_by_barcode(barcode)
    if product:
        print(f"✅ Найден в локальной БД: {product['name']}")
        context.user_data['product_id'] = product['id']
        print(f"✅ product_id сохранён: {product['id']}")
        
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
    
    await update.message.reply_text("🌐 Ищу в Open Food Facts...")
    api_products = await db.search_product_by_barcode(barcode)
    
    if api_products:
        product_data = api_products[0]
        print(f"✅ Найден в Open Food Facts: {product_data['name']}")
        
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
        print(f"✅ product_id сохранён: {product['id']}")
        
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
    
    await update.message.reply_text(f"❌ Продукт со штрих-кодом {barcode} не найден.")
    return ENTER_PRODUCT

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
            return await search_by_barcode(update, context, barcode)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            await update.message.reply_text("❌ Ошибка обработки фото.")
            return ENTER_PRODUCT
    
    product_name = update.message.text.strip()
    print(f"📩 Текст: {product_name}")

    # 1. Локальная БД
    print("🔍 ПОИСК В ЛОКАЛЬНОЙ БАЗЕ")
    local_products = await db.find_products_by_name(product_name)
    print(f"🔍 Найдено: {len(local_products)}")
    if local_products:
        await show_product_list(update, context, local_products, "local")
        return SELECT_PRODUCT_FROM_LIST

    # 2. Open Food Facts
    print("🌐 ПОИСК В OPEN FOOD FACTS")
    await update.message.reply_text("🔍 Ищу в глобальной базе...")
    api_products = await db.search_product_by_name(product_name)
    print(f"🌐 Найдено: {len(api_products) if api_products else 0}")
    
    if api_products:
        context.user_data['api_products'] = api_products
        await show_product_list(update, context, api_products, "api")
        return SELECT_PRODUCT_FROM_LIST

    # 3. DeepSeek
    print("🤖 ПОИСК В DEEPSEEK")
    await update.message.reply_text("🤖 Ищу в DeepSeek...")
    deepseek_products = await db.search_product_by_deepseek(product_name)
    
    if deepseek_products:
        context.user_data['api_products'] = deepseek_products
        await show_product_list(update, context, deepseek_products, "deepseek")
        return SELECT_PRODUCT_FROM_LIST
    
    # 4. Ручной ввод
    print("❌ ПРОДУКТ НЕ НАЙДЕН")
    keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "❌ Продукт не найден.\n"
        "Введи КБЖУ на 100 г через запятую:\n"
        "Пример: 45, 1.2, 0.3, 8.5",
        reply_markup=reply_markup
    )
    context.user_data['product_name'] = product_name
    return MANUAL_ENTRY

async def show_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE, products, source):
    print(f"📋 ПОКАЗ СПИСКА ({source})")
    
    if source == "deepseek":
        await update.message.reply_text("🤖 Данные от DeepSeek (примерные):")
    
    back_button = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")]]
    back_markup = InlineKeyboardMarkup(back_button)
    
    if len(products) == 1:
        context.user_data['product_id'] = products[0]['id']
        print(f"📋 Единственный продукт: ID={products[0]['id']}")
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

    keyboard = []
    for idx, product in enumerate(products):
        btn_text = f"{idx+1}. {product['name']} ({product['calories']} ккал/100г)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"prod_{product['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔍 Выбери продукт:", reply_markup=reply_markup)
    return SELECT_PRODUCT_FROM_LIST

async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("=" * 50)
    print("🎯 ВЫБОР ПРОДУКТА")
    
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_back":
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    product_id_str = query.data.split('_')[1]
    product_id = int(product_id_str)
    print(f"🎯 product_id: {product_id}")
    
    # Сохраняем ID в контекст
    context.user_data['product_id'] = product_id
    print(f"✅ product_id сохранён: {product_id}")
    
    # Ищем продукт в локальной БД
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    if product:
        print(f"✅ Найден в локальной БД: {product['name']}")
        
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
        return ENTER_WEIGHT  # <-- ЯВНО ВОЗВРАЩАЕМ СОСТОЯНИЕ
    
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
                print(f"❌ Ошибка сохранения: {e}")
                await query.edit_message_text(f"❌ Ошибка: {e}")
                return ConversationHandler.END
    
    print("❌ Продукт не найден")
    await query.edit_message_text("❌ Ошибка: продукт не найден.")
    return ConversationHandler.END

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
    
    try:
        weight = float(update.message.text.replace(',', '.'))
        print(f"⚖️ Вес: {weight} г")
    except ValueError:
        await update.message.reply_text("❌ Введи число (граммы). Например: 150")
        return ENTER_WEIGHT
    
    product_id = context.user_data.get('product_id')
    meal_type_id = context.user_data.get('meal_type_id')
    
    print(f"⚖️ product_id: {product_id}")
    print(f"⚖️ meal_type_id: {meal_type_id}")
    
    if not product_id:
        print("❌ ОШИБКА: нет product_id")
        await update.message.reply_text("❌ Ошибка: продукт не найден.")
        return ConversationHandler.END
    
    if not meal_type_id:
        print("❌ ОШИБКА: нет meal_type_id")
        await update.message.reply_text("❌ Ошибка: приём пищи не выбран.")
        return ConversationHandler.END
    
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    if not product:
        print("❌ ОШИБКА: продукт не найден в БД")
        await update.message.reply_text("❌ Ошибка: продукт не найден.")
        return ConversationHandler.END
    
    calories = (product['calories'] / 100) * weight
    protein = (product['protein'] / 100) * weight
    fat = (product['fat'] / 100) * weight
    carbs = (product['carbs'] / 100) * weight
    
    print(f"⚖️ Пересчитано: {calories:.1f} ккал")
    
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
        print("✅ ЗАПИСЬ СОХРАНЕНА!")
        
        await update.message.reply_text(
            format_nutrition(product['name'], weight, calories, protein, fat, carbs)
        )
        
        context.user_data.clear()
        await show_main_menu(update, context)
        return ConversationHandler.END
    except Exception as e:
        print(f"❌ ОШИБКА СОХРАНЕНИЯ: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return ConversationHandler.END

# --- ИСТОРИЯ ---

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
            CallbackQueryHandler(enter_product, pattern='^menu_back$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_product),
            MessageHandler(filters.PHOTO, enter_product)
        ],
        SELECT_PRODUCT_FROM_LIST: [
            CallbackQueryHandler(select_product, pattern='^(prod_|menu_back)')
        ],
        MANUAL_ENTRY: [
            CallbackQueryHandler(manual_entry, pattern='^menu_back$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, manual_entry)
        ],
        ENTER_WEIGHT: [
            CallbackQueryHandler(enter_weight, pattern='^menu_back$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_weight)
        ]
    },
    fallbacks=[
        CommandHandler('cancel', cancel),
        CommandHandler('add', add_start)
    ],
    per_message=False
)
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('history', history))
    app.add_handler(CommandHandler('week', week))
    app.add_handler(CommandHandler('export', export_csv))
    app.add_handler(CommandHandler('cancel', cancel))
    
    # НЕ ДОБАВЛЯЙТЕ ГЛОБАЛЬНЫЙ CallbackQueryHandler для menu_back!
    # Вместо этого, кнопки главного меню обрабатываются через conv_handler
    
    print("🤖 Бот запущен!")
    
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
        print("🌐 Web server started on port 10000")
    
    loop.run_until_complete(start_web_server())
    app.run_polling()

if __name__ == '__main__':
    main()
