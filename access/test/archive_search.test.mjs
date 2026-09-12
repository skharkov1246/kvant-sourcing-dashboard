// Synthetic fixtures only. No production endpoint, key, corpus, or database used.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createHash } from 'node:crypto';
import worker from '../../public/_worker.js';
import { defaultAcl } from '../acl.js';
import { archiveRoute, archiveApi, archiveQuery, archiveSearchResult, archiveUnitResult, archiveStatusResult } from '../../public/archive_search.js';

const BASE='https://portal.example.test', TEAM='archive-test.cloudflareaccess.com', AUD='synthetic-audience';
const OWNER='owner@example.test', READER='reader@example.test', KEY='sb_secret_SYNTHETIC_ONLY';
const b64=(v)=>Buffer.from(v).toString('base64url');
const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const jwk={...await crypto.subtle.exportKey('jwk',pair.publicKey),kid:'archive-fixture',alg:'RS256'};
async function token(email,aud=AUD) {const data=b64(JSON.stringify({alg:'RS256',kid:jwk.kid}))+'.'+b64(JSON.stringify({email,aud,iss:'https://'+TEAM,exp:Math.floor(Date.now()/1000)+600}));return data+'.'+b64(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',pair.privateKey,new TextEncoder().encode(data)));}
function envFor(){const acl=defaultAcl();acl.defaultRole='guest';acl.users[READER]={role:'guest',sites:['knowledge'],tabs:[]};const box=new Map([['acl:v1',JSON.stringify(acl)]]);const writes=[],assets=[];return {CF_ACCESS_TEAM:TEAM,CF_ACCESS_AUD:AUD,__certs:{keys:[jwk]},ADMIN_EMAILS:OWNER,SUPABASE_SERVICE_KEY:KEY,writes,assets,ACL:{get:async(k,o)=>{const raw=box.get(k)??null;return o?.type==='json'&&raw!==null?JSON.parse(raw):raw;},put:async(...args)=>writes.push(args)},ASSETS:{fetch:async req=>{assets.push(new URL(req.url).pathname);return new Response('<!doctype html><title>ARCHIVE_SHELL</title>',{headers:{'Content-Type':'text/html'}});}}};}
const ctx={waitUntil:()=>{throw new Error('Archive must not enqueue visit/audit work');}};
async function call(env,path,email=OWNER,init={}){const headers=new Headers(init.headers);if(email)headers.set('Cf-Access-Jwt-Assertion',await token(email,init.aud??AUD));return worker.fetch(new Request(BASE+path,{...init,headers}),env,ctx);}
function item(overrides={}){return {extraction_id:'12',unit_key:'page:7',unit_kind:'page',source_kind:'attachment',source_entity_type:null,source_id:'42',title:'SYNTHETIC DOCUMENT',title_truncated:false,text:'SYNTHETIC PRIVATE TEXT',text_length:22,text_truncated:false,is_excerpt:true,locator:{page:7,cell:'B3'},source_created_at:'2025-01-01T00:00:00+00:00',source_updated_at:null,source_version_id:'31',input_blob_sha256:'a'.repeat(64),extraction_output_sha256:'b'.repeat(64),raw_blob_sha256:null,raw_json_pointer:null,text_sha256:createHash('sha256').update(overrides.text??'SYNTHETIC PRIVATE TEXT').digest('hex'),...overrides};}
function searchResult(items=[item()]){return {items,returned:items.length,has_more:false,scope:'imported_archive_text',all_versions:true,full_archive:false,search_mode:'plain_terms',order:'extraction_id_unit_key_asc'};}
function statusResult(){return {scope:'imported_archive_text',all_versions:true,full_archive:false,counts:Object.fromEntries(['objects','object_versions','extraction_versions','text_units','logical_blobs','attachment_observations','import_batches','ingestion_runs'].map(k=>[k,'1'])),last_run:null,storage_files_outside_import_not_searched:true,semantic_understanding_measured:false};}
function response(value,headers={}){return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json',...headers}});}
async function withFetch(fn,body){const old=globalThis.fetch,calls=[];globalThis.fetch=async(...args)=>{calls.push(args);return fn(...args);};try{return await body(calls);}finally{globalThis.fetch=old;}}

test('archive canonical routes and reserved variants',()=>{
  for(const [p,r]of [['/library/archive','page'],['/library/archive/','page'],['/archive.html','page'],['/api/library/archive/search','search'],['/api/library/archive/status','status'],['/api/library/archive/unit','unit']])assert.equal(archiveRoute(p),r);
  for(const p of ['/ARCHIVE.HTML','/%2561rchive.html','/archive.html/extra','/archive_search.js','/library/%61rchive','/api//library/archive/search','/api/library/archive/search/','/api/library/archive/search;foo','/api/library/archive/unknown'])assert.equal(archiveRoute(p),'invalid',p);
  assert.equal(archiveRoute('/api/library/v2'),null);
});

test('anonymous, forged, wrong-audience and non-admin requests perform zero RPC and expose no asset',async()=>{
  await withFetch(()=>{throw new Error('No outbound request expected');},async(calls)=>{
    for(const path of ['/library/archive','/archive.html','/api/library/archive/search?q=AB','/api/library/archive/status','/api/library/archive/unit?extraction_id=12&unit_key=page%3A7']){
      for(const email of [null,READER,'guest@example.test']){const env=envFor(),r=await call(env,path,email);assert.equal(r.status,403);assert.equal(env.assets.length,0);assert.equal(env.writes.length,0);assert.doesNotMatch(await r.text(),/PRIVATE TEXT|ARCHIVE_SHELL/);}
      const r=await call(envFor(),path,OWNER,{aud:'another-application'});assert.equal(r.status,403);
    }
    const env=envFor(),r=await worker.fetch(new Request(BASE+'/api/library/archive/status',{headers:{'Cf-Access-Jwt-Assertion':'e30.e30.invalid'}}),env,ctx);assert.equal(r.status,403);assert.equal(calls.length,0);
  });
});

test('strict missing/corrupt ACL and missing archive audience fail before RPC',async()=>{
  await withFetch(()=>{throw new Error('No request');},async(calls)=>{
    for(const mutate of [env=>{delete env.ACL;},env=>{env.ACL.get=async()=>{throw new Error('SECRET DB FAILURE');};},env=>{env.ACL.get=async()=>({roles:[]});},env=>{delete env.CF_ACCESS_AUD;}]){
      const env=envFor();mutate(env);const r=await call(env,'/api/library/archive/status');assert.equal(r.status,503);assert.doesNotMatch(await r.text(),/SECRET|FAILURE/);assert.equal(env.assets.length,0);
    }assert.equal(calls.length,0);
  });
});

test('admin page is protected and does not write visit/query logs',async()=>{
  const env=envFor();const r=await call(env,'/library/archive');assert.equal(r.status,200);assert.match(await r.text(),/ARCHIVE_SHELL/);assert.deepEqual(env.assets,['/archive.html']);assert.equal(env.writes.length,0);assert.match(r.headers.get('Cache-Control'),/private.*no-store/);assert.equal(r.headers.get('Referrer-Policy'),'no-referrer');assert.match(r.headers.get('Content-Security-Policy'),/connect-src 'self'/);
  assert.equal((await call(env,'/library/archive?secret=not-logged')).status,400);
});

test('portal shows archive entry only to administrators',async()=>{
  for(const email of [OWNER,READER]){const env=envFor();const r=await worker.fetch(new Request(BASE+'/',{headers:{'Cf-Access-Jwt-Assertion':await token(email)}}),env,{waitUntil:p=>Promise.resolve(p).catch(()=>{})});const html=await r.text();assert.equal(r.status,200);assert.equal(html.includes('href="/library/archive"'),email===OWNER);}
});

test('methods, invalid and unknown parameters fail before RPC',async()=>{
  await withFetch(()=>{throw new Error('No request');},async(calls)=>{
    for(const path of ['/api/library/archive/search?q=A','/api/library/archive/search?q=AB&q=CD','/api/library/archive/search?q=AB&url=https://evil.test','/api/library/archive/search?q=AB&limit=0','/api/library/archive/search?q=AB&limit=51','/api/library/archive/search?q=AB&limit=2.5','/api/library/archive/search?q=AB&source_kind=secret_table','/api/library/archive/search?q=A%00B','/api/library/archive/status?q=AB','/api/library/archive/unit?extraction_id=0&unit_key=x','/api/library/archive/unit?extraction_id=9223372036854775808&unit_key=x','/api/library/archive/unit?extraction_id=12&unit_key='])assert.equal((await call(envFor(),path)).status,400,path);
    for(const method of ['POST','PUT','DELETE','PATCH','HEAD','OPTIONS'])assert.equal((await call(envFor(),'/api/library/archive/status',OWNER,{method})).status,405,method);
    assert.equal(calls.length,0);
  });
});

test('query handles Unicode codepoints, byte limits, literal syntax and source enum',()=>{
  const url=q=>new URL(BASE+'/api/library/archive/search?'+new URLSearchParams({q}));
  assert.equal(archiveQuery(url('😀'.repeat(200)),'search').search_query.length,400);
  assert.throws(()=>archiveQuery(url('😀'.repeat(201)),'search'));
  assert.equal(archiveQuery(url("PN-42 | ' SELECT * ;"),'search').search_query,"PN-42 | ' SELECT * ;");
  for(const source_kind of ['crm','activity','timeline','mail','chat','task','attachment','other'])assert.equal(archiveQuery(new URL(BASE+'/api/library/archive/search?'+new URLSearchParams({q:'AB',source_kind})),'search').source_kind,source_kind);
});

test('one fixed read RPC uses only server key, strips all client headers, returns bounded allowlisted JSON',async()=>{
  await withFetch(()=>response({...searchResult(),server_secret:'MUST_NOT_FORWARD'}),async(calls)=>{
    const env=envFor();const r=await call(env,'/api/library/archive/search?q=PN-42&source_kind=attachment',OWNER,{headers:{Cookie:'PRIVATE_COOKIE','Authorization':'Bearer CLIENT_FAKE','apikey':'CLIENT_FAKE','X-Secret':'MUST_NOT_FORWARD'}});
    assert.equal(r.status,200);const out=await r.json();assert.equal(out.items[0].locator.cell,'B3');assert.equal(out.server_secret,undefined);assert.equal(calls.length,1);const [url,init]=calls[0];assert.equal(url,'https://vpjliavuuxjcvtxbthlp.supabase.co/rest/v1/rpc/archive_search_text');assert.equal(init.method,'POST');assert.equal(init.redirect,'manual');assert.ok(init.signal instanceof AbortSignal);assert.deepEqual(JSON.parse(init.body),{search_query:'PN-42',result_limit:20,source_kind:'attachment'});assert.deepEqual(init.headers,{apikey:KEY,'Content-Type':'application/json',Accept:'application/json'});assert.equal(env.writes.length,0);assert.match(r.headers.get('Cache-Control'),/private.*no-store/);assert.equal(r.headers.get('Access-Control-Allow-Origin'),null);
  });
});

test('modern key has no Bearer; legacy JWT key retains Bearer; missing/invalid key has no fallback',async()=>{
  await withFetch(()=>response(statusResult()),async(calls)=>{
    const env=envFor();env.SUPABASE_SERVICE_KEY='legacy.synthetic.jwt';assert.equal((await call(env,'/api/library/archive/status')).status,200);assert.equal(calls[0][1].headers.Authorization,'Bearer legacy.synthetic.jwt');
    for(const key of [undefined,'','anon-key','sb_publishable_fixture','sb_secret_']){const other=envFor();other.SUPABASE_SERVICE_KEY=key;const r=await call(other,'/api/library/archive/status');assert.equal(r.status,503);assert.equal((await r.json()).error,'archive_not_configured');}assert.equal(calls.length,1);
  });
});

test('upstream errors and transport failures never masquerade as empty results or leak upstream details',async()=>{
  for(const status of [301,302,400,401,403,404,409,429,500,502,503])await withFetch(()=>new Response('SECRET URL BODY HEADER TOKEN',{status,headers:{Location:'https://evil.test','X-Secret':'PRIVATE'}}),async(calls)=>{
    const r=await call(envFor(),'/api/library/archive/status');assert.equal(r.status,status===400?400:status===409?409:503);assert.doesNotMatch(await r.text(),/SECRET|TOKEN|evil|PRIVATE/);assert.equal(r.headers.get('Location'),null);assert.equal(calls.length,1);
  });
  for(const name of ['TypeError','AbortError'])await withFetch(()=>{const e=new Error('SECRET TRANSPORT');e.name=name;throw e;},async(calls)=>{const r=await call(envFor(),'/api/library/archive/status');assert.equal(r.status,503);assert.doesNotMatch(await r.text(),/SECRET/);assert.equal(calls.length,1);});
});

test('malformed JSON, wrong types, incorrect counts, overlong preview and response size fail closed',async()=>{
  const wrong=[()=>new Response('not JSON',{headers:{'Content-Type':'application/json'}}),()=>new Response('{}',{headers:{'Content-Type':'text/html'}}),()=>response({...searchResult(),returned:5}),()=>response({...searchResult(),full_archive:true}),()=>response(searchResult([item({text:'x'.repeat(1601),text_length:1601})])),()=>response({...searchResult(),items:Array.from({length:21},()=>item()),returned:21}),()=>response(searchResult(),{'Content-Length':String(1024*1024+1)}),()=>response({padding:'x'.repeat(1024*1024)})];
  for(const make of wrong)await withFetch(make,async()=>{const r=await call(envFor(),'/api/library/archive/search?q=AB');assert.equal(r.status,503);assert.match((await r.json()).error,/archive_response/);});
});

test('empty valid index is distinct from errors and status preserves string counts without rounding',()=>{
  assert.deepEqual(archiveSearchResult(searchResult([]),20).items,[]);
  const status=statusResult();status.counts.text_units='9007199254740993';status.last_run={id:'1',status:'partial',started_at:'2026-09-12T00:00:00Z',finished_at:null,coverage:{complete:false}};assert.equal(archiveStatusResult(status).counts.text_units,'9007199254740993');
  assert.throws(()=>archiveStatusResult({...status,counts:{...status.counts,objects:2}}));assert.throws(()=>archiveStatusResult({...status,last_run:{...status.last_run,status:'unknown'}}));
});

test('full unit must match the requested immutable extraction/key; not found is explicit',async()=>{
  const input={extraction_id:'12',unit_key:'page:7'},value={scope:'imported_archive_text',all_versions:true,full_archive:false,found:true,item:item({is_excerpt:false})};
  assert.equal(archiveUnitResult(value,input).item.text,'SYNTHETIC PRIVATE TEXT');
  assert.throws(()=>archiveUnitResult({...value,item:item({extraction_id:'13',is_excerpt:false})},input));
  assert.throws(()=>archiveUnitResult({...value,item:item({is_excerpt:false,text_truncated:true,text_length:25})},input));
  assert.equal(archiveUnitResult({...value,found:false,item:null},input).found,false);
  await withFetch(()=>response(value),async(calls)=>{const r=await call(envFor(),'/api/library/archive/unit?extraction_id=12&unit_key=page%3A7');assert.equal(r.status,200);assert.equal(calls[0][0].endsWith('/archive_search_unit'),true);assert.deepEqual(JSON.parse(calls[0][1].body),input);});
});

test('raw response provenance is preserved; missing digest, oversized pointer and full-text mismatch fail closed',async()=>{
  const row=item({source_kind:'crm',source_entity_type:'crm_deal',input_blob_sha256:null,raw_blob_sha256:'d'.repeat(64),raw_json_pointer:'/result/0'});
  const out=archiveSearchResult(searchResult([row]),20).items[0];assert.equal(out.raw_blob_sha256,'d'.repeat(64));assert.equal(out.raw_json_pointer,'/result/0');assert.equal(out.text_sha256,row.text_sha256);
  for(const bad of [{text_sha256:null},{extraction_output_sha256:null},{raw_json_pointer:'ж'.repeat(4097)},{raw_blob_sha256:null,raw_json_pointer:'/result'}])assert.throws(()=>archiveSearchResult(searchResult([{...row,...bad}]),20));
  await withFetch(()=>response({found:true,item:item({is_excerpt:false,text_sha256:'f'.repeat(64)}),scope:'imported_archive_text',all_versions:true,full_archive:false}),async()=>{const r=await call(envFor(),'/api/library/archive/unit?extraction_id=12&unit_key=page%3A7');assert.equal(r.status,503);assert.equal((await r.json()).error,'archive_text_integrity');});
});

test('10 second timeout is installed, cleared, and never retries',async()=>{
  const oldSet=globalThis.setTimeout,oldClear=globalThis.clearTimeout;const timers=[],cleared=[];
  globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return 91;};globalThis.clearTimeout=id=>cleared.push(id);
  try{await withFetch(()=>response(statusResult()),async(calls)=>{assert.equal((await archiveApi(new Request(BASE+'/api/library/archive/status'),envFor(),'status')).status,200);assert.equal(calls.length,1);});assert.equal(timers[0].ms,10000);assert.deepEqual(cleared,[91]);}finally{globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;}
});

function fakeUI(fetcher){
  class Element{constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.hidden=false;this.disabled=false;this._text='';}set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent??'').join('');}set innerHTML(_){throw new Error('Unsafe HTML insertion');}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];this._text='';}addEventListener(name,fn){this.listeners[name]=fn;}focus(){}}
  const nodes=new Map();const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);},createElement:tag=>new Element(tag)};
  const html=fs.readFileSync(new URL('../../public/archive.html',import.meta.url),'utf8');const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
  for(const match of html.matchAll(/\bid="([^"]+)"/g))document.getElementById(match[1]);
  const context={document,fetch:fetcher,URLSearchParams,AbortController,TextEncoder,BigInt,Date,Error,setTimeout:()=>1,clearTimeout:()=>{}};vm.runInNewContext(script,context);return {nodes,html,all:()=>[...nodes.values()].map(x=>x.textContent).join(' ')};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('UI renders malicious source text as text, distinguishes absent search from empty scope and has no persistence',async()=>{
  const calls=[],bad='<script>SECRET_XSS()</script>';const ui=fakeUI(async url=>{calls.push(url);return response(url.endsWith('/status')?statusResult():searchResult([item({title:bad,text:bad,text_length:bad.length})]));});await tick();
  assert.equal(calls.length,1);assert.match(ui.all(),/Текстовых фрагментов/);assert.doesNotMatch(ui.html,/localStorage|sessionStorage|indexedDB|innerHTML|document\.write|https:\/\/.*supabase|sb_secret_/);
  ui.nodes.get('query').value='PN-42';await ui.nodes.get('searchForm').listeners.submit({preventDefault(){}});assert.match(ui.nodes.get('results').textContent,/<script>SECRET_XSS/);assert.equal(calls.length,2);assert.equal(calls[1],'/api/library/archive/search?q=PN-42&limit=20');
  assert.match(ui.html,/@media\(max-width:600px\)/);assert.equal((ui.html.match(/<option\b/g)||[]).length,(ui.html.match(/<\/option>/g)||[]).length);
});
test('UI clears stale search and shows 503 as unavailable rather than zero matches',async()=>{
  const ui=fakeUI(async url=>url.endsWith('/status')?response(statusResult()):new Response('{"error":"archive_unavailable"}',{status:503,headers:{'Content-Type':'application/json'}}));await tick();ui.nodes.get('query').value='AB';await ui.nodes.get('searchForm').listeners.submit({preventDefault(){}});assert.match(ui.nodes.get('searchStatus').textContent,/ошибка подключения/);assert.doesNotMatch(ui.nodes.get('searchStatus').textContent,/совпадений не найдено/);ui.nodes.get('clearSearch').listeners.click();assert.equal(ui.nodes.get('query').value,'');assert.equal(ui.nodes.get('results').textContent,'');
});
test('late result after Clear cannot restore a previous query or its private text',async()=>{
  let resolveSearch;const ui=fakeUI(async url=>url.endsWith('/status')?response(statusResult()):new Promise(resolve=>{resolveSearch=resolve;}));await tick();ui.nodes.get('query').value='AB';const pending=ui.nodes.get('searchForm').listeners.submit({preventDefault(){}});await tick();ui.nodes.get('clearSearch').listeners.click();resolveSearch(response(searchResult()));await pending;assert.equal(ui.nodes.get('results').textContent,'');assert.equal(ui.nodes.get('query').value,'');assert.doesNotMatch(ui.nodes.get('searchStatus').textContent,/Найдено фрагментов/);
});
test('full-fragment control fetches exact IDs and renders full source as plain text',async()=>{
  const full='<img src=x onerror=SECRET()> '+ 'ф'.repeat(1700),short=full.slice(0,1600);const calls=[];
  const ui=fakeUI(async url=>{calls.push(url);if(url.endsWith('/status'))return response(statusResult());if(url.includes('/unit?'))return response({found:true,item:item({text:full,text_length:full.length,is_excerpt:false}),scope:'imported_archive_text',all_versions:true,full_archive:false});return response(searchResult([item({text:short,text_length:full.length,text_truncated:true})]));});await tick();ui.nodes.get('query').value='AB';await ui.nodes.get('searchForm').listeners.submit({preventDefault(){}});
  const walk=el=>[el,...el.children.flatMap(walk)];const button=walk(ui.nodes.get('results')).find(el=>el.tagName==='button'&&el.textContent==='Прочитать полный фрагмент');assert.ok(button);await button.listeners.click();assert.equal(calls.at(-1),'/api/library/archive/unit?extraction_id=12&unit_key=page%3A7');assert.match(ui.nodes.get('results').textContent,/<img src=x onerror=SECRET\(\)>/);assert.match(ui.nodes.get('results').textContent,/Полный фрагмент/);assert.equal(button.hidden,true);
});
