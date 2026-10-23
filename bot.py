"""Long polling: python -m app.bot. Только один экземпляр на токен."""
import asyncio
import io
import uuid
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from PIL import Image, ImageOps
from .core import TOKEN, ADMIN_IDS, MANAGER_ID, WEBAPP_URL, DATA, db

router=Dispatcher()

def is_admin(user_id):
    return user_id in ADMIN_IDS

@router.message(CommandStart())
async def start(m:Message):
    await m.answer('RAVOUT SHMOT — редкие вещи из Китая.\nОткройте каталог или оставьте заявку на поиск вещи по фото.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Открыть магазин',web_app=WebAppInfo(url=WEBAPP_URL))]]))

@router.message(Command('id'))
async def ids(m:Message):
    await m.answer(f'Ваш ID: {m.from_user.id}\nID этого чата: {m.chat.id}')

@router.message(Command('admin'))
async def admin(m:Message):
    if not is_admin(m.from_user.id): return
    await m.answer('Управление магазином:\n\n'
      'Отправьте фото товара с подписью:\n/add Название | 15000 | Одежда | Описание\n\n'
      'Категории: Одежда, Обувь, Сумки, Аксессуары\n'
      '/products — список товаров\n'
      '/edit ID | Название | Цена | Категория | Описание\n'
      '/hide ID — скрыть товар\n/show ID — вернуть товар\n'
      '/requests — последние 20 заявок и очередь доставки\n'
      '/status ID new|working|done|cancelled — статус заявки\n'
      '/retry ID — повторить доставку менеджеру\n'
      '/id — ID пользователя и чата')

@router.message(F.photo, F.caption.startswith('/add '))
async def add(m:Message,bot:Bot):
    if not is_admin(m.from_user.id): return
    try:
        parts=m.caption[5:].split('|',3)
        title=parts[0].strip(); price=int(parts[1].strip()); category=parts[2].strip()
        description=parts[3].strip() if len(parts)>3 else ''
        if not title or len(title)>120 or price<0 or price>100_000_000 or category not in {'Одежда','Обувь','Сумки','Аксессуары'} or len(description)>1200:
            raise ValueError()
        raw=io.BytesIO(); await bot.download(m.photo[-1],destination=raw); raw.seek(0)
        name=uuid.uuid4().hex+'.jpg'
        with Image.open(raw) as im:
            im=ImageOps.exif_transpose(im).convert('RGB'); im.thumbnail((1600,1600)); im.save(DATA/'products'/name,quality=90)
        with db() as c:
            cur=c.execute('INSERT INTO products(title,price,category,image,description) VALUES (?,?,?,?,?)',(title,price,category,'/product-images/'+name,description))
            pid=cur.lastrowid
        await m.answer(f'Товар №{pid} добавлен в каталог.')
    except (ValueError,IndexError,OSError):
        await m.answer('Формат подписи: /add Название | 15000 | Одежда | Описание. Прикрепите фото.')

@router.message(Command('products'))
async def products(m:Message):
    if not is_admin(m.from_user.id): return
    with db() as c:
        rows=c.execute('SELECT * FROM products ORDER BY id DESC LIMIT 50').fetchall()
    await m.answer('\n'.join(f'{r["id"]}. {r["title"][:40]} · {r["price"]} ₽ · {"виден" if r["active"] else "скрыт"}' for r in rows) or 'Товаров пока нет. /admin — инструкция.')

@router.message(Command('edit','hide','show'))
async def edit(m:Message):
    if not is_admin(m.from_user.id): return
    try:
        cmd,body=m.text.split(maxsplit=1); cmd=cmd.split('@')[0]
        if cmd=='/edit':
            parts=[s.strip() for s in body.split('|',4)]
            pid=int(parts[0]); price=int(parts[2]); title=parts[1]; category=parts[3]; description=parts[4] if len(parts)>4 else ''
            if not title or len(title)>120 or not 0<=price<=100_000_000 or category not in {'Одежда','Обувь','Сумки','Аксессуары'} or len(description)>1200: raise ValueError()
            with db() as c:
                cur=c.execute('UPDATE products SET title=?,price=?,category=?,description=? WHERE id=?',(title,price,category,description,pid))
        else:
            with db() as c:
                cur=c.execute('UPDATE products SET active=? WHERE id=?',(int(cmd=='/show'),int(body)))
        await m.answer('Готово.' if cur.rowcount else 'Товар не найден.')
    except (ValueError,IndexError):
        await m.answer('Неверный формат. См. /admin')

@router.message(Command('requests'))
async def requests(m:Message):
    if not is_admin(m.from_user.id): return
    with db() as c:
        rows=c.execute('SELECT r.id,r.status,r.username,o.delivered,o.attempts FROM requests r JOIN outbox o ON r.id=o.request_id ORDER BY r.id DESC LIMIT 20').fetchall()
    await m.answer('\n'.join(f'№{r["id"]} @{r["username"] or "без_username"} · {r["status"]} · {"доставлена" if r["delivered"] else "очередь, попыток: "+str(r["attempts"])}' for r in rows) or 'Заявок пока нет.')

async def set_status(bot,rid,status):
    if status not in {'new','working','done','cancelled'}: return False
    with db() as c:
        r=c.execute('SELECT user_id FROM requests WHERE id=?',(rid,)).fetchone()
        if not r: return False
        c.execute('UPDATE requests SET status=? WHERE id=?',(status,rid))
    labels={'new':'Принята','working':'В работе','done':'Завершена','cancelled':'Отменена'}
    try:
        await bot.send_message(r['user_id'],f'Заявка №{rid}: {labels[status]}.')
    except Exception:
        pass  # Пользователь мог заблокировать бота; статус доступен в Mini App.
    return True

@router.callback_query(F.data.startswith('status:'))
async def status_callback(q:CallbackQuery,bot:Bot):
    if not is_admin(q.from_user.id):
        return await q.answer('Нет доступа',show_alert=True)
    _,rid,status=q.data.split(':')
    ok=await set_status(bot,int(rid),status)
    await q.answer('Статус обновлён' if ok else 'Заявка не найдена',show_alert=True)

@router.message(Command('status','retry'))
async def status_command(m:Message,bot:Bot):
    if not is_admin(m.from_user.id): return
    try:
        parts=m.text.split(); rid=int(parts[1])
        if parts[0].split('@')[0]=='/retry':
            with db() as c:
                cur=c.execute('UPDATE outbox SET delivered=0,next_attempt=0,attempts=0 WHERE request_id=?',(rid,))
            ok=bool(cur.rowcount)
        else:
            ok=await set_status(bot,rid,parts[2])
        await m.answer('Готово.' if ok else 'Заявка или статус не найдены.')
    except (ValueError,IndexError):
        await m.answer('Неверный формат. См. /admin')

async def main(handle_signals=True):
    if not TOKEN or not WEBAPP_URL.startswith('https://') or not ADMIN_IDS or not MANAGER_ID:
        raise RuntimeError('Нужны BOT_TOKEN, ADMIN_IDS, MANAGER_CHAT_ID и WEBAPP_URL=https://...')
    async with Bot(TOKEN) as bot:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='Магазин',web_app=WebAppInfo(url=WEBAPP_URL)))
        await router.start_polling(bot,allowed_updates=router.resolve_used_update_types(),handle_signals=handle_signals)

if __name__=='__main__':
    asyncio.run(main())
