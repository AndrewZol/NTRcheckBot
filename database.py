import asyncpg
import aiohttp
import json
from datetime import datetime, date
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
            # Таблица пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица продуктов (id - BIGSERIAL)
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
            
            # Типы приёмов пищи
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS meal_types (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE
                )
            ''')
            
            # Добавляем типы приёмов, если их нет
            await conn.execute('''
                INSERT INTO meal_types (name) VALUES 
                    ('Завтрак'), ('Второй завтрак'), ('Обед'), 
                    ('Полдник'), ('Ужин'), ('Поздний ужин')
                ON CONFLICT (name) DO NOTHING
            ''')
            
            # Таблица записей о приёмах пищи (product_id - BIGINT)
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

    # Регистрация пользователя
    async def register_user(self, user_id: int, username: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, username) 
                VALUES ($1, $2) 
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id, username)

    # Поиск продукта по штрих-коду (локальная БД)
    async def find_product_by_barcode(self, barcode: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                'SELECT * FROM products WHERE barcode = $1', barcode
            )

    # Поиск продукта по названию (локальная БД)
    async def find_products_by_name(self, name: str):
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                'SELECT * FROM products WHERE name ILIKE $1 LIMIT 5',
                f'%{name}%'
            )

    # Добавление нового продукта
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

    # Сохранение приёма пищи
   async def add_meal_entry(self, user_id: int, product_id: int, 
                         meal_type_id: int, weight: float,
                         calories: float, protein: float, 
                         fat: float, carbs: float):
    # Получаем московскую дату
    from datetime import datetime, timedelta
    moscow_time = datetime.utcnow() + timedelta(hours=3)
    moscow_date = moscow_time.date()
    
    async with self.pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO meal_entries 
            (user_id, product_id, meal_type_id, date, weight_grams, 
             calories, protein, fat, carbs)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ''', user_id, product_id, meal_type_id, moscow_date, weight, 
             calories, protein, fat, carbs)

    # Получение сводки за день
    async def get_daily_summary(self, user_id: int, target_date: date):
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

    # Получение суммарного КБЖУ за день
    async def get_daily_totals(self, user_id: int, target_date: date):
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

    # Получение всех записей за день для CSV
    async def get_day_entries(self, user_id: int, target_date: date):
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

    # Получение типов приёмов пищи
    async def get_meal_types(self):
        async with self.pool.acquire() as conn:
            return await conn.fetch('SELECT * FROM meal_types ORDER BY id')

    # =====================================================
    # НОВЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С OPEN FOOD FACTS API
    # =====================================================

    async def search_product_by_name(self, product_name: str):
        """Ищет продукты по названию через Open Food Facts API."""
        from urllib.parse import quote
        encoded_name = quote(product_name)
        url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={encoded_name}*&search_simple=1&action=process&json=1&page_size=5&lc=ru"
        
        print(f"🔍 Запрос к API: {url}")
        
        headers = {
            "User-Agent": "Nutricheckbot/1.0 (merimeeev@gmail.com)"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    print(f"📡 Статус ответа API: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        products = data.get('products', [])
                        print(f"📦 Найдено продуктов: {len(products)}")
                        formatted_products = []
                        for p in products:
                            nutriments = p.get('nutriments', {})
                            product_id = p.get('_id', 0)
                            name = p.get('product_name_ru') or p.get('product_name', 'Без названия')
                            formatted_products.append({
                                'id': str(product_id),
                                'name': name,
                                'calories': nutriments.get('energy-kcal_100g', 0) or nutriments.get('energy_100g', 0),
                                'protein': nutriments.get('proteins_100g', 0),
                                'fat': nutriments.get('fat_100g', 0),
                                'carbs': nutriments.get('carbohydrates_100g', 0),
                                'barcode': str(product_id) if product_id else '',
                            })
                        return formatted_products
                    else:
                        print(f"⚠️ API вернул статус: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Ошибка при поиске в Open Food Facts: {e}")
            return []

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
    # НОВЫЙ МЕТОД ДЛЯ РАБОТЫ С DEEPSEEK API (РЕЗЕРВНЫЙ)
    # =====================================================

    async def search_product_by_deepseek(self, product_name: str):
        """Ищет КБЖУ продукта через DeepSeek API (резервный источник)."""
        import os
        
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("⚠️ DEEPSEEK_API_KEY не найден в переменных окружения")
            return []
        
        url = "https://api.deepseek.com/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        prompt = f"""Ты — помощник по питанию. Пользователь ищет КБЖУ продукта: "{product_name}".

Твоя задача:
1. Если ты знаешь точные данные для этого продукта — верни их.
2. Если точных данных нет, но есть данные для похожего продукта или общего типа (например, "яблоко" для "яблоко Гала") — используй их.
3. Если данных совсем нет — дай разумную оценку, указав, что это приблизительное значение.

Верни ответ в строгом JSON формате. Используй поле "source" для указания источника:
- "source": "known" — если ты уверен в данных
- "source": "estimated" — если это приблизительная оценка

Пример ответа для точных данных:
[
    {{
        "id": "deepseek_1",
        "name": "Яблоко Гала",
        "calories": 47.0,
        "protein": 0.4,
        "fat": 0.4,
        "carbs": 9.8,
        "barcode": "",
        "source": "known"
    }}
]

Пример ответа для приблизительных данных:
[
    {{
        "id": "deepseek_1",
        "name": "Яблоко (среднее)",
        "calories": 52.0,
        "protein": 0.3,
        "fat": 0.2,
        "carbs": 14.0,
        "barcode": "",
        "source": "estimated"
    }}
]

ВАЖНО: Всегда давай ответ в JSON массиве, даже если это оценка. Не добавляй пояснений вне JSON."""
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты — помощник по питанию. Отвечай только JSON массивами с полем source."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
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
