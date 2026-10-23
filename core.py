from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'private').mkdir(exist_ok=True)
(DATA / 'products').mkdir(exist_ok=True)
TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()}
MANAGER_ID = int(os.getenv('MANAGER_CHAT_ID', '0'))
DEV = os.getenv('DEV_MODE', 'false').lower() == 'true'
WEBAPP_URL = os.getenv('WEBAPP_URL', '')

@contextmanager
def db():
    con = sqlite3.connect(DATA / 'shop.sqlite3', timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.create_function('CASEFOLD', 1, lambda x: str(x or '').casefold(), deterministic=True)
    try:
        with con:
            yield con
    finally:
        con.close()

def init_db():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>=0),
          category TEXT NOT NULL, image TEXT, description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS favorites (
          user_id INTEGER NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id), PRIMARY KEY(user_id,product_id));
        CREATE TABLE IF NOT EXISTS requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT NOT NULL,
          first_name TEXT NOT NULL, description TEXT NOT NULL, link TEXT NOT NULL,
          size TEXT NOT NULL, color TEXT NOT NULL, budget TEXT NOT NULL, quantity INTEGER NOT NULL,
          product_id INTEGER REFERENCES products(id), status TEXT NOT NULL DEFAULT 'new',
          created_at INTEGER NOT NULL, idempotency TEXT NOT NULL, UNIQUE(user_id,idempotency));
        CREATE TABLE IF NOT EXISTS photos (
          id TEXT PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id), filename TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
          request_id INTEGER PRIMARY KEY REFERENCES requests(id), attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0, last_error TEXT);
        ''')

def validate_init_data(raw, token=TOKEN, now=None):
    pairs = parse_qsl(raw, keep_blank_values=True)
    if len(pairs) != len(dict(pairs)):
        raise ValueError('Повторяющиеся поля авторизации')
    fields = dict(pairs)
    supplied = fields.pop('hash', '')
    if not token or not supplied:
        raise ValueError('Откройте приложение через Telegram')
    check = '\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    digest = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, supplied):
        raise ValueError('Недействительная подпись Telegram')
    age = (int(time.time()) if now is None else now) - int(fields.get('auth_date', '0'))
    if age < -30 or age > 86400:
        raise ValueError('Сессия истекла. Закройте и снова откройте приложение')
    user = json.loads(fields.get('user', '{}'))
    if not isinstance(user, dict) or not isinstance(user.get('id'), int) or user['id'] <= 0:
        raise ValueError('Нет пользователя Telegram')
    return user

init_db()
