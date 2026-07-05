import asyncpg
from datetime import datetime, date
from config import DATABASE_URL

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)

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
            
            # Таблица продуктов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
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
            
            # Таблица записей о приёмах пищи
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS meal_entries (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    product_id INTEGER REFERENCES products(id),
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

    # Поиск продукта по штрих-коду
    async def find_product_by_barcode(self, barcode: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                'SELECT * FROM products WHERE barcode = $1', barcode
            )

    # Поиск продукта по названию
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
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO meal_entries 
                (user_id, product_id, meal_type_id, date, weight_grams, 
                 calories, protein, fat, carbs)
                VALUES ($1, $2, $3, CURRENT_DATE, $4, $5, $6, $7, $8)
            ''', user_id, product_id, meal_type_id, weight, 
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
