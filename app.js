'use strict';
const $=s=>document.querySelector(s), tg=window.Telegram?.WebApp;
const newKey=()=>crypto.randomUUID?crypto.randomUUID():[...crypto.getRandomValues(new Uint8Array(16))].map(b=>b.toString(16).padStart(2,'0')).join('');
const categories=['Одежда','Обувь','Сумки','Аксессуары'];
const defaults=()=>({category:'',brand:'',size:'',color:'',min_price:'',max_price:'',sort:'newest'});
const state={user:null,tab:'catalog',filters:defaults(),products:[],favorites:[],adminItems:[],adminTotal:0,photos:[],product:null,detail:null,editor:null,editorURL:null,saving:false,key:newKey(),requestURLs:[],querySeq:0,favoriteSeq:0,adminSeq:0};
const plural=(n,one,few,many)=>{n=Math.abs(n)%100;return n>10&&n<20?many:n%10===1?one:n%10>=2&&n%10<=4?few:many;};
const money=n=>new Intl.NumberFormat('ru-RU').format(n)+' ₽';
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
function icon(name){const ns='http://www.w3.org/2000/svg',s=document.createElementNS(ns,'svg'),u=document.createElementNS(ns,'use');u.setAttribute('href','#i-'+name);s.setAttribute('aria-hidden','true');s.append(u);return s;}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,4200);}
function buttonContents(button,text,name){button.replaceChildren(document.createTextNode(text));if(name)button.append(icon(name));}
async function api(path,options={}){
 const headers={'X-Telegram-Init-Data':tg?.initData||'',...options.headers};
 if(options.body&&!(options.body instanceof FormData))headers['Content-Type']='application/json';
 let response;try{response=await fetch('/api'+path,{...options,headers});}catch{throw Error('Нет соединения. Проверь интернет и попробуй снова.');}
 let data;try{data=await response.json();}catch{throw Error('Сервер недоступен. Попробуй позже.');}
 if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Проверь заполнение полей.');return data;
}
function imageSource(path){return typeof path==='string'&&(path.startsWith('/product-images/')||path.startsWith('/static/'))?path:null;}
function empty(title,text,find=false){const el=node('div','empty');el.append(icon('photo'),node('strong','',title),node('p','',text));if(find){const b=node('button','secondary','Найти по фото');b.onclick=()=>navigate('find');el.append(b);}return el;}
function navigate(tab){
 if(tab==='admin'&&!state.user?.is_admin){toast('Раздел доступен только администратору');return;}
 clearTimeout(toast.timer);$('#toast').hidden=true;
 state.tab=tab;['catalog','find','favorites','profile','admin','success'].forEach(id=>$('#'+id).hidden=id!==tab);
 $('#nav').hidden=tab==='success';$('#viewport').scrollTop=0;
 document.querySelectorAll('nav [data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-current',b.dataset.tab===tab?'page':'false');});
 let pending;if(tab==='catalog')pending=loadCatalog();if(tab==='favorites')pending=loadFavorites();if(tab==='profile')pending=loadRequests();if(tab==='admin')pending=loadAdmin();
 if(tab!=='catalog')tg?.BackButton?.show();else tg?.BackButton?.hide();
 return pending;
}
function renderCategories(){
 $('#categories').replaceChildren(...['',...categories].map(c=>{const b=node('button','chip'+(state.filters.category===c?' selected':''),c||'Все вещи');b.setAttribute('aria-pressed',state.filters.category===c);b.onclick=()=>{state.filters.category=c;renderFilters();loadCatalog();};return b;}));
}
function renderFilters(){
 renderCategories();const fields=Object.entries(state.filters).filter(([k,v])=>v&&!(k==='sort'&&v==='newest'));
 $('#filter-badge').hidden=!fields.length;$('#filter-badge').textContent=fields.length;
 $('#active-filters').replaceChildren(...fields.map(([k,v])=>{const labels={min_price:'От '+money(v),max_price:'До '+money(v),sort:{price_asc:'Дешевле',price_desc:'Дороже',title:'По названию'}[v]};const b=node('button','filter-tag',labels[k]||v);b.append(icon('close'));b.setAttribute('aria-label','Убрать фильтр: '+(labels[k]||v));b.onclick=()=>{state.filters[k]=defaults()[k];renderFilters();loadCatalog();};return b;}));
}
function productCard(p){
 const el=node('article','product'),open=node('button','product-open'),media=node('div','product-media'),src=imageSource(p.image);
 if(src){const img=node('img','product-image');img.src=src;img.alt=p.title;img.loading='lazy';img.decoding='async';img.onerror=()=>media.replaceChildren(node('span','product-no-photo','Фото недоступно'));media.append(img);}else media.append(node('span','product-no-photo','Фото появится позже'));
 const meta=node('div','product-meta');if(p.brand)meta.append(node('span','product-brand',p.brand));meta.append(node('h3','',p.title),node('p','price',money(p.price)));open.append(media,meta);open.onclick=()=>openProduct(p);
 const heart=node('button','heart'+(p.favorite?' on':''));heart.append(icon('heart'));heart.setAttribute('aria-label','В избранное: '+p.title);heart.setAttribute('aria-pressed',!!p.favorite);
 heart.onclick=async()=>{heart.disabled=true;try{const data=await api('/favorites/'+p.id,{method:'PUT',body:JSON.stringify({favorite:!p.favorite})});p.favorite=data.favorite;heart.classList.toggle('on',p.favorite);heart.setAttribute('aria-pressed',p.favorite);tg?.HapticFeedback?.selectionChanged();if(state.tab==='favorites')loadFavorites();}catch(e){toast(e.message);}finally{heart.disabled=false;}};
 el.append(open,heart);return el;
}
function queryFilters(offset=0){const p=new URLSearchParams({q:$('#search').value,limit:'40',offset:String(offset)});for(const[k,v]of Object.entries(state.filters))if(v!=='')p.set(k,v);return p;}
async function loadCatalog(more=false){
 const seq=++state.querySeq;$('#load-more').disabled=true;if(!more){state.products=[];$('#products').replaceChildren(node('div','skeleton'),node('div','skeleton'));$('#product-count').textContent='';}
 try{const items=await api('/products?'+queryFilters(more?state.products.length:0));if(seq!==state.querySeq)return;state.products=more?[...state.products,...items]:items;$('#products').replaceChildren(...state.products.map(productCard));$('#product-count').textContent=state.products.length+(items.length===40?'+':'')+' '+plural(state.products.length,'вещь','вещи','вещей');$('#load-more').hidden=items.length<40;
 if(!state.products.length)$('#products').append(empty('Пока ничего не найдено','Попробуй другие фильтры или отправь фото — подберём нужную вещь.',true));
 }catch(e){if(seq!==state.querySeq)return;$('#products').replaceChildren(empty('Не удалось загрузить каталог',e.message));$('#load-more').hidden=true;}finally{if(seq===state.querySeq)$('#load-more').disabled=false;}
}
async function loadFavorites(more=false){
 const seq=++state.favoriteSeq;$('#favorites-more').disabled=true;
 try{const items=await api('/products?favorites=true&limit=40&offset='+(more?state.favorites.length:0));if(seq!==state.favoriteSeq)return;state.favorites=more?[...state.favorites,...items]:items;$('#favorite-products').replaceChildren(...state.favorites.map(productCard));$('#favorites-more').hidden=items.length<40;if(!state.favorites.length)$('#favorite-products').append(empty('Здесь будут твои находки','Нажми на сердечко у понравившейся вещи.'));}catch(e){toast(e.message);}finally{if(seq===state.favoriteSeq)$('#favorites-more').disabled=false;}
}
function fillSelect(selector,values,placeholder,selected){const el=$(selector);el.replaceChildren(new Option(placeholder,''),...values.map(v=>new Option(v,v)));if(selected&&!values.includes(selected))el.add(new Option(selected,selected));el.value=selected||'';}
async function openFilters(){
 $('#filter-error').hidden=true;
 try{const f=await api('/catalog-filters');fillSelect('#filter-category',f.categories,'Все категории',state.filters.category);fillSelect('#filter-brand',f.brands,'Все бренды',state.filters.brand);fillSelect('#filter-size',f.sizes,'Любой',state.filters.size);fillSelect('#filter-color',f.colors,'Любой',state.filters.color);$('#filter-min').value=state.filters.min_price;$('#filter-max').value=state.filters.max_price;$('#filter-sort').value=state.filters.sort;$('#filter-dialog').showModal();$('#menu-open').setAttribute('aria-expanded','true');}catch(e){toast(e.message);}
}
$('#filters-form').onsubmit=async e=>{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));if(f.min_price&&f.max_price&&Number(f.min_price)>Number(f.max_price)){$('#filter-error').textContent='Цена «От» не должна быть выше цены «До».';$('#filter-error').hidden=false;return;}state.filters=f;$('#filter-dialog').close();renderFilters();if(state.tab==='catalog')await loadCatalog();else await navigate('catalog');$('#products-heading').scrollIntoView({block:'start',behavior:'instant'});};
$('#filter-reset').onclick=()=>{$('#filters-form').reset();$('#filter-error').hidden=true;};
$('#filter-dialog').addEventListener('close',()=>$('#menu-open').setAttribute('aria-expanded','false'));
function openProduct(p){state.detail=p;const src=imageSource(p.image);$('#detail-image').src=src||'/static/assets/hero.webp';$('#detail-image').alt=p.title;$('#detail-category').textContent=p.category;$('#detail-brand').textContent=p.brand||'';$('#detail-title').textContent=p.title;$('#detail-price').textContent=money(p.price);$('#detail-description').textContent=p.description||'Уточни подробности у менеджера.';$('#detail-options').replaceChildren(...[...(p.sizes||[]).map(s=>'Размер '+s),...(p.colors||[])].map(t=>node('span','',t)));$('#product-dialog').showModal();}
$('#order-product').onclick=()=>{state.product=state.detail;$('#product-dialog').close();$('#selected-product-text').textContent=state.product.title+' · '+money(state.product.price);$('#selected-product').hidden=false;navigate('find');};
$('#detail-photo-open').onclick=()=>{$('#large-photo').src=$('#detail-image').src;$('#large-photo').alt=$('#detail-image').alt;$('#photo-dialog').showModal();};
$('#clear-product').onclick=()=>{state.product=null;$('#selected-product').hidden=true;};
$('#product-dialog').addEventListener('close',()=>state.detail=null);
function photoPreviews(){
 $('#photo-previews').replaceChildren(...state.photos.map((p,i)=>{const box=node('div','photo-preview'),img=node('img');img.src=p.url;img.alt='Фото '+(i+1);const remove=node('button');remove.type='button';remove.setAttribute('aria-label','Удалить фото '+(i+1));remove.append(icon('close'));remove.onclick=()=>{URL.revokeObjectURL(p.url);state.photos.splice(i,1);photoPreviews();};box.append(img,remove);return box;}));$('.add-photo').hidden=state.photos.length>=3;
}
$('#photos').onchange=e=>{for(const f of e.target.files){if(state.photos.length>=3){toast('Не больше 3 фотографий');break;}if(!['image/jpeg','image/png','image/webp'].includes(f.type)){toast('Выбери JPG, PNG или WebP');continue;}if(f.size>5*1024*1024){toast('Фото должно быть не больше 5 МБ');continue;}state.photos.push({file:f,url:URL.createObjectURL(f)});}e.target.value='';photoPreviews();};
$('#request-form').onsubmit=async e=>{
 e.preventDefault();if($('#send').disabled)return;$('#form-error').hidden=true;
 if(!$('#description').value.trim()&&!state.photos.length&&!state.product){$('#form-error').textContent='Добавь фото или описание вещи.';$('#form-error').hidden=false;return;}
 const f=new FormData(e.target);f.set('idempotency',state.key);if(state.product)f.set('product_id',state.product.id);state.photos.forEach(p=>f.append('photos',p.file));$('#send').disabled=true;buttonContents($('#send'),'Отправляем…');
 try{const r=await api('/requests',{method:'POST',body:f});$('#request-number').textContent='ЗАЯВКА № '+r.id;$('#success-text').textContent=state.user.dev&&!state.user.manager_enabled?'Демо: заявка сохранена локально. После подключения бота она сможет отправляться менеджеру.':'Заявка сохранена. После получения менеджер свяжется с тобой в Telegram и предложит варианты.';e.target.reset();state.photos.forEach(p=>URL.revokeObjectURL(p.url));state.photos=[];photoPreviews();state.product=null;$('#selected-product').hidden=true;state.key=newKey();$('#username').value=state.user.username?'@'+state.user.username:'';navigate('success');tg?.HapticFeedback?.notificationOccurred('success');}catch(err){$('#form-error').textContent=err.message;$('#form-error').hidden=false;tg?.HapticFeedback?.notificationOccurred('error');}finally{$('#send').disabled=false;buttonContents($('#send'),'Отправить заявку','arrow');}
};
let requestSeq=0;
async function loadRequests(){
 const seq=++requestSeq;state.requestURLs.forEach(u=>URL.revokeObjectURL(u));state.requestURLs=[];$('#requests').replaceChildren(node('p','hint','Загружаем заявки…'));
 try{const items=await api('/requests');if(seq!==requestSeq)return;$('#requests').replaceChildren(...items.map(r=>{const card=node('article','request-card'),header=node('header');header.append(node('strong','','№ '+r.id),node('span','status',r.status_label));card.append(header,node('p','',r.description||'Подбор по фото / товару'),node('small','',new Date(r.created_at*1000).toLocaleString('ru-RU')));const d=node('details');d.append(node('summary','','Параметры заявки'),node('p','',`Размер: ${r.size||'—'} · Цвет: ${r.color||'—'}\nБюджет: ${r.budget||'—'} · Количество: ${r.quantity}\n${r.link||''}`));if(r.photos.length){const pics=node('div','request-photos');d.append(pics);d.addEventListener('toggle',async()=>{if(!d.open||pics.dataset.loaded)return;pics.dataset.loaded='true';for(const id of r.photos){try{const res=await fetch('/api/photos/'+id,{headers:{'X-Telegram-Init-Data':tg?.initData||''}});if(!res.ok)throw Error();const blob=await res.blob();if(seq!==requestSeq)return;const url=URL.createObjectURL(blob);state.requestURLs.push(url);const img=node('img');img.src=url;img.alt='Фото к заявке';pics.append(img);}catch{pics.append(node('small','','Не удалось загрузить фото'));}}});}card.append(d,node('p','hint',r.delivered?'Доставлена менеджеру':'Сохранена · ожидает отправки менеджеру'));return card;}));if(!items.length)$('#requests').append(empty('Заявок пока нет','Выбери вещь или отправь фото для подбора.',true));}catch(e){if(seq===requestSeq)$('#requests').replaceChildren(empty('Не удалось загрузить заявки',e.message));}
}
async function loadAdmin(more=false){
 if(!state.user?.is_admin)return;const seq=++state.adminSeq;$('#admin-more').disabled=true;
 const params=new URLSearchParams({q:$('#admin-search').value,visibility:$('#admin-visibility').value,offset:String(more?state.adminItems.length:0)});
 try{const data=await api('/admin/products?'+params);if(seq!==state.adminSeq)return;state.adminItems=more?[...state.adminItems,...data.items]:data.items;state.adminTotal=data.total;$('#admin-total').textContent=data.total+' '+plural(data.total,'товар','товара','товаров');$('#admin-products').replaceChildren(...state.adminItems.map(adminCard));$('#admin-more').hidden=state.adminItems.length>=data.total;if(!data.total)$('#admin-products').append(empty('Добавь первую вещь','Загрузи качественное фото, укажи цену и опубликуй товар.'));}catch(e){if(seq===state.adminSeq)$('#admin-products').replaceChildren(empty('Не удалось загрузить товары',e.message));}finally{if(seq===state.adminSeq)$('#admin-more').disabled=false;}
}
function adminCard(p){
 const row=node('article','admin-item'),img=node('img','admin-item-image');img.src=imageSource(p.image)||'/static/assets/hero.webp';img.alt=p.title;img.loading='lazy';const body=node('div','admin-item-body');body.append(node('h3','',p.title),node('p','price',money(p.price)),node('p','admin-item-meta',(p.active?'Опубликован':'Скрыт')+' · '+p.category+' · № '+p.id));const actions=node('div','admin-actions'),edit=node('button','','Изменить'),vis=node('button','visibility-btn',p.active?'Скрыть':'Опубликовать');edit.prepend(icon('edit'));edit.onclick=()=>openEditor(p);vis.onclick=async()=>{vis.disabled=true;try{await api('/admin/products/'+p.id+'/visibility',{method:'PUT',body:JSON.stringify({active:!p.active})});toast(p.active?'Товар скрыт из каталога':'Товар опубликован');loadAdmin();}catch(e){toast(e.message);vis.disabled=false;}};actions.append(edit,vis);body.append(actions);row.append(img,body);return row;
}
function editorPreview(src){$('#product-preview').hidden=!src;$('#product-upload-label').hidden=!!src;$('#replace-photo').hidden=!src;if(src)$('#product-preview').src=src;else $('#product-preview').removeAttribute('src');}
function openEditor(p=null){
 state.editor=p;$('#product-form').reset();$('#editor-error').hidden=true;$('#editor-title').textContent=p?'Редактировать товар':'Новый товар';$('#product-title').value=p?.title||'';$('#product-price').value=p?.price??'';$('#product-category').value=p?.category||categories[0];$('#product-brand').value=p?.brand||'';$('#product-description').value=p?.description||'';$('#product-sizes').value=(p?.sizes||[]).join(', ');$('#product-colors').value=(p?.colors||[]).join(', ');$('#product-active').checked=p?!!p.active:true;editorPreview(imageSource(p?.image));$('#editor-dialog').showModal();$('#editor-dialog .editor-body').scrollTop=0;
}
$('#product-photo').onchange=e=>{const f=e.target.files[0];if(!f)return;if(!['image/jpeg','image/png','image/webp'].includes(f.type)||f.size>15*1024*1024){toast('Выбери JPG, PNG или WebP до 15 МБ');e.target.value='';if(state.editorURL)URL.revokeObjectURL(state.editorURL);state.editorURL=null;editorPreview(imageSource(state.editor?.image));return;}if(state.editorURL)URL.revokeObjectURL(state.editorURL);state.editorURL=URL.createObjectURL(f);editorPreview(state.editorURL);};
$('#product-form').onsubmit=async e=>{
 e.preventDefault();if(state.saving)return;$('#editor-error').hidden=true;
 if(!state.editor&&!$('#product-photo').files.length){$('#editor-error').textContent='Добавь фотографию товара.';$('#editor-error').hidden=false;$('#editor-error').scrollIntoView({block:'nearest'});return;}
 const f=new FormData(e.target);f.set('active',$('#product-active').checked?'1':'0');if(!$('#product-photo').files.length)f.delete('image');state.saving=true;$('#save-product').disabled=true;$('#editor-dialog .close').disabled=true;buttonContents($('#save-product'),'Сохраняем…');
 try{await api('/admin/products'+(state.editor?'/'+state.editor.id:''),{method:state.editor?'PUT':'POST',body:f});$('#editor-dialog').close();toast('Товар сохранён');await loadAdmin();}catch(err){$('#editor-error').textContent=err.message;$('#editor-error').hidden=false;$('#editor-error').scrollIntoView({block:'nearest'});}finally{state.saving=false;$('#save-product').disabled=false;$('#editor-dialog .close').disabled=false;buttonContents($('#save-product'),'Сохранить товар','check');}
};
$('#editor-dialog').addEventListener('cancel',e=>{if(state.saving)e.preventDefault();});$('#editor-dialog').addEventListener('close',()=>{if(state.editorURL)URL.revokeObjectURL(state.editorURL);state.editorURL=null;state.editor=null;$('#product-photo').value='';});
document.querySelectorAll('dialog .close').forEach(b=>b.onclick=()=>b.closest('dialog').close());document.querySelectorAll('nav [data-tab]').forEach(b=>b.onclick=()=>navigate(b.dataset.tab));
$('#brand-home').onclick=()=>navigate('catalog');$('#menu-open').onclick=openFilters;$('#filters-open').onclick=openFilters;$('#hero-find').onclick=()=>navigate('find');$('#find-banner').onclick=()=>navigate('find');$('#my-requests').onclick=()=>navigate('profile');$('#back-catalog').onclick=()=>navigate('catalog');$('#refresh-requests').onclick=loadRequests;$('#reload').onclick=()=>location.reload();$('#privacy-open').onclick=()=>$('#privacy-dialog').showModal();$('#profile-privacy').onclick=()=>$('#privacy-dialog').showModal();$('#profile-admin').onclick=()=>navigate('admin');$('#admin-add').onclick=()=>openEditor();$('#admin-visibility').onchange=()=>loadAdmin();$('#load-more').onclick=()=>loadCatalog(true);$('#favorites-more').onclick=()=>loadFavorites(true);$('#admin-more').onclick=()=>loadAdmin(true);
let searchTimer,adminSearchTimer;$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadCatalog(),250);};$('#admin-search').oninput=()=>{clearTimeout(adminSearchTimer);adminSearchTimer=setTimeout(()=>loadAdmin(),250);};tg?.BackButton?.onClick(()=>navigate('catalog'));
async function init(){
 try{tg?.ready();tg?.expand();tg?.setHeaderColor?.('#f9f8f4');tg?.setBackgroundColor?.('#ffffff');state.user=await api('/me');$('#profile-name').textContent=state.user.first_name||'Покупатель';$('#profile-username').textContent=(state.user.username?'@'+state.user.username+' · ':'')+'ID '+state.user.id;$('#avatar').textContent=(state.user.first_name||'R')[0].toUpperCase();$('#username').value=state.user.username?'@'+state.user.username:'';$('#nav-admin').hidden=!state.user.is_admin;$('#profile-admin').hidden=!state.user.is_admin;$('#header').hidden=false;$('#nav').hidden=false;$('#demo').hidden=!state.user.dev;renderFilters();navigate('catalog');}catch(e){$('#fatal-message').textContent=e.message;$('#fatal').hidden=false;}finally{$('#splash').hidden=true;}
}
init();
