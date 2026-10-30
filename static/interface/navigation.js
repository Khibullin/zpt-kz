'use strict';
(() => {
const translations = {"Главная": "Басты бет", "Каталог": "Каталог", "Корзина": "Себет", "ZPT Гид": "ZPT Гид", "Кабинет": "Кабинет", "Клиентам": "Клиенттерге", "Автобизнесу": "Автобизнеске", "Артикул или название запчасти": "Артикул немесе бөлшек атауы", "Найти": "Іздеу", "Всё для вашего автомобиля": "Көлігіңізге қажеттінің бәрі", "Запчасти, авторазборы и услуги по Казахстану": "Қазақстан бойынша қосалқы бөлшектер, автобөлшектеу және қызметтер", "Оставить заявку": "Өтінім қалдыру", "Что вам нужно?": "Сізге не қажет?", "Найти запчасть": "Қосалқы бөлшек табу", "Авторазборы / Б/У запчасти": "Автобөлшектеу / Қолданылған бөлшектер", "Магазин запчастей": "Қосалқы бөлшектер дүкені", "AG Parts — фильтры и свечи": "AG Parts — сүзгілер мен оталдыру шамдары", "Комплекты ТО": "Техникалық қызмет жинақтары", "СТО и ремонт": "ТҚКС және жөндеу", "Детейлинг и тюнинг": "Детейлинг және тюнинг", "Оптовые покупки": "Көтерме сатып алу", "Продавцы запчастей": "Қосалқы бөлшек сатушылары", "Выбрать продавца": "Сатушыны таңдау", "Каталог СТО": "ТҚКС каталогы", "Найти станцию": "Станция табу", "Каталог авторазборов": "Автобөлшектеу каталогы", "Товары": "Тауарлар", "Все товары": "Барлық тауарлар", "В наличии": "Қолда бар", "Нет в наличии": "Қолда жоқ", "Развивайте свой бизнес на ZPT.KZ": "ZPT.KZ сайтында бизнесіңізді дамытыңыз", "Расширьте возможности вашего бизнеса": "Бизнесіңіздің мүмкіндіктерін кеңейтіңіз", "Подключить бизнес": "Бизнесті қосу", "Полезные материалы": "Пайдалы материалдар", "Обратная связь": "Кері байланыс", "Мои заявки": "Менің өтінімдерім", "Показать ещё": "Тағы көрсету", "Назад": "Артқа", "На главную": "Басты бетке", "Сбросить": "Тазарту", "Применить": "Қолдану", "Весь Казахстан": "Бүкіл Қазақстан", "Город": "Қала", "Выберите город": "Қаланы таңдаңыз", "Все": "Барлығы", "Марка": "Марка", "Модель": "Модель", "Год": "Жылы", "Двигатель": "Қозғалтқыш", "Выберите": "Таңдаңыз", "Фильтры": "Сүзгілер", "Заявка на Б/У запчасть": "Қолданылған бөлшекке өтінім", "Заявка на ремонт": "Жөндеуге өтінім", "Заявка на детейлинг и тюнинг": "Детейлинг пен тюнингке өтінім", "Выберите, как продолжить": "Жалғастыру тәсілін таңдаңыз", "Опишите задачу и укажите автомобиль.": "Қажет қызметті сипаттап, көлікті көрсетіңіз.", "Посмотрите специализацию и профиль компании.": "Компанияның мамандануын және профилін қараңыз.", "Закрыть": "Жабу", "Политика конфиденциальности": "Құпиялылық саясаты", "Товары пока не добавлены": "Тауарлар әлі қосылмаған", "Кабинет продавца запчастей": "Қосалқы бөлшек сатушысының кабинеті", "Кабинет СТО и детейлинга": "ТҚКС және детейлинг кабинеті", "Кабинет магазина": "Дүкен кабинеті", "Каталог наполняется. Добавьте свой авторазбор через кабинет продавца.": "Каталог толықтырылуда. Сатушы кабинеті арқылы автобөлшектеу орныңызды қосыңыз.", "Поиск": "Іздеу", "Подробнее": "Толығырақ", "В корзину": "Себетке", "Купить": "Сатып алу", "Розница": "Бөлшек сауда", "Цена по запросу": "Баға сұрау бойынша", "Новый": "Жаңа", "Б/у": "Қолданылған", "Все города": "Барлық қалалар", "Каталог продавцов автозапчастей": "Қосалқы бөлшек сатушыларының каталогы", "Отправить заявку": "Өтінім жіберу", "Отправить запрос": "Сұрау жіберу", "Тип транспорта": "Көлік түрі", "Легковые": "Жеңіл көліктер", "Грузовые": "Жүк көліктері", "Страна производителя": "Өндіруші ел", "Категория запчасти": "Бөлшек санаты", "Категория": "Санат", "Артикул": "Артикул", "Телефон": "Телефон", "Описание": "Сипаттама", "Год выпуска (необязательно)": "Шығарылған жылы (міндетті емес)", "Дополнительно": "Қосымша", "Фото": "Фото", "Новый запрос": "Жаңа сұрау", "Название, артикул или описание запчасти": "Бөлшек атауы, артикулы немесе сипаттамасы", "Ищем подходящих продавцов по всему Казахстану": "Бүкіл Қазақстан бойынша сәйкес сатушыларды іздейміз", "СТО / ремонт": "ТҚКС / жөндеу", "Детейлинг / тюнинг": "Детейлинг / тюнинг", "Кабинет продавца": "Сатушы кабинеті", "Кабинет исполнителя": "Орындаушы кабинеті", "Применить фильтры": "Сүзгілерді қолдану", "Сбросить фильтры": "Сүзгілерді тазарту", "Зарегистрироваться продавцом": "Сатушы болып тіркелу", "Зарегистрироваться исполнителем": "Орындаушы болып тіркелу", "Выбрать услугу": "Қызметті таңдау", "Район": "Аудан", "Где искать продавцов?": "Сатушыларды қайдан іздеу керек?", "Только мой город": "Тек менің қалам", "Выбрать города": "Қалаларды таңдау", "Обработку данных": "Деректерді өңдеу", "Найдено продавцов:": "Табылған сатушылар:", "Продавцы не найдены": "Сатушылар табылмады", "Изменить фильтры": "Сүзгілерді өзгерту"};

let lang='ru';
try { lang=localStorage.getItem('zpt-language')==='kk'?'kk':'ru'; } catch (_) {}
const originals=new WeakMap();
function translate(root=document.body){
  root.querySelectorAll('[data-ru][data-kk]').forEach(el=>{el.textContent=el.dataset[lang];});
  root.querySelectorAll('[data-placeholder-ru]').forEach(el=>{el.placeholder=lang==='kk'?el.dataset.placeholderKk:el.dataset.placeholderRu;});
  // Translate interface text only; leave product names, company names and values intact.
  root.querySelectorAll('label,button,a,h1,h2,h3,.hint,.home-parts-scope,.product-card-price-label').forEach(el=>{
    if(el.closest('script,style,select,textarea,[data-ru],.product-title-link,.card-seller-link,.seller-name,.product-card-fitment'))return;
    el.childNodes.forEach(node=>{
      if(node.nodeType!==3)return;
      const current=node.textContent;
      let original=originals.get(node);
      if(!original || (current!==original && current!==original.replace(original.trim(),translations[original.trim()]||original.trim())))original=current;
      if(!translations[original.trim()])return;
      originals.set(node,original);
      node.textContent=lang==='kk'?original.replace(original.trim(),translations[original.trim()]):original;
    });
  });
  document.documentElement.lang=lang;
  document.querySelectorAll('[data-zpt-language]').forEach(b=>{b.innerHTML=lang==='kk'?'<span>RU</span> / <strong>KZ</strong>':'<strong>RU</strong> / <span>KZ</span>';b.setAttribute('aria-label',lang==='kk'?'Переключить на русский':'Қазақшаға ауыстыру');});
}
document.addEventListener('click',e=>{
  if(e.target.closest('[data-zpt-language]')){lang=lang==='ru'?'kk':'ru';try{localStorage.setItem('zpt-language',lang);}catch(_){}translate();}
  if(e.target.closest('[data-zpt-back]')){
    e.preventDefault();
    // A direct link always has a safe home fallback.
    if(document.referrer && new URL(document.referrer).origin===location.origin && history.length>1)history.back();else location.href='/';
  }
});
document.addEventListener('click',async e=>{
  const link=e.target.closest('#showcase-more');
  if(!link || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)return;
  e.preventDefault();if(link.dataset.loading)return;
  link.dataset.loading='1';link.setAttribute('aria-busy','true');
  try{
    const response=await fetch(link.href,{credentials:'same-origin'});
    if(!response.ok)throw new Error('showcase');
    const page=new DOMParser().parseFromString(await response.text(),'text/html');
    const cards=page.querySelectorAll('#interface-products > .product');
    if(!page.querySelector('#interface-products'))throw new Error('showcase');
    const grid=document.querySelector('#interface-products');
    const seen=new Set([...grid.querySelectorAll(':scope > .product')].map(card=>card.dataset.productId));
    cards.forEach(card=>{
      const id=card.dataset.productId;
      if(id && seen.has(id))return;
      grid.append(card);if(id)seen.add(id);
    });
    document.dispatchEvent(new CustomEvent('zpt:products-added',{detail:grid}));
    const next=page.querySelector('#showcase-more');
    if(next)link.href=next.href;else link.parentElement.remove();
    translate();
  }catch(_){location.href=link.href;}
  finally{delete link.dataset.loading;link.removeAttribute('aria-busy');}
});
const city=document.querySelector('#interface-city');
if(city){
  try{city.value=localStorage.getItem('zpt-city')||'';}catch(_){}
  city.addEventListener('change',()=>{try{localStorage.setItem('zpt-city',city.value);}catch(_){} });
  const search=document.querySelector('form.search');
  if(search)search.addEventListener('submit',()=>{
    if(city.value){const input=document.createElement('input');input.type='hidden';input.name='city';input.value=city.value;search.append(input);}
  });
}
// City is a preference, never a change to the request dispatch geography.
const formCity=document.querySelector('#home-city, #city');
if(formCity && !formCity.value){try{const preferred=localStorage.getItem('zpt-city');if(preferred && [...formCity.options].some(o=>o.value===preferred))formCity.value=preferred;}catch(_){} }
translate();
window.addEventListener('pageshow',()=>translate());
// Existing scripts may append errors/options; translate labels without rebuilding forms.
let pending=false;
const observer=new MutationObserver(records=>{
  if(pending || !records.some(r=>r.addedNodes.length))return;
  pending=true;queueMicrotask(()=>{observer.disconnect();translate();observer.observe(document.body,{childList:true,subtree:true});pending=false;});
});
observer.observe(document.body,{childList:true,subtree:true});
})();
