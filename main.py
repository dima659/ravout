import asyncio
import contextlib
import io
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from .core import DATA, ROOT, TOKEN, DEV, MANAGER_ID, ADMIN_IDS, WEBAPP_URL, db, validate_init_data

log = logging.getLogger('ravout')
STATUSES = {'new':'Принята', 'working':'В работе', 'done':'Завершена', 'cancelled':'Отменена'}

async def current_user(request: Request, x_telegram_init_data: str = Header(default='')):
    if DEV and request.client and request.client.host in {'127.0.0.1', '::1', 'testclient'} and not x_telegram_init_data:
        return {'id': 1, 'first_name': 'Демо', 'username': 'demo_user'}
    try:
        return validate_init_data(x_telegram_init_data)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, 'Откройте приложение через Telegram. Если сессия истекла — перезапустите его.')

async def deliver(bot, request_id):
    from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
    with db() as c:
        r = dict(c.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone())
        photos = c.execute('SELECT filename FROM photos WHERE request_id=? ORDER BY id', (request_id,)).fetchall()
    text = (f'Новая заявка №{r["id"]}\n'
            f'Клиент: {r["first_name"]} (@{r["username"]})\nTelegram ID: {r["user_id"]}\n'
            f'Описание: {r["description"] or "Только фото"}\nСсылка: {r["link"] or "—"}\n'
            f'Размер: {r["size"] or "—"}\nЦвет: {r["color"] or "—"}\n'
            f'Бюджет: {r["budget"] or "—"}\nКоличество: {r["quantity"]}\n'
            f'Товар: {r["product_id"] or "Подбор по фото / описанию"}')
    # Фотографии отправляются до сообщения с кнопками. Повторная доставка возможна
    # при сетевом сбое после принятия сообщения Telegram (at-least-once).
    for p in photos:
        await bot.send_photo(MANAGER_ID, FSInputFile(DATA / 'private' / p['filename']), caption=f'Фото к заявке №{request_id}')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Открыть клиента', url=f'tg://user?id={r["user_id"]}')],
        [InlineKeyboardButton(text='В работу', callback_data=f'status:{request_id}:working'),
         InlineKeyboardButton(text='Завершить', callback_data=f'status:{request_id}:done')],
        [InlineKeyboardButton(text='Отменить', callback_data=f'status:{request_id}:cancelled')]])
    await bot.send_message(MANAGER_ID, text, reply_markup=keyboard)

async def outbox_loop():
    from aiogram import Bot
    async with Bot(TOKEN) as bot:
        while True:
            with db() as c:
                jobs = c.execute('SELECT request_id, attempts FROM outbox WHERE delivered=0 AND next_attempt<=? LIMIT 5', (int(time.time()),)).fetchall()
            for job in jobs:
                try:
                    await deliver(bot, job['request_id'])
                    with db() as c:
                        c.execute('UPDATE outbox SET delivered=1,last_error=NULL WHERE request_id=?', (job['request_id'],))
                except Exception as e:
                    # Не логируем персональные данные, токены и текст исключения HTTP-клиента.
                    log.warning('Delivery failed for request %s (%s)', job['request_id'], type(e).__name__)
                    with db() as c:
                        c.execute('UPDATE outbox SET attempts=attempts+1,next_attempt=?,last_error=? WHERE request_id=?',
                                  (int(time.time()) + min(3600, 15 * 2**min(job['attempts'], 8)), type(e).__name__, job['request_id']))
            await asyncio.sleep(5)

async def polling_loop():
    from .bot import main as run_bot
    while True:
        try:
            await run_bot(handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error('Telegram polling interrupted (%s); retry in 15s', type(exc).__name__)
        await asyncio.sleep(15)

@asynccontextmanager
async def lifespan(app):
    if not DEV and (not TOKEN or not MANAGER_ID or not ADMIN_IDS):
        raise RuntimeError('Set BOT_TOKEN, MANAGER_CHAT_ID and ADMIN_IDS in Render Environment.')
    run_bot = os.getenv('RUN_BOT', 'true').lower() == 'true' and not DEV
    if run_bot and (not WEBAPP_URL.startswith('https://') or not urlsplit(WEBAPP_URL).netloc):
        raise RuntimeError('Set WEBAPP_URL to your public https://...onrender.com address.')
    tasks = []
    if TOKEN and MANAGER_ID:
        tasks.append(asyncio.create_task(outbox_loop()))
    if run_bot:
        tasks.append(asyncio.create_task(polling_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(title='RAVOUT SHMOT', lifespan=lifespan, docs_url=None, redoc_url=None)

@app.middleware('http')
async def security_headers(request, call_next):
    if request.method == 'POST' and request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 17 * 1024 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Максимум 3 фотографии по 5 МБ'}, status_code=413)
    if request.url.path == '/api/requests' and request.method == 'POST':
        chunks = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > 17 * 1024 * 1024:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'Максимум 3 фотографии по 5 МБ'}, status_code=413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/health')
def health():
    with db() as c:
        c.execute('SELECT 1').fetchone()
    return {'ok':True}

@app.get('/api/me')
def me(user=Depends(current_user)):
    return {**user, 'dev':DEV, 'manager_enabled':bool(TOKEN and MANAGER_ID)}

@app.get('/api/products')
def products(q: str='', category: str='', favorites: bool=False, user=Depends(current_user)):
    if len(q)>100 or len(category)>40:
        raise HTTPException(422, 'Слишком длинный поисковый запрос')
    sql = '''SELECT p.*, EXISTS(SELECT 1 FROM favorites f WHERE f.product_id=p.id AND f.user_id=?) AS favorite
             FROM products p WHERE active=1'''
    args = [user['id']]
    if q:
        sql += ' AND (CASEFOLD(p.title) LIKE ? OR CASEFOLD(p.description) LIKE ?)'
        args += ['%'+q.casefold()+'%']*2
    if category:
        sql += ' AND p.category=?'
        args.append(category)
    if favorites:
        sql += ' AND EXISTS(SELECT 1 FROM favorites f WHERE f.product_id=p.id AND f.user_id=?)'
        args.append(user['id'])
    with db() as c:
        rows=c.execute(sql+' ORDER BY p.id DESC LIMIT 200', args).fetchall()
    return [dict(r) for r in rows]

@app.put('/api/favorites/{product_id}')
async def favorite(product_id:int, request:Request, user=Depends(current_user)):
    data = await request.json()
    if type(data.get('favorite')) is not bool:
        raise HTTPException(422,'Нужно передать favorite: true или false')
    with db() as c:
        if not c.execute('SELECT 1 FROM products WHERE id=? AND active=1',(product_id,)).fetchone():
            raise HTTPException(404,'Товар не найден')
        if data['favorite']:
            c.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)',(user['id'],product_id))
        else:
            c.execute('DELETE FROM favorites WHERE user_id=? AND product_id=?',(user['id'],product_id))
    return {'favorite':data['favorite']}

@app.get('/api/requests')
def requests_list(user=Depends(current_user)):
    with db() as c:
        rows=c.execute('SELECT * FROM requests WHERE user_id=? ORDER BY id DESC LIMIT 100',(user['id'],)).fetchall()
        result=[]
        for row in rows:
            r=dict(row)
            r.pop('idempotency',None)
            r['status_label']=STATUSES[r['status']]
            r['photos']=[p['id'] for p in c.execute('SELECT id FROM photos WHERE request_id=?',(r['id'],))]
            r['delivered']=bool(c.execute('SELECT delivered FROM outbox WHERE request_id=?',(r['id'],)).fetchone()['delivered'])
            result.append(r)
        return result

@app.get('/api/photos/{photo_id}')
def private_photo(photo_id:str,user=Depends(current_user)):
    with db() as c:
        p=c.execute('SELECT p.filename,r.user_id FROM photos p JOIN requests r ON r.id=p.request_id WHERE p.id=?',(photo_id,)).fetchone()
    if not p or (p['user_id']!=user['id'] and user['id'] not in ADMIN_IDS):
        raise HTTPException(404,'Фото не найдено')
    return FileResponse(DATA/'private'/p['filename'], media_type='image/jpeg', headers={'Cache-Control':'no-store'})

@app.post('/api/requests', status_code=201)
async def create_request(request:Request, user=Depends(current_user)):
    async with request.form(max_files=3,max_fields=16,max_part_size=6*1024*1024) as form:
        key=str(form.get('idempotency',''))
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',key):
            raise HTTPException(422,'Нет ключа запроса. Обновите приложение.')
        with db() as c:
            old=c.execute('SELECT id FROM requests WHERE user_id=? AND idempotency=?',(user['id'],key)).fetchone()
            if old:
                return {'id':old['id']}
        def text(name,limit):
            value=str(form.get(name,'')).strip()
            if len(value)>limit:
                raise HTTPException(422,f'Поле {name}: максимум {limit} символов')
            return value
        description=text('description',1200)
        link=text('link',500)
        size=text('size',40); color=text('color',40); budget=text('budget',40)
        username=text('username',32).lstrip('@') or user.get('username','')
        if username and not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):
            raise HTTPException(422,'Username: 5–32 латинских символа, цифры или _')
        if link and (urlsplit(link).scheme not in {'https','http'} or not urlsplit(link).netloc):
            raise HTTPException(422,'Ссылка должна начинаться с https:// или http://')
        try:
            quantity=int(form.get('quantity',1)); product_id=int(form['product_id']) if form.get('product_id') else None
            if not 1<=quantity<=100:
                raise ValueError()
        except (ValueError,TypeError):
            raise HTTPException(422,'Количество должно быть от 1 до 100')
        uploads=form.getlist('photos')
        if len(uploads)>3:
            raise HTTPException(422,'Не более 3 фотографий')
        if not description and not uploads and not product_id:
            raise HTTPException(422,'Добавьте фото или описание')
        images=[]
        for upload in uploads:
            if not hasattr(upload,'read'):
                raise HTTPException(422,'Неверный формат фотографии')
            raw=await upload.read(5*1024*1024+1)
            if len(raw)>5*1024*1024:
                raise HTTPException(413,'Каждая фотография должна быть не больше 5 МБ')
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in {'JPEG','PNG','WEBP'} or im.width*im.height>25_000_000:
                        raise ValueError()
                    im=ImageOps.exif_transpose(im).convert('RGB')
                    im.thumbnail((1800,1800))
                    output=io.BytesIO(); im.save(output,format='JPEG',quality=88)
                    images.append((uuid.uuid4().hex,output.getvalue()))
            except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
                raise HTTPException(422,'Разрешены только изображения JPG, PNG или WebP до 25 мегапикселей')
        written=[]
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                old=c.execute('SELECT id FROM requests WHERE user_id=? AND idempotency=?',(user['id'],key)).fetchone()
                if old:
                    return {'id':old['id']}
                if c.execute('SELECT COUNT(*) FROM requests WHERE user_id=? AND created_at>?',(user['id'],int(time.time())-3600)).fetchone()[0]>=10:
                    raise HTTPException(429,'Максимум 10 заявок в час. Попробуйте позже.')
                if product_id and not c.execute('SELECT 1 FROM products WHERE id=? AND active=1',(product_id,)).fetchone():
                    raise HTTPException(404,'Товар больше не доступен')
                cur=c.execute('''INSERT INTO requests(user_id,username,first_name,description,link,size,color,budget,quantity,product_id,created_at,idempotency)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(user['id'],username,user.get('first_name',''),description,link,size,color,budget,quantity,product_id,int(time.time()),key))
                rid=cur.lastrowid
                for pid,raw in images:
                    filename=pid+'.jpg'; path=DATA/'private'/filename
                    path.write_bytes(raw); written.append(path)
                    c.execute('INSERT INTO photos VALUES (?,?,?)',(pid,rid,filename))
                c.execute('INSERT INTO outbox(request_id) VALUES (?)',(rid,))
        except Exception:
            for p in written:
                p.unlink(missing_ok=True)
            raise
        return {'id':rid}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
app.mount('/product-images',StaticFiles(directory=DATA/'products'),name='product-images')

@app.get('/')
def index():
    return FileResponse(ROOT/'static'/'index.html', headers={'Cache-Control':'no-cache'})
