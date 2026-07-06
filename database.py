import asyncpg
import aiohttp
import json
from datetime import datetime, timedelta
from config import DATABASE_URL

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            DATABASE_URL,
            timeout=10.0,
            max_inactive_connection_lifetime=300.0,
            statement_cache_size=0
        )

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id BIGSERIAL PRIMARY KEY,
                    barcode TEXT UNIQUE,
                    name TEXT NOT NULL,
                    calories REAL,
                    protein REAL,
                    fat REAL,
                    carbs REAL,
                    is_custom BOOLEAN DEFAULT FALSE
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS meal_types (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE
                )
            ''')
            
            await conn.execute('''
                INSERT INTO meal_types (name) VALUES 
                    ('Завтрак'), ('Второй завтрак'), ('Обед'), 
                    ('Полдник'), ('Ужин'), ('Поздний ужин')
                ON CONFLICT (name) DO NOTHING
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS meal_entries (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    product_id BIGINT REFERENCES products(id),
                    meal_type_id INTEGER REFERENCES meal_types(id),
                    date DATE DEFAULT CURRENT_DATE,
                    weight_grams REAL,
                    calories REAL,
                    protein REAL,
                    fat REAL,
                    carbs REAL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    async def register_user(self, user_id: int, username: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, username) 
                VALUES ($1, $2) 
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id, username)

    async def find_product_by_barcode(self, barcode: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                'SELECT * FROM products WHERE barcode = $1', barcode
            )

    async def find_products_by_name(self, name: str):
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                'SELECT * FROM products WHERE name ILIKE $1 LIMIT 5',
                f'%{name}%'
            )

    async def add_product(self, name: str, barcode: str = None, 
                           calories: float = 0, protein: float = 0, 
                           fat: float = 0, carbs: float = 0, 
                           is_custom: bool = False):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow('''
                INSERT INTO products (barcode, name, calories, protein, fat, carbs, is_custom)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (barcode) DO UPDATE SET 
                    name = EXCLUDED.name,
                    calories = EXCLUDED.calories,
                    protein = EXCLUDED.protein,
                    fat = EXCLUDED.fat,
                    carbs = EXCLUDED.carbs
                RETURNING id
            ''', barcode, name, calories, protein, fat, carbs, is_custom)

    async def add_meal_entry(self, user_id: int, product_id: int, 
                             meal_type_id: int, weight: float,
                             calories: float, protein: float, 
                             fat: float, carbs: float):
        """Сохраняет запись о приёме пищи с московским временем."""
        print("=" * 50)
        print("💾 [DB] СОХРАНЕНИЕ ЗАПИСИ")
        print(f"💾 user_id: {user_id}")
        print(f"💾 product_id: {product_id}")
        print(f"💾 meal_type_id: {meal_type_id}")
        print(f"💾 weight: {weight}")
        print(f"💾 calories: {calories}")
        print(f"💾 protein: {protein}")
        print(f"💾 fat: {fat}")
        print(f"💾 carbs: {carbs}")
        
        # Московское время (UTC+3)
        moscow_time = datetime.utcnow() + timedelta(hours=3)
        moscow_date = moscow_time.date()
        print(f"💾 Московская дата: {moscow_date}")
        
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute('''
                    INSERT INTO meal_entries 
                    (user_id, product_id, meal_type_id, date, weight_grams, 
                     calories, protein, fat, carbs)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ''', user_id, product_id, meal_type_id, moscow_date, weight, 
                     calories, protein, fat, carbs)
                print(f"✅ [DB] Запись успешно сохранена! Result: {result}")
                return result
        except Exception as e:
            print(f"❌ [DB] ОШИБКА сохранения: {e}")
            raise  # Пробрасываем ошибку дальше, чтобы бот мог её обработать

    async def get_daily_summary(self, user_id: int, target_date):
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT 
                    mt.name as meal_type,
                    p.name as product_name,
                    me.weight_grams,
                    me.calories,
                    me.protein,
                    me.fat,
                    me.carbs
                FROM meal_entries me
                JOIN products p ON me.product_id = p.id
                JOIN meal_types mt ON me.meal_type_id = mt.id
                WHERE me.user_id = $1 AND me.date = $2
                ORDER BY mt.id, me.created_at
            ''', user_id, target_date)

    async def get_daily_totals(self, user_id: int, target_date):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow('''
                SELECT 
                    COALESCE(SUM(calories), 0) as total_calories,
                    COALESCE(SUM(protein), 0) as total_protein,
                    COALESCE(SUM(fat), 0) as total_fat,
                    COALESCE(SUM(carbs), 0) as total_carbs
                FROM meal_entries
                WHERE user_id = $1 AND date = $2
            ''', user_id, target_date)

    async def get_day_entries(self, user_id: int, target_date):
        async with self.pool.acquire() as conn:
            return await conn.fetch('''
                SELECT 
                    me.date,
                    mt.name as meal_type,
                    p.name as product_name,
                    me.weight_grams,
                    me.calories,
                    me.protein,
                    me.fat,
                    me.carbs
                FROM meal_entries me
                JOIN products p ON me.product_id = p.id
                JOIN meal_types mt ON me.meal_type_id = mt.id
                WHERE me.user_id = $1 AND me.date = $2
                ORDER BY mt.id, me.created_at
            ''', user_id, target_date)

    async def get_meal_types(self):
        async with self.pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM meal_types ORDER BY id')

    # =====================================================
    # ПОИСК В OPEN FOOD FACTS
    # =====================================================

    async def search_product_by_name(self, product_name: str):
        """Ищет продукты по названию через Open Food Facts API."""
        from urllib.parse import quote
        
        words = product_name.split()
        
        # Пробуем несколько вариантов поиска
        search_queries = [
            f"{product_name}*",
            f"{product_name}",
        ]
        
        if len(words) > 1:
            search_queries.append(f"{words[0]} {words[-1]}*")
            search_queries.append(f"{words[0]}*")
        
        # Английские переводы
        eng_translations = {
            "курица": "chicken",
            "грудка": "breast",
            "филе": "fillet",
            "говядина": "beef",
            "свинина": "pork",
            "рыба": "fish",
            "лосось": "salmon",
            "творог": "cottage cheese",
            "йогурт": "yogurt",
            "молоко": "milk",
            "сыр": "cheese",
            "масло": "butter",
            "яйцо": "egg",
            "хлеб": "bread",
            "рис": "rice",
            "гречка": "buckwheat",
            "овсянка": "oatmeal",
            "макароны": "pasta",
        }
        
        if words[0].lower() in eng_translations:
            eng_word = eng_translations[words[0].lower()]
            if len(words) > 1:
                search_queries.append(f"{eng_word} {words[-1]}*")
            else:
                search_queries.append(f"{eng_word}*")
        
        # Удаляем дубликаты
        search_queries = list(dict.fromkeys(search_queries))
        
        headers = {
            "User-Agent": "Nutricheckbot/1.0 (merimeeev@gmail.com)"
        }
        
        all_products = []
        seen_ids = set()
        
        for query in search_queries[:3]:
            encoded_query = quote(query)
            url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={encoded_query}&search_simple=1&action=process&json=1&page_size=10&lc=ru"
            
            print(f"🔍 Запрос к API: {url}")
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            products = data.get('products', [])
                            print(f"📦 Найдено продуктов: {len(products)}")
                            
                            for p in products:
                                product_id = p.get('_id', 0)
                                if product_id in seen_ids:
                                    continue
                                seen_ids.add(product_id)
                                
                                nutriments = p.get('nutriments', {})
                                name = p.get('product_name_ru') or p.get('product_name', 'Без названия')
                                
                                if len(words) > 1:
                                    name_lower = name.lower()
                                    if not any(word.lower() in name_lower for word in words):
                                        continue
                                
                                all_products.append({
                                    'id': str(product_id),
                                    'name': name,
                                    'calories': nutriments.get('energy-kcal_100g', 0) or nutriments.get('energy_100g', 0),
                                    'protein': nutriments.get('proteins_100g', 0),
                                    'fat': nutriments.get('fat_100g', 0),
                                    'carbs': nutriments.get('carbohydrates_100g', 0),
                                    'barcode': str(product_id) if product_id else '',
                                })
                                
                                if len(all_products) >= 10:
                                    break
                        else:
                            print(f"⚠️ API вернул статус: {response.status}")
            except Exception as e:
                print(f"❌ Ошибка при поиске в Open Food Facts: {e}")
            
            if len(all_products) >= 10:
                break
        
        print(f"📦 Всего уникальных продуктов: {len(all_products)}")
        return all_products[:10]

    async def search_product_by_barcode(self, barcode: str):
        """Ищет продукт по штрих-коду через Open Food Facts API."""
        url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
        
        print(f"🔍 Запрос по штрих-коду: {url}")
        
        headers = {
            "User-Agent": "Nutricheckbot/1.0 (merimeeev@gmail.com)"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    print(f"📡 Статус ответа API (штрих-код): {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 1:
                            p = data['product']
                            nutriments = p.get('nutriments', {})
                            product_id = p.get('_id', 0)
                            name = p.get('product_name_ru') or p.get('product_name', 'Без названия')
                            return [{
                                'id': str(product_id),
                                'name': name,
                                'calories': nutriments.get('energy-kcal_100g', 0) or nutriments.get('energy_100g', 0),
                                'protein': nutriments.get('proteins_100g', 0),
                                'fat': nutriments.get('fat_100g', 0),
                                'carbs': nutriments.get('carbohydrates_100g', 0),
                                'barcode': barcode,
                            }]
                    return []
        except Exception as e:
            print(f"❌ Ошибка при поиске по штрих-коду: {e}")
            return []

    # =====================================================
    # ПОИСК В DEEPSEEK
    # =====================================================

    async def search_product_by_deepseek(self, product_name: str):
    """Ищет КБЖУ продукта через DeepSeek API (основной источник)."""
    import os
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("⚠️ DEEPSEEK_API_KEY не найден")
        return []
    
    url = "https://api.deepseek.com/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""Ты — эксперт по питанию. Пользователь ищет КБЖУ продукта: "{product_name}".

Верни **точные средние значения** на 100 г продукта в строгом JSON формате. НЕ используй диапазоны (например, "100-120"). Дай конкретные числа.

Если точных данных нет — дай **усреднённые значения** на основе типичных продуктов этой категории.

Пример ответа:
[
    {{
        "id": "deepseek_1",
        "name": "Куриная грудка варёная",
        "calories": 165,
        "protein": 31,
        "fat": 3.6,
        "carbs": 0,
        "barcode": ""
    }}
]

Важно:
- calories, protein, fat, carbs — это числа (не строки).
- Не используй диапазоны.
- Не добавляй пояснений вне JSON.
- Если продукт имеет несколько вариантов (жареный/варёный) — выбери самый распространённый."""
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Ты — эксперт по питанию. Отвечай только JSON массивами с конкретными числами. Никаких диапазонов."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,  # <-- НИЗКАЯ ТЕМПЕРАТУРА ДЛЯ ТОЧНОСТИ
        "max_tokens": 500
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '[]')
                    print(f"🤖 DeepSeek ответ: {content[:200]}...")
                    
                    try:
                        result = json.loads(content)
                        if isinstance(result, list):
                            return result
                        else:
                            return [result]
                    except json.JSONDecodeError:
                        print(f"❌ Не удалось распарсить JSON из DeepSeek: {content}")
                        return []
                else:
                    print(f"❌ DeepSeek API ошибка: {response.status}")
                    return []
    except Exception as e:
        print(f"❌ Ошибка при запросе к DeepSeek: {e}")
        return []
