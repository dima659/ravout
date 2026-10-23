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

from fastapi import FastAPI, Depends, Header, HTTPException, Request, Query
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
    if request.method in {'POST', 'PUT'} and request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 17 * 1024 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Максимум 3 фотографии по 5 МБ'}, status_code=413)
    if (request.url.path == '/api/requests' or request.url.path.startswith('/api/admin/')) and request.method in {'POST','PUT'}:
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
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; base-uri 'self'; form-action 'self'"
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
    return {**user, 'dev':DEV, 'is_admin':user['id'] in ADMIN_IDS, 'manager_enabled':bool(TOKEN and MANAGER_ID)}

CATEGORIES = ['Одежда', 'Обувь', 'Сумки', 'Аксессуары']

def product_json(row):
    p = dict(row)
    p['sizes'] = json.loads(p.get('sizes') or '[]')
    p['colors'] = json.loads(p.get('colors') or '[]')
    return p

@app.get('/api/catalog-filters')
def catalog_filters(user=Depends(current_user)):
    with db() as c:
        rows = c.execute('SELECT category,brand,sizes,colors FROM products WHERE active=1').fetchall()
    return {'categories': CATEGORIES,
            'brands': sorted({r['brand'] for r in rows if r['brand']}, key=str.casefold),
            'sizes': sorted({v for r in rows for v in json.loads(r['sizes'])}, key=str.casefold),
            'colors': sorted({v for r in rows for v in json.loads(r['colors'])}, key=str.casefold)}

@app.get('/api/products')
def products(q: str='', category: str='', favorites: bool=False,
             brand: str='', size: str='', color: str='',
             min_price: int=Query(default=0,ge=0,le=100000000),
             max_price: int=Query(default=100000000,ge=0,le=100000000),
             sort: str='newest', limit: int=Query(default=40,ge=1,le=200),
             offset: int=Query(default=0,ge=0,le=100000), user=Depends(current_user)):
    if len(q)>100 or len(category)>40 or any(len(v)>80 for v in [brand,size,color]):
        raise HTTPException(422, 'Слишком длинный поисковый запрос')
    orders = {'newest':'p.id DESC', 'price_asc':'p.price ASC,p.id DESC',
              'price_desc':'p.price DESC,p.id DESC', 'title':'CASEFOLD(p.title) ASC,p.id DESC'}
    if sort not in orders or min_price>max_price:
        raise HTTPException(422, 'Проверьте порядок сортировки и диапазон цены')
    sql = """SELECT p.*, EXISTS(SELECT 1 FROM favorites f WHERE f.product_id=p.id AND f.user_id=?) AS favorite
             FROM products p WHERE active=1 AND p.price>=? AND p.price<=?"""
    args = [user['id'],min_price,max_price]
    if q:
        sql += ' AND (CASEFOLD(p.title) LIKE ? OR CASEFOLD(p.description) LIKE ? OR CASEFOLD(p.brand) LIKE ?)'
        args += ['%'+q.casefold()+'%']*3
    if category:
        sql += ' AND p.category=?'; args.append(category)
    if brand:
        sql += ' AND CASEFOLD(p.brand)=?'; args.append(brand.casefold())
    for field,value in [('sizes',size),('colors',color)]:
        if value:
            sql += f' AND EXISTS(SELECT 1 FROM json_each(p.{field}) WHERE CASEFOLD(value)=?)'
            args.append(value.casefold())
    if favorites:
        sql += ' AND EXISTS(SELECT 1 FROM favorites f WHERE f.product_id=p.id AND f.user_id=?)'
        args.append(user['id'])
    with db() as c:
        rows=c.execute(sql+' ORDER BY '+orders[sort]+' LIMIT ? OFFSET ?', args+[limit,offset]).fetchall()
    return [product_json(r) for r in rows]

async def require_admin(user=Depends(current_user)):
    if user['id'] not in ADMIN_IDS:
        raise HTTPException(403,'Доступ только для администратора магазина')
    return user

@app.get('/api/admin/products')
def admin_products(q: str='', visibility: str='all',
                   offset: int=Query(default=0,ge=0,le=100000), user=Depends(require_admin)):
    if len(q)>100 or visibility not in {'all','active','hidden'}:
        raise HTTPException(422,'Некорректный фильтр')
    where='WHERE 1=1';args=[]
    if q:
        where+=' AND (CASEFOLD(title) LIKE ? OR CASEFOLD(brand) LIKE ?)'
        args+=['%'+q.casefold()+'%']*2
    if visibility!='all':
        where+=' AND active=?';args.append(int(visibility=='active'))
    with db() as c:
        total=c.execute('SELECT COUNT(*) FROM products '+where,args).fetchone()[0]
        rows=c.execute('SELECT * FROM products '+where+' ORDER BY id DESC LIMIT 50 OFFSET ?',args+[offset]).fetchall()
    return {'items':[product_json(r) for r in rows], 'total':total}

async def save_admin_product(request, product_id=None):
    with db() as c:
        old=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone() if product_id else None
    if product_id and not old:
        raise HTTPException(404,'Товар не найден')
    async with request.form(max_files=1,max_fields=12,max_part_size=16*1024*1024) as form:
        def text(name,limit,required=False):
            value=form.get(name,'')
            if not isinstance(value,str): raise HTTPException(422,f'Некорректное поле {name}')
            value=value.strip()
            if len(value)>limit or (required and not value):
                raise HTTPException(422,f'Заполните {name}, максимум {limit} символов')
            return value
        title=text('title',120,True); description=text('description',1200)
        category=text('category',40,True); brand=text('brand',80)
        try:
            price=int(str(form.get('price',''))); active=int(str(form.get('active','1')))
            if not 0<=price<=100000000 or active not in {0,1}: raise ValueError()
        except (ValueError,TypeError):
            raise HTTPException(422,'Цена — целое число от 0 до 100 000 000; публикация — 0 или 1')
        if category not in CATEGORIES:
            raise HTTPException(422,'Выберите категорию из списка')
        def options(name):
            values=list(dict.fromkeys(s.strip() for s in text(name,500).split(',') if s.strip()))
            if len(values)>25 or any(len(v)>40 for v in values):
                raise HTTPException(422,'Не более 25 значений по 40 символов, разделённых запятой')
            return json.dumps(values,ensure_ascii=False)
        sizes=options('sizes'); colors=options('colors')
        upload=form.get('image'); image=old['image'] if old else None; path=None
        if upload and hasattr(upload,'read') and upload.filename:
            raw=await upload.read(15*1024*1024+1)
            if len(raw)>15*1024*1024: raise HTTPException(413,'Фото должно быть не больше 15 МБ')
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    if source.format not in {'JPEG','PNG','WEBP'} or source.width*source.height>36000000:
                        raise ValueError()
                    if min(source.size)<600:
                        raise HTTPException(422,'Выберите оригинал фото: минимум 600×600 пикселей')
                    im=ImageOps.exif_transpose(source)
                    if im.mode in {'RGBA','LA'} or 'transparency' in im.info:
                        rgba=im.convert('RGBA'); background=Image.new('RGBA',rgba.size,'white');background.alpha_composite(rgba);im=background.convert('RGB')
                    else: im=im.convert('RGB')
                    im.thumbnail((2560,2560))
                    output=io.BytesIO(); im.save(output,format='WEBP',quality=94,method=4)
                    name=uuid.uuid4().hex+'.webp';path=DATA/'products'/name
                    image='/product-images/'+name
            except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
                raise HTTPException(422,'Нужен JPG, PNG или WebP до 36 мегапикселей')
        if not image:
            raise HTTPException(422,'Добавьте качественную фотографию товара')
        try:
            if path: path.write_bytes(output.getvalue())
            with db() as c:
                args=(title,price,category,image,description,active,brand,sizes,colors)
                if product_id:
                    cur=c.execute('UPDATE products SET title=?,price=?,category=?,image=?,description=?,active=?,brand=?,sizes=?,colors=? WHERE id=?',args+(product_id,))
                    if not cur.rowcount: raise HTTPException(404,'Товар не найден')
                else:
                    product_id=c.execute('INSERT INTO products(title,price,category,image,description,active,brand,sizes,colors) VALUES (?,?,?,?,?,?,?,?,?)',args).lastrowid
                row=c.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        except Exception:
            if path: path.unlink(missing_ok=True)
            raise
        # Старые изображения не удаляются автоматически: клиент мог открыть карточку
        # до замены. Очистку неиспользуемых файлов выполняет владелец с резервной копией.
        return product_json(row)

@app.post('/api/admin/products',status_code=201)
async def admin_create(request:Request,user=Depends(require_admin)):
    return await save_admin_product(request)

@app.put('/api/admin/products/{product_id}')
async def admin_update(product_id:int,request:Request,user=Depends(require_admin)):
    return await save_admin_product(request,product_id)

@app.put('/api/admin/products/{product_id}/visibility')
async def admin_visibility(product_id:int,request:Request,user=Depends(require_admin)):
    try: data=await request.json()
    except ValueError: raise HTTPException(422,'Некорректный JSON')
    if not isinstance(data,dict) or type(data.get('active')) is not bool:
        raise HTTPException(422,'Передайте active: true или false')
    with db() as c:
        cur=c.execute('UPDATE products SET active=? WHERE id=?',(int(data['active']),product_id))
        if not cur.rowcount: raise HTTPException(404,'Товар не найден')
    return {'active':data['active']}

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
