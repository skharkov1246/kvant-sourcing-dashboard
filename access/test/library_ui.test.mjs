// Synthetic UI fixtures only; no private library data or browser dependencies.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import test from 'node:test';
const html=fs.readFileSync(new URL('../../public/library.html',import.meta.url),'utf8');
const source=html.split('<script>')[1].split('</script>')[0];
const elementDefs={};
for(const match of html.split('<script>')[0].matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)){
 const id=match[2].match(/\bid="([^"]+)"/);if(!id)continue;
 assert.ok(!elementDefs[id[1]],'duplicate element ID: '+id[1]);
 elementDefs[id[1]]={tag:match[1],attrs:{...( /\shidden(?:\s|$)/.test(match[2])?{hidden:''}:{}),...( /\sdisabled(?:\s|$)/.test(match[2])?{disabled:''}:{})}};
}
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.events={};this.hidden=false;this.disabled=false;this.value='';this._text='';this.className='';this.classList={toggle:(k,on)=>{const list=new Set(this.className.split(' ').filter(Boolean));if(on)list.add(k);else list.delete(k);this.className=[...list].join(' ');}};}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
 append(...nodes){for(const n of nodes)this.children.push(typeof n==='string'?Object.assign(new Element('#text'),{textContent:n}):n);}
 replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k];}
 addEventListener(k,fn){(this.events[k] ||= []).push(fn);}querySelectorAll(tags){const allowed=tags.split(',').map(t=>t.toUpperCase());return descendants(this).filter(e=>allowed.includes(e.tagName));}async fire(k,event={}){for(const fn of this.events[k]||[])await fn({preventDefault(){},...event});}
 querySelector(tag){return descendants(this).find(e=>e.tagName===tag.toUpperCase())||null;}focus(){}scrollIntoView(){}reportValidity(){return true;}reset(){}
}
function descendants(root){return [root,...root.children.flatMap(descendants)];}
const articles=[
 {id:'k',segment_id:'engine',title:'Пример проверки',topic:'Опыт',body:'## Вывод\n\nТекст **подтверждён** и `<img src=x onerror=bad()>`. [опасно](javascript:bad) [источник](https://example.org/source).\n\n| Артикул | Статус |\n|---|---|\n| 0007 | Проверить |',confidence:'partial',sources:{kind:'knowledge',references:[{title:'Документ',sha256:'a'.repeat(64),locator:{page:1}}],open_questions:['Уточнить исполнение']},updated_at:'2026-09-10'},
 {id:'c',segment_id:'engine',title:'Пример компонента',body:'Описание детали',sources:{kind:'component',component_fields:{part_number:'0007',family:'Кольца',quantity:12,unit:'комплект',priced:true}}},
 {id:'p1',segment_id:'engine',title:'Пример цены USD',body:'Историческая цена',sources:{kind:'price',price_fields:{part_number:'0007',amount:'10.00',currency:'USD',unit:'комплект',price_type:'Расценка из сделки',supplier:'Автор не подтверждён',price_date:'2026-07-01'}}},
 {id:'p2',segment_id:'engine',title:'Пример цены EUR',body:'Другая цена',sources:{kind:'price',price_fields:{part_number:'0007',amount:'20.00',currency:'EUR',unit:'комплект',price_type:'Наше КП заказчику',supplier:'Компания',price_date:'2026-07-02'}}},
 {id:'s',segment_id:'engine',title:'Пример поставщика',body:'Компания требует проверки',sources:{kind:'supplier',supplier_fields:{name:'Компания примера',role:'unknown'}}}
];
async function setup(options={}){
 const optionsForSetup=options;const {mobile=false,failure=false,admin=true}=options;let posts=0;const saved=[];
 const map={};for(const [id,def]of Object.entries(elementDefs)){const el=new Element(def.tag);Object.assign(el.attrs,def.attrs);el.id=id;el.hidden=Object.hasOwn(def.attrs,'hidden');el.disabled=Object.hasOwn(def.attrs,'disabled');map[id]=el;}
 map.recordForm.querySelectorAll=()=>Object.values(map).filter(e=>e.id?.startsWith('record')&&['INPUT','SELECT','TEXTAREA'].includes(e.tagName));
 const body=new Element('body'),allCreated=[],historyLog=[],requests=[];let loc=new URL('https://portal.example/library');
 const doc={body,getElementById:id=>map[id]||allCreated.find(e=>e.id===id),createElement:tag=>{const e=new Element(tag);allCreated.push(e);return e;},createElementNS:(_,tag)=>{const e=new Element(tag);allCreated.push(e);return e;},createTextNode:t=>Object.assign(new Element('#text'),{textContent:t})};
 const context={document:doc,window:{matchMedia:()=>({matches:mobile}),addEventListener(){}},get location(){return loc;},history:{replaceState:(_a,_b,path)=>{loc=new URL(path,loc);historyLog.push(path);}},URL,URLSearchParams,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:crypto.randomUUID},console,
 fetch:async(url,options)=>{requests.push({url,options});
 if(optionsForSetup.v2){const u=new URL(url,'https://portal.example'),json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});const counts={knowledge:1000,component:1000,supplier:1000,price:1000};
  if(u.pathname==='/api/library/v2')return optionsForSetup.manifestFailure?json({error:'library_unavailable'},503):json({version:2,revision:'r2',published_at:'2026-09-11',segments:[{id:'engine',name:'Пример оборудования',note:'Описание',article_count:4000,counts_by_kind:counts}],article_count:4000,admin});
  if(u.pathname==='/api/library/v2/article'){const a=articles.find(a=>a.id===u.searchParams.get('id'));return json({revision:'r2',article:a});}
  if(u.pathname==='/api/library/v2/articles'){if(optionsForSetup.slowKnowledge&&u.searchParams.get('kind')==='knowledge')await new Promise(r=>setTimeout(r,60));if(optionsForSetup.pageFailure)return json({error:'missing_shard'},503);const next=u.searchParams.get('cursor'),q=u.searchParams.get('q'),kind=u.searchParams.get('kind');const selected=articles.filter(a=>!kind||(a.sources.kind===kind));const items=(q?(next?[articles[0],articles[1]]:[]):next?selected.slice(1):selected.slice(0,1)).map(({body,...a})=>({...a,excerpt:'Краткая карточка без полного текста',...(optionsForSetup.truncatedSources?{sources:{kind:a.sources.kind},sources_truncated:true}:{})}));return json({revision:'r2',items,next_cursor:next?null:'p2',complete:!!next,scope_total:4000,scanned:25,matched_in_batch:items.length,total:q?null:4000});}
 }
 if(url==='/api/library/v2')return new Response(JSON.stringify({error:'library_v2_unavailable'}),{status:404,headers:{'Content-Type':'application/json'}});if(failure&&url==='/api/library')return {status:503,ok:false,headers:new Headers({'Content-Type':'application/json'})};if(url.startsWith('/admin/library/drafts?'))return new Response(JSON.stringify({drafts:saved,cursor:null,list_complete:true}),{headers:{'Content-Type':'application/json'}});
 if(url==='/admin/library/drafts'){posts++;const article=JSON.parse(options.body);saved.push({id:article.id,title:article.title,segment_id:article.segment_id,status:'pending',created_at:article.updated_at});if(optionsForSetup.uncertainFirst&&posts===1)throw new Error('Connection lost after server save');return new Response(JSON.stringify({ok:true,id:article.id,status:'pending',queued:true,sync_requested:optionsForSetup.syncRequested!==false}),{headers:{'Content-Type':'application/json'}});}return new Response(JSON.stringify(url==='/api/rights'?{sites:['gt'],admin:true}:{segments:[{id:'engine',name:'Пример оборудования',note:'Описание'},{id:'gtu',name:'ГТУ',note:'Газотурбинные установки'}],articles,published_at:'2026-09-11',admin}),{headers:{'Content-Type':'application/json'}});}};
 vm.runInNewContext(source,context);for(let i=0;i<5;i++)await new Promise(setImmediate);return {map,allCreated,requests,historyLog,body};
}
function button(root,label){const found=descendants(root).find(e=>e.tagName==='BUTTON'&&e.textContent.includes(label));assert.ok(found,'button not found: '+label);return found;}

test('equipment hierarchy, typed records, source catalog and empty sections',async()=>{
 const h=await setup();assert.equal(h.map.equipmentCards.children.length,2);assert.equal(h.map.equipmentLanding.hidden,false);assert.equal(h.map.workspace.hidden,true);assert.equal(h.map.editor.hidden,false);assert.equal(h.map.projectLinks.children.length,1);
 await h.map.equipmentCards.children[0].fire('click');assert.equal(h.map.equipmentOverview.hidden,false);
 await button(h.map.kindTabs,'Компоненты').fire('click');assert.equal(h.map.recordCatalog.hidden,false);assert.match(h.map.recordCatalog.textContent,/0007/);assert.match(h.map.recordCatalog.textContent,/комплект/);
 await button(h.map.recordCatalog,'0007').fire('click');assert.match(h.map.reader.textContent,/Ценовые наблюдения/);assert.match(h.map.reader.textContent,/10.00 USD/);assert.match(h.map.reader.textContent,/20.00 EUR/);
 await button(h.map.kindTabs,'Цены').fire('click');assert.match(h.map.recordCatalog.textContent,/Расценка из сделки/);await button(h.map.recordCatalog,'Цена \/ валюта').fire('click');
 await button(h.map.kindTabs,'Поставщики').fire('click');assert.match(h.map.subtype.textContent,/Роль не установлена/);
 await button(h.map.kindTabs,'Источники').fire('click');assert.equal(h.map.sourceCatalog.hidden,false);assert.match(h.map.sourceCatalog.textContent,/Документ/);
 await button(h.map.segments,'Всё оборудование').fire('click');await h.map.equipmentCards.children[1].fire('click');assert.match(h.map.equipmentOverview.textContent,/материалов пока нет/);assert.equal(h.map.projectLinks.children.length,1);
 await button(h.map.kindTabs,'Знания').fire('click');assert.match(h.map.emptyList.textContent,/Материалов пока нет/);
});
test('safe Markdown preserves tables and literal HTML, and blocks executable links',async()=>{
 const h=await setup();await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Знания').fire('click');
 assert.match(h.map.reader.textContent,/Уточнить исполнение/);assert.ok(descendants(h.map.reader).some(e=>e.tagName==='TABLE'));assert.equal(descendants(h.map.reader).some(e=>e.tagName==='IMG'),false);assert.equal(descendants(h.map.reader).some(e=>e.tagName==='A'&&String(e.href).startsWith('javascript:')),false);assert.match(h.map.reader.textContent,/<img src=x onerror=bad\(\)>/);
 assert.equal(/innerHTML|localStorage|sessionStorage|eval\(/.test(source),false);
});
test('part-number search preserves leading zeroes and mobile selection returns to list',async()=>{
 const h=await setup({mobile:true});h.map.search.value='0007';await h.map.search.fire('input');assert.match(h.map.resultCount.textContent,/4 материала/);
 await button(h.map.clearSearch,'').fire('click');await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Знания').fire('click');assert.equal(h.body.className.includes('has-selection'),false);await h.map.articleList.children[0].children[0].fire('click');assert.equal(h.body.className.includes('has-selection'),true);await button(h.map.reader,'К списку').fire('click');assert.equal(h.body.className.includes('has-selection'),false);
});
function fillDraft(h){for(const [key,value]of Object.entries({recordKind:'supplier',recordSegment:'engine',recordTitle:'Тестовая компания',recordBody:'Тестовое описание',recordSupplier:'Учебная компания',recordRole:'unknown',recordConfidence:'unverified',recordSource:'https://example.org/reference'}))h.map[key].value=value;}
test('uncertain save retries exactly the same immutable draft and timestamp',async()=>{
 const h=await setup({uncertainFirst:true});fillDraft(h);await h.map.recordForm.fire('submit');assert.match(h.map.draftMessage.textContent,/Сохранение не подтверждено/);assert.equal(h.map.recordTitle.disabled,true);
 const first=h.requests.filter(r=>r.url==='/admin/library/drafts')[0];await new Promise(setImmediate);await h.map.recordForm.fire('submit');const posts=h.requests.filter(r=>r.url==='/admin/library/drafts');assert.equal(posts.length,2);assert.equal(posts[1].options.body,first.options.body);const payload=JSON.parse(first.options.body);assert.match(payload.id,/^draft:/);assert.equal(payload.sources.supplier_fields.role,'unknown');assert.match(h.map.draftMessage.textContent,/публикация пока не подтверждена/);assert.equal(h.map.recordTitle.disabled,false);assert.match(h.map.draftQueueList.textContent,/Ожидает синхронизации/);
});
test('failed sync is reported as saved pending, with exact-payload retry',async()=>{
 const h=await setup({syncRequested:false});fillDraft(h);await h.map.recordForm.fire('submit');assert.match(h.map.draftMessage.textContent,/автоматическая отправка не запустилась/);const original=h.requests.find(r=>r.url==='/admin/library/drafts').options.body;await button(h.map.draftQueueList,'Повторить синхронизацию').fire('click');const posts=h.requests.filter(r=>r.url==='/admin/library/drafts');assert.equal(posts[1].options.body,original);assert.match(h.map.draftQueueMessage.textContent,/автоматическая отправка не запустилась/);
});
test('API error differs from an empty library; editor is hidden from non-admins',async()=>{
 const f=await setup({failure:true});assert.equal(f.map.loadStatus.hidden,false);assert.match(f.map.loadStatus.textContent,/Не удалось загрузить/);assert.equal(f.map.equipmentLanding.hidden,true);assert.equal(f.map.editor.hidden,true);
 const h=await setup({admin:false});assert.equal(h.map.editor.hidden,true);assert.equal(h.map.admin.hidden,true);assert.equal(h.requests.some(r=>r.url.startsWith('/admin/')),false);
});

test('v2 loads metadata and one bounded page, then fetches full article on demand',async()=>{
 const h=await setup({v2:true});assert.match(h.map.equipmentCards.textContent,/4000/);assert.equal(h.requests.some(r=>r.url==='/api/library'),false);assert.equal(h.requests.some(r=>r.url.includes('/articles?')),false);
 await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Компоненты').fire('click');assert.match(h.map.recordCatalog.textContent,/0007/);assert.match(h.map.pageStatus.textContent,/4000/);assert.equal(h.map.admin.hidden,true);assert.equal(h.map.editor.hidden,false);
 await button(h.map.recordCatalog,'0007').fire('click');assert.ok(h.requests.some(r=>r.url.includes('/article?')&&r.url.includes('revision=r2')));assert.match(h.map.reader.textContent,/Описание детали/);assert.match(h.map.reader.textContent,/Найти все материалы по артикулу/);
 await h.map.nextPage.fire('click');assert.match(h.map.pageStatus.textContent,/Страница 2/);assert.equal(h.map.nextPage.disabled,true);await h.map.previousPage.fire('click');assert.match(h.map.pageStatus.textContent,/Страница 1/);
});
test('v2 search does not call an incomplete empty page an empty search',async()=>{
 const h=await setup({v2:true});h.map.search.value='подтверждён';await h.map.search.fire('input');await new Promise(r=>setTimeout(r,450));assert.match(h.map.pageStatus.textContent,/ещё не завершён/);assert.equal(h.map.nextPage.disabled,false);assert.doesNotMatch(h.map.emptyList.textContent,/Ничего не найдено/);
 await h.map.nextPage.fire('click');assert.match(h.map.pageStatus.textContent,/Всего совпадений: 2/);assert.equal(h.map.articleList.children.length,2);assert.equal(h.map.nextPage.disabled,true);
 await h.map.previousPage.fire('click');assert.match(h.map.pageStatus.textContent,/ещё не завершён/);await h.map.nextPage.fire('click');assert.match(h.map.pageStatus.textContent,/Всего совпадений: 2/);
});
test('v2 missing data fails visibly and never falls back to stale v1',async()=>{
 const broken=await setup({v2:true,manifestFailure:true});assert.match(broken.map.loadStatus.textContent,/Не удалось загрузить/);assert.equal(broken.requests.some(r=>r.url==='/api/library'),false);
 const h=await setup({v2:true,pageFailure:true});await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Компоненты').fire('click');assert.match(h.map.pageStatus.textContent,/Данные страницы не получены/);assert.equal(h.map.retryPage.hidden,false);assert.equal(h.requests.some(r=>r.url==='/api/library'),false);
});

test('v2 ignores an older page response after equipment section changes',async()=>{
 const h=await setup({v2:true,slowKnowledge:true});const first=h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Компоненты').fire('click');await first;assert.equal(h.map.recordCatalog.hidden,false);assert.match(h.map.recordCatalog.textContent,/0007/);assert.doesNotMatch(h.map.recordCatalog.textContent,/Пример проверки/);
});

test('v2 sources omitted from summaries remain reachable during an active search',async()=>{
 const h=await setup({v2:true,truncatedSources:true});await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Источники').fire('click');assert.match(h.map.sourceCatalog.textContent,/не поместились/);assert.doesNotMatch(h.map.sourceCatalog.textContent,/пока не приложены/);
 h.map.search.value='подтверждён';await h.map.search.fire('input');await new Promise(r=>setTimeout(r,450));await h.map.nextPage.fire('click');await button(h.map.sourceCatalog,'Пример проверки').fire('click');assert.equal(h.map.sourceCatalog.hidden,true);assert.equal(h.map.workspace.hidden,false);assert.match(h.map.reader.textContent,/Уточнить исполнение/);assert.match(h.map.reader.textContent,/Документ/);
});
test('v2 empty or failed component search page is not reported as an empty database',async()=>{
 const h=await setup({v2:true});await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Компоненты').fire('click');h.map.search.value='искомый';await h.map.search.fire('input');await new Promise(r=>setTimeout(r,450));assert.match(h.map.recordCatalog.textContent,/Продолжите поиск/);assert.doesNotMatch(h.map.recordCatalog.textContent,/ничего не найдено/);
 const broken=await setup({v2:true,pageFailure:true});await broken.map.equipmentCards.children[0].fire('click');await button(broken.map.kindTabs,'Компоненты').fire('click');assert.match(broken.map.recordCatalog.textContent,/Не удалось загрузить/);assert.doesNotMatch(broken.map.recordCatalog.textContent,/пока не опубликованы/);
});
