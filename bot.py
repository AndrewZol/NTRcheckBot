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

# --- ОБРАБОТЧИКИ КОМАНД ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.register_user(user.id, user.username or "без username")
    
    await update.message.reply_text(
        "👋 Привет! Я бот для подсчёта КБЖУ.\n\n"
        "📌 Команды:\n"
        "/add — добавить продукт в дневник\n"
        "/history — показать сводку за сегодня\n"
        "/history YYYY-MM-DD — за конкретную дату\n"
        "/week — за последние 7 дней\n"
        "/export — выгрузить CSV за сегодня\n"
        "/cancel — отменить текущее действие"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено.")

# --- ДОБАВЛЕНИЕ ПРОДУКТА ---

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    meal_types = await db.get_meal_types()
    keyboard = [
        [InlineKeyboardButton(mt['name'], callback_data=f"meal_{mt['id']}")]
        for mt in meal_types
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🍽️ Выбери приём пищи:",
        reply_markup=reply_markup
    )
    return SELECT_MEAL

async def select_meal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    meal_id = int(query.data.split('_')[1])
    context.user_data['meal_type_id'] = meal_id
    
    await query.edit_message_text(
        "📷 Отправь фото штрих-кода или напиши название продукта.\n\n"
        "Если продукт не найдётся, я предложу ввести КБЖУ вручную."
    )
    return ENTER_PRODUCT

async def enter_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Если прислали фото
    if update.message.photo:
        # Здесь должна быть логика распознавания штрих-кода
        # Пока заглушка — распознаём только если есть подпись к фото
        await update.message.reply_text(
            "⚠️ Функция распознавания штрих-кода пока в разработке.\n"
            "Пожалуйста, напиши название продукта текстом."
        )
        return ENTER_PRODUCT
    
    # Если прислали текст
    product_name = update.message.text.strip()
    context.user_data['search_query'] = product_name

    # 1. Сначала ищем в локальной базе
    local_products = await db.find_products_by_name(product_name)

    if local_products:
        # Если нашли в локальной БД — показываем их
        await show_product_list(update, context, local_products)
        return SELECT_PRODUCT_FROM_LIST

    # 2. Если локально не нашли, ищем через Open Food Facts API
    await update.message.reply_text("🔍 Ищу в глобальной базе Open Food Facts...")
    api_products = await db.search_product_by_name(product_name)

    if api_products:
        # Сохраняем ВЕСЬ список API-продуктов в контекст для последующего выбора
        context.user_data['api_products'] = api_products
        await show_product_list(update, context, api_products)
        return SELECT_PRODUCT_FROM_LIST
    else:
        # 3. Если продукт не найден нигде — предлагаем ввести вручную
        await update.message.reply_text(
            "❌ Продукт не найден ни в локальной базе, ни в Open Food Facts.\n\n"
            "📸 Ты можешь отправить фото штрих-кода, и я попробую найти продукт по нему.\n"
            "Либо введи КБЖУ на 100 г вручную через запятую:\n"
            "Пример: 45, 1.2, 0.3, 8.5\n"
            "(Калории, Белки, Жиры, Углеводы)"
        )
        context.user_data['product_name'] = product_name
        return MANUAL_ENTRY

async def show_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE, products):
    """Вспомогательная функция для отображения списка продуктов."""
    if len(products) == 1:
        # Если один продукт — сразу спрашиваем вес
        context.user_data['product_id'] = products[0]['id']
        await update.message.reply_text(
            f"📦 {products[0]['name']}\n"
            f"🔥 {products[0]['calories']} ккал | "
            f"🥩 {products[0]['protein']}г | "
            f"🧈 {products[0]['fat']}г | "
            f"🍞 {products[0]['carbs']}г на 100 г\n\n"
            "Сколько граммов ты съел?"
        )
        return ENTER_WEIGHT

    # Если несколько — показываем список для выбора
    keyboard = []
    for idx, product in enumerate(products):
        btn_text = f"{idx+1}. {product['name']} ({product['calories']} ккал/100г)"
        keyboard.append([
            InlineKeyboardButton(btn_text, callback_data=f"prod_{product['id']}")
        ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔍 Найдено несколько продуктов. Выбери нужный:",
        reply_markup=reply_markup
    )
    return SELECT_PRODUCT_FROM_LIST

async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Получаем ID продукта из callback_data
    product_id = int(query.data.split('_')[1])
    context.user_data['product_id'] = product_id
    
    # Пытаемся найти продукт в локальной БД
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    # Если продукта нет в локальной БД — ищем в сохранённых из API
    if not product:
        api_products = context.user_data.get('api_products', [])
        print(f"🔍 Ищем в API-продуктах (ID: {product_id})")
        for p in api_products:
            if p['id'] == product_id:
                # Сохраняем продукт в локальную БД
                product = await db.add_product(
                    name=p['name'],
                    barcode=p['barcode'],
                    calories=p['calories'],
                    protein=p['protein'],
                    fat=p['fat'],
                    carbs=p['carbs'],
                    is_custom=False
                )
                # Перезапрашиваем из БД, чтобы получить правильный id
                product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product['id'])
                break
    
    # Если продукт всё ещё None — ошибка
    if not product:
        await query.edit_message_text(
            "❌ Ошибка: продукт не найден. Попробуйте снова /add"
        )
        return ConversationHandler.END
    
    # Показываем продукт и запрашиваем вес
    await query.edit_message_text(
        f"📦 {product['name']}\n"
        f"🔥 {product['calories']} ккал | "
        f"🥩 {product['protein']}г | "
        f"🧈 {product['fat']}г | "
        f"🍞 {product['carbs']}г на 100 г\n\n"
        "Сколько граммов ты съел?"
    )
    return ENTER_WEIGHT

async def manual_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        values = update.message.text.split(',')
        calories, protein, fat, carbs = map(float, values)
    except:
        await update.message.reply_text(
            "❌ Ошибка! Введи четыре числа через запятую.\n"
            "Пример: 45, 1.2, 0.3, 8.5"
        )
        return MANUAL_ENTRY
    
    # Сохраняем продукт как пользовательский
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
    
    await update.message.reply_text(
        f"✅ Продукт {product_name} сохранён.\n"
        f"🔥 {calories} ккал | 🥩 {protein}г | 🧈 {fat}г | 🍞 {carbs}г на 100 г\n\n"
        "Сколько граммов ты съел?"
    )
    return ENTER_WEIGHT

async def enter_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        weight = float(update.message.text.replace(',', '.'))
    except:
        await update.message.reply_text("❌ Введи число (граммы). Например: 150")
        return ENTER_WEIGHT
    
    # Получаем продукт
    product_id = context.user_data['product_id']
    async with db.pool.acquire() as conn:
        product = await conn.fetchrow('SELECT * FROM products WHERE id = $1', product_id)
    
    # Пересчитываем КБЖУ на вес
    calories = (product['calories'] / 100) * weight
    protein = (product['protein'] / 100) * weight
    fat = (product['fat'] / 100) * weight
    carbs = (product['carbs'] / 100) * weight
    
    # Сохраняем запись
    await db.add_meal_entry(
        user_id=update.effective_user.id,
        product_id=product_id,
        meal_type_id=context.user_data['meal_type_id'],
        weight=weight,
        calories=calories,
        protein=protein,
        fat=fat,
        carbs=carbs
    )
    
    await update.message.reply_text(
        format_nutrition(product['name'], weight, calories, protein, fat, carbs)
    )
    
    context.user_data.clear()
    return ConversationHandler.END

# --- ИСТОРИЯ ---

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    # Определяем дату
    if args and len(args) > 0:
        try:
            target_date = datetime.strptime(args[0], '%Y-%m-%d').date()
        except:
            await update.message.reply_text(
                "❌ Неверный формат. Используй: /history YYYY-MM-DD"
            )
            return
    else:
        target_date = date.today()
    
    entries = await db.get_daily_summary(user_id, target_date)
    totals = await db.get_daily_totals(user_id, target_date)
    
    response = f"📅 Сводка за {target_date.strftime('%d.%m.%Y')}\n\n"
    response += format_history(entries)
    
    # Добавляем условную норму (можно хранить в БД)
    response += "\n\n🎯 Ваша норма (примерная): 1800 ккал"
    
    await update.message.reply_text(response)

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    end_date = date.today()
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
    
    await update.message.reply_text(response)

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
        target_date = date.today()
    
    entries = await db.get_day_entries(user_id, target_date)
    
    if not entries:
        await update.message.reply_text(f"📭 За {target_date.strftime('%d.%m.%Y')} записей нет.")
        return
    
    # Создаём CSV в памяти
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

# --- ГЛАВНАЯ ФУНКЦИЯ ---

def main():
    # Подключаемся к БД
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(db.connect())
    loop.run_until_complete(db.create_tables())
    
    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # ConversationHandler для добавления продукта
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_start)],
        states={
            SELECT_MEAL: [CallbackQueryHandler(select_meal, pattern='^meal_')],
            ENTER_PRODUCT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_product),
                MessageHandler(filters.PHOTO, enter_product)
            ],
            SELECT_PRODUCT_FROM_LIST: [
                CallbackQueryHandler(select_product, pattern='^prod_')
            ],
            MANUAL_ENTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_entry)
            ],
            ENTER_WEIGHT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_weight)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel),
                  CommandHandler('add', add_start)]
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('history', history))
    app.add_handler(CommandHandler('week', week))
    app.add_handler(CommandHandler('export', export_csv))
    app.add_handler(CommandHandler('cancel', cancel))
    
    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == '__main__':
    main()
