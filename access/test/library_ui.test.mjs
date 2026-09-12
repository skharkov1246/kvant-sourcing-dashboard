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
const defaultArticles=[
 {id:'k',segment_id:'engine',title:'Пример проверки',topic:'Опыт',body:'## Вывод\n\nТекст **подтверждён** и `<img src=x onerror=bad()>`. [опасно](javascript:bad) [источник](https://example.org/source).\n\n| Артикул | Статус |\n|---|---|\n| 0007 | Проверить |',confidence:'partial',sources:{kind:'knowledge',references:[{title:'Документ',sha256:'a'.repeat(64),locator:{page:1}}],open_questions:['Уточнить исполнение']},updated_at:'2026-09-10'},
 {id:'c',segment_id:'engine',title:'Пример компонента',body:'Описание детали',sources:{kind:'component',component_fields:{part_number:'0007',family:'Кольца',quantity:12,unit:'комплект',priced:true}}},
 {id:'p1',segment_id:'engine',title:'Пример цены USD',body:'Историческая цена',sources:{kind:'price',price_fields:{part_number:'0007',amount:'10.00',currency:'USD',unit:'комплект',price_type:'Расценка из сделки',supplier:'Автор не подтверждён',price_date:'2026-07-01'}}},
 {id:'p2',segment_id:'engine',title:'Пример цены EUR',body:'Другая цена',sources:{kind:'price',price_fields:{part_number:'0007',amount:'20.00',currency:'EUR',unit:'комплект',price_type:'Наше КП заказчику',supplier:'Компания',price_date:'2026-07-02'}}},
 {id:'s',segment_id:'engine',title:'Пример поставщика',body:'Компания требует проверки',sources:{kind:'supplier',supplier_fields:{name:'Компания примера',role:'unknown'}}}
];

function syntheticServiceArticle(){return {id:'service-example',segment_id:'engine',title:'Учебная сервисная заметка',topic:'Сервис',body:'Исходный текст Ω остаётся полностью.',confidence:'partial',sources:{kind:'knowledge',references:[{title:'Учебный источник',url:'https://example.test/reference'}],service_knowledge:{schema_version:1,equipment_family:{id:'rotating-example',label:'Учебное вращающееся оборудование'},model_scope:{level:'generic',manufacturers:[],models:[],note:'Без установленной модели'},assembly:'Учебный подшипниковый узел',service_stage:'diagnostics',evidence:[{reference_index:0,claim:'Учебный факт'}],applicability:{status:'generic_reference',note:'Не установлена для агрегата',limits:['Нужно уточнить исполнение']},diagnostic_checks:[{check:'Учебная карта проверки',method:null,result_interpretation:'Обсудить признаки',basis:'proposed_workflow',reference_indices:[0]},{check:'Учебный пример источника',method:'Метод прямо указан в примере',result_interpretation:null,basis:'source_guidance',reference_indices:[0]}],repair_decision:{status:'assessment_framework',note:'Не является выполненным ремонтом',reference_indices:[0]},customer_content:false,engineering_procedure:false}}};}
async function setup(options={}){
 const articles=options.articles||defaultArticles;
 const optionsForSetup=options;const {mobile=false,failure=false,admin=true}=options;let posts=0;const saved=[];
 const map={};for(const [id,def]of Object.entries(elementDefs)){const el=new Element(def.tag);Object.assign(el.attrs,def.attrs);el.id=id;el.hidden=Object.hasOwn(def.attrs,'hidden');el.disabled=Object.hasOwn(def.attrs,'disabled');map[id]=el;}
 map.recordForm.querySelectorAll=()=>Object.values(map).filter(e=>e.id?.startsWith('record')&&['INPUT','SELECT','TEXTAREA'].includes(e.tagName));
 const body=new Element('body'),allCreated=[],historyLog=[],requests=[];let loc=new URL('https://portal.example/library');
 const doc={body,getElementById:id=>map[id]||allCreated.find(e=>e.id===id),createElement:tag=>{const e=new Element(tag);allCreated.push(e);return e;},createElementNS:(_,tag)=>{const e=new Element(tag);allCreated.push(e);return e;},createTextNode:t=>Object.assign(new Element('#text'),{textContent:t})};
 const context={document:doc,window:{matchMedia:()=>({matches:mobile}),addEventListener(){}},get location(){return loc;},history:{replaceState:(_a,_b,path)=>{loc=new URL(path,loc);historyLog.push(path);}},URL,URLSearchParams,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:crypto.randomUUID},console,
 fetch:async(url,options)=>{requests.push({url,options});
 if(optionsForSetup.v2){const u=new URL(url,'https://portal.example'),json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});const counts={knowledge:1000,component:1000,supplier:1000,price:1000};
  if(u.pathname==='/api/library/v2')return optionsForSetup.manifestFailure?json({error:'library_unavailable'},503):json({version:2,revision:'r2',published_at:'2026-09-11',segments:optionsForSetup.segments?.map(s=>({...s,article_count:4000,counts_by_kind:counts}))||[{id:'engine',name:'Пример оборудования',note:'Описание',article_count:4000,counts_by_kind:counts}],article_count:4000,admin,...(optionsForSetup.serviceFacets?{capabilities:{service_filters:true},service_facets:optionsForSetup.serviceFacets}:{})});
  if(u.pathname==='/api/library/v2/article'){const a=articles.find(a=>a.id===u.searchParams.get('id'));return json({revision:'r2',article:a});}
  if(u.pathname==='/api/library/v2/articles'){if(optionsForSetup.slowKnowledge&&u.searchParams.get('kind')==='knowledge')await new Promise(r=>setTimeout(r,60));if(optionsForSetup.pageFailure)return json({error:'missing_shard'},503);const next=u.searchParams.get('cursor'),q=u.searchParams.get('q'),kind=u.searchParams.get('kind');const serviceFilter=u.searchParams.has('equipment_family')||u.searchParams.has('service_stage');const selected=articles.filter(a=>!kind||(a.sources.kind===kind)).filter(a=>{if(!serviceFilter)return true;const v=a.sources.service_knowledge;return v&&(!u.searchParams.has('equipment_family')||v.equipment_family?.id===u.searchParams.get('equipment_family'))&&(!u.searchParams.has('service_stage')||v.service_stage===u.searchParams.get('service_stage'));});const items=((q||serviceFilter)?(next?(serviceFilter?selected:[articles[0],articles[1]]):[]):next?selected.slice(1):selected.slice(0,1)).map(({body,...a})=>({...a,excerpt:'Краткая карточка без полного текста',...(optionsForSetup.truncatedSources?{sources:{kind:a.sources.kind},sources_truncated:true}:{})}));return json({revision:'r2',items,next_cursor:next?null:'p2',complete:!!next,scope_total:4000,scanned:25,matched_in_batch:items.length,total:q||serviceFilter?null:4000});}
 }
 if(url==='/api/library/v2')return new Response(JSON.stringify({error:'library_v2_unavailable'}),{status:404,headers:{'Content-Type':'application/json'}});if(failure&&url==='/api/library')return {status:503,ok:false,headers:new Headers({'Content-Type':'application/json'})};if(url.startsWith('/admin/library/drafts?'))return new Response(JSON.stringify({drafts:saved,cursor:null,list_complete:true}),{headers:{'Content-Type':'application/json'}});
 if(url==='/admin/library/drafts'){posts++;const article=JSON.parse(options.body);saved.push({id:article.id,title:article.title,segment_id:article.segment_id,status:'pending',created_at:article.updated_at});if(optionsForSetup.uncertainFirst&&posts===1)throw new Error('Connection lost after server save');return new Response(JSON.stringify({ok:true,id:article.id,status:'pending',queued:true,sync_requested:optionsForSetup.syncRequested!==false}),{headers:{'Content-Type':'application/json'}});}return new Response(JSON.stringify(url==='/api/rights'?{sites:['gt'],admin:true}:{segments:optionsForSetup.segments||[{id:'engine',name:'Пример оборудования',note:'Описание'},{id:'gtu',name:'ГТУ',note:'Газотурбинные установки'}],articles,published_at:'2026-09-11',admin}),{headers:{'Content-Type':'application/json'}});}};
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
test('service families come from the full manifest and empty partial results continue',async()=>{
 const item=syntheticServiceArticle(),facets={schema_version:1,article_count:1,equipment_families:[{id:'rotating-example',label:'Учебное вращающееся оборудование',count:1}],service_stages:[{id:'diagnostics',count:1}]};
 const h=await setup({v2:true,articles:[item,...defaultArticles],serviceFacets:facets});
 assert.equal(h.map.serviceFilters.hidden,false);assert.match(h.map.serviceFamily.textContent,/Учебное вращающееся оборудование/);assert.equal(h.requests.some(r=>r.url.includes('/articles?')),false);
 h.map.serviceFamily.value='rotating-example';await h.map.serviceFamily.fire('change');h.map.serviceStage.value='diagnostics';await h.map.serviceStage.fire('change');
 const request=new URL(h.requests.filter(r=>r.url.includes('/articles?')).at(-1).url,'https://portal.example');
 assert.equal(request.searchParams.get('equipment_family'),'rotating-example');assert.equal(request.searchParams.get('service_stage'),'diagnostics');assert.equal(request.searchParams.get('kind'),'knowledge');assert.equal(request.searchParams.get('revision'),'r2');
 assert.match(h.map.pageStatus.textContent,/ещё не завершён/);assert.doesNotMatch(h.map.emptyList.textContent,/Ничего не найдено/);
 await h.map.nextPage.fire('click');assert.match(h.map.pageStatus.textContent,/Всего совпадений: 1/);assert.match(h.map.articleList.textContent,/Диагностика/);
 await h.map.articleList.children[0].children[0].fire('click');
 assert.match(h.map.reader.textContent,/Предлагаемая структура проверки/);assert.match(h.map.reader.textContent,/Проверка по источнику/);assert.match(h.map.reader.textContent,/оснований и контекста, а не утверждённая методика/);
 assert.match(h.map.reader.textContent,/Общая справочная область/);assert.match(h.map.reader.textContent,/Не является выполненным ремонтом/);assert.match(h.map.reader.textContent,/Исходный текст Ω остаётся полностью/);
 assert.ok(h.historyLog.some(v=>v.includes('equipment_family=rotating-example')&&v.includes('service_stage=diagnostics')));
 await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Компоненты').fire('click');
 const component=new URL(h.requests.filter(r=>r.url.includes('/articles?')).at(-1).url,'https://portal.example');assert.equal(component.searchParams.has('service_stage'),false);assert.equal(component.searchParams.has('equipment_family'),false);
});
test('old published manifests do not display unsupported service filters',async()=>{const h=await setup({v2:true});assert.equal(h.map.serviceFilters.hidden,true);});

test('full service evidence takes priority over an unrelated same-named summary field',async()=>{
 const article=syntheticServiceArticle();article.sources.service_summary={schema_version:1,service_stage:'inspection',equipment_family:{id:'wrong',label:'Ошибочная подпись'},assembly:'Посторонний узел'};
 const before=JSON.stringify(article.sources),h=await setup({articles:[article]});await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Знания').fire('click');
 assert.match(h.map.articleList.textContent,/Диагностика/);assert.doesNotMatch(h.map.articleList.textContent,/Ошибочная подпись|Посторонний узел|Осмотр/);
 assert.match(h.map.reader.textContent,/Диагностика/);assert.equal(JSON.stringify(article.sources),before);
});

test('service citations preserve original reference numbers when unused slots are empty',async()=>{
 const article=syntheticServiceArticle(),service=article.sources.service_knowledge;
 article.sources.references=[null,article.sources.references[0]];
 service.evidence[0].reference_index=1;service.diagnostic_checks.forEach(c=>c.reference_indices=[1]);service.repair_decision.reference_indices=[1];
 const h=await setup({articles:[article]});await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Знания').fire('click');
 const refs=descendants(h.map.reader).filter(e=>e.tagName==='LI'&&e.className==='source');
 assert.equal(refs.length,1);assert.equal(refs[0].getAttribute('value'),'2');assert.match(refs[0].textContent,/^№ 2 · Учебный источник/);assert.match(h.map.reader.textContent,/№\s*2/);
 assert.deepEqual(article.sources.references,[null,{title:'Учебный источник',url:'https://example.test/reference'}]);
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

function componentFixture(fields,extra={}){const component_fields={part_number:'T-007',oem:'Example OEM',...fields};return {id:'part:synthetic',segment_id:'engine',title:[component_fields.oem,component_fields.part_number].filter(Boolean).join(' '),body:'Учебный каталог. Подтверждена запись источника.',confidence:'confirmed',sources:{kind:'component',component_fields},...extra};}
async function openComponent(h){await h.map.equipmentCards.children[0].fire('click');await button(h.map.kindTabs,'Компоненты').fire('click');await button(h.map.recordCatalog,'T-007').fire('click');}
test('explicit component name and purpose appear consistently without inventing a supplier',async()=>{
 const h=await setup({articles:[componentFixture({name:'Крышка корпуса',family:'Исходная группа',purpose:'Закрывает корпус учебного узла'})]});await openComponent(h);
 assert.match(h.map.recordCatalog.textContent,/Крышка корпуса — Example OEM T-007/);assert.match(h.map.articleList.textContent,/Крышка корпуса — Example OEM T-007/);assert.equal(h.map.reader.children.find(e=>e.tagName==='HEADER').children.find(e=>e.id==='articleTitle').textContent,'Крышка корпуса — Example OEM T-007');
 assert.match(h.map.reader.textContent,/Назначение в узле: Закрывает корпус учебного узла/);assert.match(h.map.reader.textContent,/Статус проработки закупки здесь не установлен/);assert.doesNotMatch(h.map.reader.textContent,/Поставщик по этой позиции: Example OEM|Готово к закупке/);
});
test('only an explicit known family supplies a Russian type, while an unknown family remains raw',async()=>{
 const known=await setup({articles:[componentFixture({name:'Example OEM T-007',family:'spherical_roller'})]});await openComponent(known);assert.match(known.map.recordCatalog.textContent,/Сферический роликовый подшипник — Example OEM T-007/);assert.match(known.map.reader.textContent,/Сферический роликовый подшипник — Example OEM T-007/);assert.match(known.map.reader.textContent,/Назначение в узле: не установлено/);
 const unknown=await setup({articles:[componentFixture({name:'T-007',family:'source_family_undecoded'})]});await openComponent(unknown);assert.match(unknown.map.reader.textContent,/source_family_undecoded/);assert.match(unknown.map.reader.textContent,/Описание позиции ещё не установлено/);assert.doesNotMatch(unknown.map.reader.textContent,/Сферический роликовый подшипник/);
});
test('a validated historical supplier relation opens the exact unloaded article and correct section',async()=>{
 const supplier={id:'supplier:actual',segment_id:'engine',title:'Досье учебного кандидата',body:'Прежняя запись, текущая поставка не проверена.',confidence:'confirmed',sources:{kind:'supplier',supplier_fields:{name:'Учебная компания',role:'unknown'}}};
 const part=componentFixture({name:'Крышка корпуса'});part.sources.library_relations={version:1,producer:'publisher-v2',candidate_suppliers:[{article_id:supplier.id,name:'Учебная компания',relation_type:'historical_supplier_candidate',position_id:7,json_pointer:'/6',source_url:'https://example.org/legacy'},{article_id:'https://invalid.example',name:'Не ссылка на карточку',relation_type:'historical_supplier_candidate'},{article_id:'supplier:guessed',name:'Неподтверждённая догадка',relation_type:'confirmed_supplier'}]};
 const h=await setup({v2:true,articles:[part,supplier]});await openComponent(h);
 assert.match(h.map.reader.textContent,/Исторический кандидат/);assert.match(h.map.reader.textContent,/Статус проработки закупки здесь не установлен/);assert.doesNotMatch(h.map.reader.textContent,/Не ссылка на карточку|Неподтверждённая догадка/);
 const before=h.requests.filter(r=>r.url.includes('/article?')).length;await button(h.map.reader,'Учебная компания →').fire('click');assert.equal(h.requests.filter(r=>r.url.includes('/article?')).length,before+1);
 assert.match(h.map.reader.textContent,/Досье учебного кандидата/);assert.equal(button(h.map.kindTabs,'Поставщики').attrs['aria-pressed'],'true');assert.match(h.historyLog.at(-1),/section=supplier/);assert.match(h.historyLog.at(-1),/article=supplier%3Aactual/);
 assert.ok(h.requests.some(r=>r.url.includes('/article?')&&new URL(r.url,'https://example.org').searchParams.get('id')===supplier.id));assert.equal(h.requests.some(r=>r.url==='/api/library'),false);
});
test('explicit position type preserves model and full order code; a stated company is not certified',async()=>{
 const part=componentFixture({part_number:'T-007_',name:'Example Model X',position_type:'Компрессорная головка / блок',family:'Source paragraph without a translated equipment type'});part.sources.supplier_fields={name:'Компания из исходного документа'};
 const h=await setup({articles:[part]});await openComponent(h);assert.match(h.map.reader.textContent,/Компрессорная головка \/ блок — Example Model X · T-007_/);assert.match(h.map.reader.textContent,/Компания, указанная в записи: Компания из исходного документа/);assert.match(h.map.reader.textContent,/без подтверждения текущей поставки/);assert.doesNotMatch(h.map.reader.textContent,/Водяной насос|Винтовой компрессор/);
});
test('narrow family translations preserve pump-stage and chain distinctions',async()=>{
 for(const [family,label]of [['hydraulic_gear_pump_stage','Секция шестерённого гидравлического насоса'],['double_row_ball','Двухрядный шариковый подшипник'],['Втулочная приводная цепь','Втулочная приводная цепь']]){
  const h=await setup({articles:[componentFixture({name:'T-007',family})]});await openComponent(h);assert.match(h.map.reader.textContent,new RegExp(label+' — Example OEM T-007'));assert.doesNotMatch(h.map.reader.textContent,/Роликовая цепь|Гидравлический насос в сборе|Двухрядный радиальный/);
 }
});
test('a displayed Russian component family is also searchable in legacy data',async()=>{
 const h=await setup({articles:[componentFixture({name:'T-007',family:'spherical_roller'})]});h.map.search.value='подшипник';await h.map.search.fire('input');assert.match(h.map.resultCount.textContent,/1 материал найдено/);assert.match(h.map.articleList.textContent,/Сферический роликовый подшипник/);
});
test('qualified tool families preserve distinct types and never guess from a part-number prefix',async()=>{
 for(const [family,label]of [['R200','Твердосплавное центровочное сверло'],['R7131','Твердосплавное ступенчатое сверло'],['RC4P','Твердосплавное пилотное сверло']]){
  const h=await setup({segments:[{id:'welding',name:'Учебный инструмент'}],articles:[componentFixture({name:'Dormer Pramet T-007',oem:'Dormer Pramet',family},{segment_id:'welding'})]});await openComponent(h);assert.match(h.map.reader.textContent,new RegExp(label+' — Dormer Pramet T-007'));
 }
 const mismatch=await setup({articles:[componentFixture({family:'R200',name:'T-007'})]});await openComponent(mismatch);assert.match(mismatch.map.reader.textContent,/Описание позиции ещё не установлено/);assert.doesNotMatch(mismatch.map.reader.textContent,/центровочное сверло/);
 const absent=await setup({segments:[{id:'welding',name:'Учебный инструмент'}],articles:[componentFixture({oem:'Dormer Pramet',part_number:'R200T-007',name:'Dormer Pramet R200T-007'},{segment_id:'welding'})]});await openComponent(absent);assert.match(absent.map.reader.textContent,/Описание позиции ещё не установлено/);
});
test('water descriptions distinguish accessories, housings and cartridges without selecting a conflicting material',async()=>{
 const fixture=(fields,values={})=>{const a=componentFixture({oem:'Pentair',name:'Pentair T-007',...fields},{segment_id:'water'});a.sources.typedfields={specification:{catalogue_fields_as_printed:values}};return a;};
 const open=async a=>{const h=await setup({segments:[{id:'water',name:'Учебная водоподготовка'}],articles:[a]});await openComponent(h);return h;};
 const gasket=await open(fixture({family:'PENTEK ST SERIES STAINLESS STEEL FILTER HOUSINGS',is_accessory:true},{DESCRIPTION:'ST Gasket , BUNA-N'}));assert.match(gasket.map.reader.textContent,/Прокладка ST · ST Gasket , BUNA-N — Pentair T-007/);assert.match(gasket.map.reader.textContent,/Описание в каталоге \(оригинал\)/);assert.doesNotMatch(gasket.map.reader.textContent,/Корпус фильтра — Pentair T-007/);
 const conflict=fixture({family:'PENTEK SLIM LINE FILTER HOUSINGS',is_accessory:true});conflict.sources.original_record={catalogue_observations:[{description:'Viton'},{description:'Silicone'}]};const c=await open(conflict);assert.match(c.map.reader.textContent,/Принадлежность системы фильтрации — Pentair T-007/);assert.doesNotMatch(c.map.reader.textContent,/Принадлежность системы фильтрации · Viton|Принадлежность системы фильтрации · Silicone/);
 const cartridge=await open(fixture({family:'PENTEK QUICK-CHANGE FILTRATION SYSTEMS',is_accessory:false},{'CARTRIDGE COLOR':'White'}));assert.match(cartridge.map.reader.textContent,/Сменный картридж фильтра — Pentair T-007/);assert.doesNotMatch(cartridge.map.reader.textContent,/Система фильтрации — Pentair T-007/);
 const unknown=await open(fixture({family:'Other equipment family',is_accessory:true}));assert.match(unknown.map.reader.textContent,/Описание позиции ещё не установлено/);
});
test('component filter labels retain raw family and do not pick one type from a mixed group',async()=>{
 const family='PENTEK SLIM LINE FILTER HOUSINGS';const a=componentFixture({oem:'Pentair',name:'Pentair T-007',family,is_accessory:false},{segment_id:'water'});const b=componentFixture({oem:'Pentair',name:'Pentair T-008',part_number:'T-008',family,is_accessory:true},{id:'second:synthetic',segment_id:'water'});
 const mixed=await setup({segments:[{id:'water',name:'Учебная водоподготовка'}],articles:[a,b]});await mixed.map.equipmentCards.children[0].fire('click');await button(mixed.map.kindTabs,'Компоненты').fire('click');assert.equal(mixed.map.subtype.children.find(x=>x.value===family).textContent,family);
 const tool=await setup({segments:[{id:'welding',name:'Учебный инструмент'}],articles:[componentFixture({oem:'Dormer Pramet',name:'Dormer Pramet T-007',family:'R023'},{segment_id:'welding'})]});await tool.map.equipmentCards.children[0].fire('click');await button(tool.map.kindTabs,'Компоненты').fire('click');const option=tool.map.subtype.children.find(x=>x.value==='R023');assert.equal(option.textContent,'Короткое твердосплавное сверло · R023');assert.equal(option.value,'R023');
});
