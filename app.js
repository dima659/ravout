'use strict';
const $=s=>document.querySelector(s);
const tg=window.Telegram?.WebApp;
const state={user:null,tab:'catalog',category:'',photos:[],product:null,detail:null,key:crypto.randomUUID(),requestsPhotos:[],querySeq:0};
const screens=['catalog','find','success','favorites','profile'];
const money=n=>new Intl.NumberFormat('ru-RU').format(n)+' ₽';
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,4000);}
async function api(path,options={}){
 const headers={'X-Telegram-Init-Data':tg?.initData||'',...options.headers};
 if(options.body && !(options.body instanceof FormData))headers['Content-Type']='application/json';
 const response=await fetch('/api'+path,{...options,headers});
 let result;try{result=await response.json();}catch{throw Error('Сервер недоступен. Попробуйте позже.');}
 if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Проверьте заполнение формы.');
 return result;
}
function navigate(tab,button){
 state.tab=tab;
 $('#header').hidden=tab==='success';$('#nav').hidden=tab==='success';
 screens.forEach(id=>$('#'+id).hidden=id!==tab);
 document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',button?b===button:b.dataset.tab===tab&&!b.dataset.catalog));
 window.scrollTo({top:0,behavior:'instant'});
 if(tab==='catalog')loadCatalog();
 if(tab==='favorites')loadFavorites();
 if(tab==='profile')loadRequests();
 if(tab==='find')tg?.BackButton?.show();else tg?.BackButton?.hide();
}
function empty(title,text,action){
 const el=node('div','empty');el.append(node('strong','',title),node('p','',text));
 if(action){const b=node('button','secondary','Найти вещь по фото →');b.onclick=()=>navigate('find');el.append(b);}return el;
}
function productCard(p){
 const card=node('article','product'), open=node('button','product-open');
 const img=node('img','product-image');img.src=p.image||'/static/assets/brand.png';img.alt=p.title;img.loading='lazy';
 open.append(img,node('h3','',p.title),node('p','price',money(p.price)));open.onclick=()=>openProduct(p);
 const heart=node('button','heart'+(p.favorite?' on':''),p.favorite?'♥':'♡');heart.setAttribute('aria-label','В избранное: '+p.title);heart.setAttribute('aria-pressed',!!p.favorite);
 heart.onclick=async()=>{heart.disabled=true;try{const r=await api('/favorites/'+p.id,{method:'PUT',body:JSON.stringify({favorite:!p.favorite})});p.favorite=r.favorite;heart.textContent=p.favorite?'♥':'♡';heart.classList.toggle('on',p.favorite);heart.setAttribute('aria-pressed',p.favorite);tg?.HapticFeedback?.selectionChanged();if(state.tab==='favorites')await loadFavorites();}catch(e){toast(e.message);}finally{heart.disabled=false;}};
 card.append(open,heart);return card;
}
async function loadCatalog(){
 const seq=++state.querySeq;
 try{const q=new URLSearchParams({q:$('#search').value,category:state.category});const items=await api('/products?'+q);if(seq!==state.querySeq)return;
 $('#products').replaceChildren(...items.map(productCard));$('#product-count').textContent=items.length+' вещей';
 if(!items.length)$('#products').append(empty('Пока пусто',state.category||$('#search').value?'По вашему запросу ничего не найдено. Мы можем найти вещь по фото.':'Новые вещи скоро появятся. А пока отправьте фото — подберём нужное.',true));
 }catch(e){if(seq!==state.querySeq)return;$('#products').replaceChildren(empty('Не удалось загрузить',e.message,false));}
}
async function loadFavorites(){
 try{const items=await api('/products?favorites=true');$('#favorite-products').replaceChildren(...items.map(productCard));if(!items.length)$('#favorite-products').append(empty('Здесь будут ваши находки','Нажмите сердечко на понравившейся вещи.',false));}catch(e){toast(e.message);}
}
function openProduct(p){
 state.detail=p;$('#detail-image').src=p.image||'/static/assets/brand.png';$('#detail-image').alt=p.title;$('#detail-title').textContent=p.title;$('#detail-category').textContent=p.category;$('#detail-description').textContent=p.description;$('#detail-price').textContent=money(p.price);$('#product-dialog').showModal();
}
function photoPreviews(){
 $('#photo-previews').replaceChildren();
 state.photos.forEach((p,i)=>{const el=node('div','photo-preview'),img=node('img');img.src=p.url;img.alt='Фото '+(i+1);const remove=node('button','','×');remove.type='button';remove.setAttribute('aria-label','Удалить фото '+(i+1));remove.onclick=()=>{URL.revokeObjectURL(p.url);state.photos.splice(i,1);photoPreviews();};el.append(img,remove);$('#photo-previews').append(el);});
 $('.add-photo').hidden=state.photos.length>=3;
}
$('#photos').addEventListener('change',e=>{
 for(const file of e.target.files){
  if(state.photos.length>=3){toast('Максимум 3 фотографии');break;}
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)){toast('Выберите JPG, PNG или WebP');continue;}
  if(file.size>5*1024*1024){toast('Фото должно быть не больше 5 МБ');continue;}
  state.photos.push({file,url:URL.createObjectURL(file)});
 }e.target.value='';photoPreviews();
});
$('#request-form').addEventListener('submit',async e=>{
 e.preventDefault();const send=$('#send');if(send.disabled)return;$('#form-error').hidden=true;
 if(!$('#description').value.trim()&&!state.photos.length&&!state.product){$('#form-error').textContent='Добавьте фото или описание вещи.';$('#form-error').hidden=false;return;}
 const form=new FormData(e.target);form.set('idempotency',state.key);if(state.product)form.set('product_id',state.product.id);state.photos.forEach(p=>form.append('photos',p.file));
 send.disabled=true;send.textContent='Отправляем…';
 try{const r=await api('/requests',{method:'POST',body:form});$('#request-number').textContent='Заявка № '+r.id;
 $('#success-text').textContent=state.user.dev&&!state.user.manager_enabled?'Демо: заявка сохранена локально. Отправка менеджеру станет доступна после подключения бота.':'Заявка сохранена. Менеджер свяжется с вами в Telegram после её получения.';
 e.target.reset();state.photos.forEach(p=>URL.revokeObjectURL(p.url));state.photos=[];photoPreviews();state.product=null;state.key=crypto.randomUUID();$('#selected-product').hidden=true;$('#username').value=state.user.username?'@'+state.user.username:'';navigate('success');tg?.HapticFeedback?.notificationOccurred('success');
 }catch(err){$('#form-error').textContent=err.message;$('#form-error').hidden=false;tg?.HapticFeedback?.notificationOccurred('error');}
 finally{send.disabled=false;send.replaceChildren(document.createTextNode('Отправить заявку '),node('span','','→'));}
});
async function loadRequests(){
 state.requestsPhotos.forEach(u=>URL.revokeObjectURL(u));state.requestsPhotos=[];
 $('#requests').replaceChildren(node('p','hint','Загружаем заявки…'));
 try{const items=await api('/requests');const cards=items.map(r=>{
  const card=node('article','request-card'),header=node('header');header.append(node('strong','','№ '+r.id),node('span','status',r.status_label));
  card.append(header,node('p','',r.description||'Подбор по фото / товару'),node('small','',new Date(r.created_at*1000).toLocaleString('ru-RU')));
  const details=node('details');details.append(node('summary','','Параметры заявки'));details.append(node('p','',`Размер: ${r.size||'—'} · Цвет: ${r.color||'—'}\nБюджет: ${r.budget||'—'} · Кол-во: ${r.quantity}\n${r.link||''}`));
  if(r.photos.length){const pics=node('div','request-photos');details.append(pics);details.addEventListener('toggle',async()=>{if(!details.open||pics.dataset.loaded)return;pics.dataset.loaded='true';for(const id of r.photos){try{const res=await fetch('/api/photos/'+id,{headers:{'X-Telegram-Init-Data':tg?.initData||''}});if(!res.ok)throw Error();const url=URL.createObjectURL(await res.blob());state.requestsPhotos.push(url);const img=node('img');img.src=url;img.alt='Фото к заявке';pics.append(img);}catch{pics.append(node('small','','Не удалось загрузить фото'));}}});}
  card.append(details,node('p','hint',r.delivered?'Доставлена менеджеру':'Сохранена · ожидает отправки менеджеру'));return card;
 });$('#requests').replaceChildren(...cards);if(!items.length)$('#requests').append(empty('Заявок пока нет','Отправьте фото или выберите вещь в каталоге.',true));
 }catch(e){$('#requests').replaceChildren(empty('Не удалось загрузить',e.message,false));}
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{navigate(b.dataset.tab,b);if(b.dataset.catalog)$('#products-heading').scrollIntoView({block:'start',behavior:'smooth'});});
$('#categories').onclick=e=>{const b=e.target.closest('[data-category]');if(!b)return;state.category=b.dataset.category;$('#categories').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));loadCatalog();};
let searchTimer;$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadCatalog,250);};
$('#brand-home').onclick=()=>navigate('catalog');$('#find-banner').onclick=()=>navigate('find');$('#my-requests').onclick=()=>navigate('profile');$('#back-catalog').onclick=()=>navigate('catalog');$('#refresh-requests').onclick=loadRequests;$('#reload').onclick=()=>location.reload();
$('#order-product').onclick=()=>{state.product=state.detail;$('#product-dialog').close();$('#selected-product').textContent='Заявка на: '+state.product.title+' · '+money(state.product.price);$('#selected-product').hidden=false;navigate('find');};
// Просмотр карточки сам по себе не должен привязывать товар к заявке.
$('#product-dialog').addEventListener('close',()=>{state.detail=null;});
document.querySelectorAll('dialog .close').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#privacy-open').onclick=()=>$('#privacy-dialog').showModal();$('#profile-privacy').onclick=()=>$('#privacy-dialog').showModal();
tg?.BackButton?.onClick(()=>navigate('catalog'));
async function init(){
 try{tg?.ready();tg?.expand();tg?.setHeaderColor?.('#ffda00');tg?.setBackgroundColor?.('#ffda00');state.user=await api('/me');
 $('#profile-name').textContent=state.user.first_name||'Покупатель';$('#profile-username').textContent=state.user.username?'@'+state.user.username:'Telegram ID: '+state.user.id;$('#avatar').textContent=(state.user.first_name||'R')[0].toUpperCase();$('#username').value=state.user.username?'@'+state.user.username:'';
 $('#demo').hidden=!state.user.dev;$('#header').hidden=false;$('#nav').hidden=false;navigate('catalog');
 }catch(e){$('#fatal-message').textContent=e.message;$('#fatal').hidden=false;}finally{$('#splash').hidden=true;}
}
init();
