// База ЗИП ходит через собственный воркер: /db/* → Supabase, ключ подставляет
// воркер уже после проверки входа Access. Раньше ключ лежал в отдаваемой странице,
// и любой, кто её открыл, мог обращаться к базе напрямую — мимо сайта и мимо Access.
// Тесты закрепляют: адрес собирается верно, ключ из браузера не проходит, куки
// Access наружу не уходят, и обращения к /db/ не засоряют журнал действий.
// Запуск: node --test access/test/zip_db_proxy.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import worker, { proxyDb, SUPA_ORIGIN } from "../../zip/site/_worker.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

let seen;
const realFetch = globalThis.fetch;
function stub(status = 200, body = "[]") {
  seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), init });
    return new Response(body, { status, headers: { "Content-Type": "application/json" } });
  };
}
test.after(() => { globalThis.fetch = realFetch; });

const req = (url, init) => new Request(url, init);

test("путь и строка запроса переносятся в Supabase без /db", async () => {
  stub();
  await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records?select=*&limit=5"), {});
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, SUPA_ORIGIN + "/rest/v1/price_records?select=*&limit=5");
});

test("ключ ставит воркер: секрет Cloudflare сильнее того, что прислал браузер", async () => {
  stub();
  await proxyDb(
    req("https://kvant-zip.pages.dev/db/rest/v1/drawings", {
      headers: { apikey: "via-worker", Authorization: "Bearer via-worker" },
    }),
    { SUPABASE_SERVICE_KEY: "secret-from-cloudflare" },
  );
  const h = seen[0].init.headers;
  assert.equal(h.get("apikey"), "secret-from-cloudflare");
  assert.equal(h.get("Authorization"), "Bearer secret-from-cloudflare");
});

test("пока секрета нет — прежний публикуемый ключ, сайт не ломается", async () => {
  stub();
  await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/drawings"), {});
  assert.match(seen[0].init.headers.get("apikey"), /^sb_publishable_/);
});

test("куки Access и служебные заголовки Cloudflare в базу не уходят", async () => {
  stub();
  await proxyDb(
    req("https://kvant-zip.pages.dev/db/rest/v1/samples", {
      headers: {
        Cookie: "CF_Authorization=jwt-of-employee",
        "Cf-Access-Jwt-Assertion": "jwt-of-employee",
        "Cf-Connecting-Ip": "203.0.113.7",
        "X-Forwarded-For": "203.0.113.7",
      },
    }),
    {},
  );
  const h = seen[0].init.headers;
  for (const name of ["cookie", "cf-access-jwt-assertion", "cf-connecting-ip", "x-forwarded-for"]) {
    assert.equal(h.get(name), null, `заголовок ${name} должен быть снят`);
  }
});

test("тело передаётся только там, где оно есть", async () => {
  stub();
  await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records"), {});
  assert.equal(seen[0].init.body, undefined);
  assert.equal(seen[0].init.method, "GET");

  stub();
  await proxyDb(
    req("https://kvant-zip.pages.dev/db/rest/v1/price_records", { method: "POST", body: '{"pn":"1"}' }),
    {},
  );
  assert.equal(seen[0].init.method, "POST");
  assert.notEqual(seen[0].init.body, undefined);
});

test("ответ базы не кэшируется и не проносит set-cookie", async () => {
  stub();
  const out = await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records"), {});
  assert.equal(out.headers.get("Cache-Control"), "no-store");
  assert.equal(out.headers.get("set-cookie"), null);
});

test("код ответа базы доходит до браузера как есть", async () => {
  stub(401, '{"message":"Invalid API key"}');
  const out = await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records"), {});
  assert.equal(out.status, 401);
});

test("страница ЗИП больше не содержит ключ Supabase", () => {
  // zip/public/ — результат сборки и в git не хранится (.gitignore), поэтому
  // проверяем его только там, где он собран локально.
  for (const f of ["zip/site/index.template.html", "zip/public/index.html"]) {
    const p = path.join(ROOT, f);
    if (!fs.existsSync(p)) continue;
    const s = fs.readFileSync(p, "utf8");
    assert.ok(!/sb_publishable_[A-Za-z0-9_-]/.test(s), `ключ остался в ${f}`);
    assert.ok(!/vpjliavuuxjcvtxbthlp\.supabase\.co/.test(s), `адрес базы остался в ${f}`);
  }
});

test("обращения страницы к данным не идут в журнал действий", async () => {
  const { AUDIT_SKIP } = await import("../siterights.js");
  assert.ok(AUDIT_SKIP.test("/db/rest/v1/price_records"), "/db/ должен пропускаться журналом");
  assert.ok(AUDIT_SKIP.test("/db/storage/v1/object/drawings/a.pdf"), "файлы базы — тоже не действие");
  assert.ok(!AUDIT_SKIP.test("/orders/spec.pdf"), "выгрузка документа остаётся в журнале");
  assert.ok(!AUDIT_SKIP.test("/gt/"), "открытие страницы остаётся в журнале");
});

test('only exact existing table/method pairs reach Supabase', async () => {
  const allowed={positions:['GET','HEAD','PATCH'],odm_suppliers:['GET','HEAD','PATCH'],price_records:['GET','HEAD','POST','PATCH','DELETE'],drawings:['GET','HEAD','POST','PATCH','DELETE'],samples:['GET','HEAD','POST','PATCH','DELETE'],rfq_requests:['POST'],change_log:['GET','HEAD'],gt_notes:['GET','HEAD','POST','PATCH']};
  for(const [table,methods] of Object.entries(allowed))for(const method of ['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS']){
    stub();const init={method};if(!['GET','HEAD'].includes(method))init.body='{}';
    const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/'+table,init),{}, {email:'person@example.test'});
    assert.equal(r.status,methods.includes(method)?200:405,table+' '+method);assert.equal(seen.length,methods.includes(method)?1:0);
  }
});

test('private archive, all RPC, auth, functions and unknown paths perform zero fetches', async () => {
  const paths=['/db','/db/','/db/rest/v1','/db/rest/v1/','/db/rest/v1/archive_text_units','/db/rest/v1/archive_object_versions','/db/rest/v1/lib_knowledge','/db/rest/v1/rpc/archive_search_text','/db/rest/v1/rpc/archive_search_status','/db/rest/v1/rpc/lib_pn_key','/db/rest/v1/gt_notes_extra','/db/rest/v1/positions/extra','/db/auth/v1/admin/users','/db/functions/v1/anything','/db/realtime/v1','/db/storage/v1/bucket','/db/storage/v1/object/list/crm-archive','/db/storage/v1/object/crm-archive/a.json','/db/storage/v1/object/sign/crm-archive/a.json','/db/storage/v1/object/public/crm-archive/a.json','/db/storage/v1/object/authenticated/crm-archive/a.json','/db/storage/v1/object/copy','/db/storage/v1/object/move','/db/storage/v1/upload/resumable'];
  for(const path of paths)for(const method of ['GET','POST','DELETE']){
    stub();const r=await proxyDb(req('https://kvant-zip.pages.dev'+path,{method,...(method==='GET'?{}:{body:'{"bucketId":"crm-archive"}'})}),{SUPABASE_SERVICE_KEY:'sb_secret_FIXTURE'});
    assert.equal(r.status,403,path+' '+method);assert.equal(seen.length,0);assert.equal(r.headers.get('Cache-Control'),'no-store');assert.doesNotMatch(await r.text(),/sb_secret|FIXTURE/);
  }
});

test('encoding, slash and suffix variants never select an unlisted upstream resource', async () => {
  const paths=['/db/rest/v1/%61rchive_text_units','/db/rest/v1/%2561rchive_text_units','/db/rest/v1/ARCHIVE_TEXT_UNITS','/db//rest/v1/positions','/db/rest/v1/positions/','/db/rest/v1/positions;archive_text_units','/db/rest/v1/positions%2farchive_text_units','/db/storage/v1/object/%63rm-archive/a','/db/storage/v1/object/drawings/../crm-archive/a','/db/storage/v1/object/drawings/%2e%2e/crm-archive/a','/db/storage/v1/object/drawings/%252e%252e%252fcrm-archive/a','/db/storage/v1/object/drawings/folder%2f..%2f..%2fcrm-archive/a','/db/storage/v1/object/drawings/a%5c..%5cb','/db/storage/v1/object/drawings/a%00b','/db/storage/v1/object/drawings/%zz'];
  for(const path of paths){stub();const r=await proxyDb(req('https://kvant-zip.pages.dev'+path,{method:'POST',body:'file'}),{});assert.equal(r.status,403,path);assert.equal(seen.length,0);}
});

test('PostgREST joins, computed fields, duplicate parameters and schema headers cannot escape scope', async () => {
  const queries=['select=*,archive_text_units(*)','select=x:archive_object_versions(*)','select=archive_search_status','select=id','select=*&select=archive_text_units(*)','select=*%26select%3Darchive_text_units(*)','archive_text_units.text=not.is.null','or=(archive_text_units.text.eq.A)','columns=archive_text_units','on_conflict=private_column','_method=DELETE','id=in.(1,2)','order=archive_text_units(id)','scope=eq.A%00B'];
  for(const query of queries){stub();const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/positions?'+query),{});assert.equal(r.status,403,query);assert.equal(seen.length,0);}
  for(const header of ['Accept-Profile','Content-Profile']){stub();const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/positions',{headers:{[header]:'storage'}}),{});assert.equal(r.status,403);assert.equal(seen.length,0);}
  stub();const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/gt_notes?scope=eq.Mars%20100&removed=is.false&select=*&order=at.asc'),{});assert.equal(r.status,200);assert.equal(seen.length,1);
});

test('headers only carry the service request, without schema, identity or method override', async () => {
  stub();await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/positions?select=*&offset=0&limit=1000',{headers:{'Accept-Profile':'public','Content-Profile':'public','Range':'0-999','Range-Unit':'items','Prefer':'return=representation','X-HTTP-Method-Override':'DELETE','X-Method-Override':'DELETE','X-Bucket-Id':'crm-archive','X-Source-Bucket':'crm-archive','X-Custom-Secret':'CLIENT_SECRET','apikey':'CLIENT_KEY',Authorization:'Bearer CLIENT_TOKEN',Cookie:'PRIVATE_COOKIE'}}),{SUPABASE_SERVICE_KEY:'sb_secret_FIXTURE'});
  const h=seen[0].init.headers;assert.equal(seen[0].init.method,'GET');assert.equal(h.get('Accept-Profile'),'public');assert.equal(h.get('Content-Profile'),'public');assert.equal(h.get('Range'),'0-999');assert.equal(h.get('Authorization'),null);assert.equal(h.get('apikey'),'sb_secret_FIXTURE');
  for(const key of ['X-HTTP-Method-Override','X-Method-Override','X-Bucket-Id','X-Source-Bucket','X-Custom-Secret','Cookie'])assert.equal(h.get(key),null,key);
  assert.equal(seen[0].init.redirect,'manual');
});

test('ordering and filters cannot execute arbitrary computed fields', async () => {
  for(const table of ['positions','odm_suppliers','price_records','drawings','samples','change_log','gt_notes']){
    for(const query of ['order=synthetic_computed_function.asc','order=at.asc,synthetic_computed_function.asc']){
      stub();const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/'+table+'?select=*&'+query),{});
      assert.equal(r.status,403,table+' '+query);assert.equal(seen.length,0);
    }
    if(table!=='gt_notes')for(const query of ['scope=eq.anything','removed=is.false']){
      stub();const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/'+table+'?'+query),{});
      assert.equal(r.status,403,table+' '+query);assert.equal(seen.length,0);
    }
  }
  for(const table of ['change_log','gt_notes'])for(const order of ['at.asc','at.desc']){
    stub();assert.equal((await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/'+table+'?select=*&order='+order),{})).status,200);assert.equal(seen.length,1);
  }
  for(const table of ['change_log','gt_notes']){
    stub();assert.equal((await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/'+table+'?id=eq.1'),{})).status,403);assert.equal(seen.length,0);
  }
  for(const table of ['positions','odm_suppliers','price_records','drawings','samples']){
    stub();assert.equal((await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/'+table+'?id=eq.1',{method:'PATCH',body:'{}'}),{})).status,200);assert.equal(seen.length,1);
  }
  stub();assert.equal((await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/positions?order=at.asc'),{})).status,403);assert.equal(seen.length,0);
});

test('existing upload, signed URL, download and deletion stay within the two buckets', async () => {
  for(const bucket of ['drawings','samples']){
    const path='/db/storage/v1/object/'+bucket+'/42/%D1%87%D0%B5%D1%80%D1%82%D0%B5%D0%B6.pdf';
    stub();const upload=await proxyDb(req('https://kvant-zip.pages.dev'+path,{method:'POST',body:'BINARY FIXTURE',headers:{'Content-Type':'application/pdf','X-Upsert':'false'}}),{});assert.equal(upload.status,200);assert.equal(seen.length,1);assert.equal(seen[0].init.headers.get('Content-Type'),'application/pdf');
    const signed='/db/storage/v1/object/sign/'+bucket+'/42/a.pdf';
    stub();const sign=await proxyDb(req('https://kvant-zip.pages.dev'+signed,{method:'POST',body:JSON.stringify({expiresIn:120})}),{});assert.equal(sign.status,200);assert.deepEqual(JSON.parse(seen[0].init.body),{expiresIn:120});
    stub();const download=await proxyDb(req('https://kvant-zip.pages.dev'+signed+'?token=SYNTHETIC_TOKEN'),{});assert.equal(download.status,200);assert.equal(seen.length,1);
    stub();const remove=await proxyDb(req('https://kvant-zip.pages.dev/db/storage/v1/object/'+bucket,{method:'DELETE',body:JSON.stringify({prefixes:['42/a.pdf']})}),{});assert.equal(remove.status,200);assert.deepEqual(JSON.parse(seen[0].init.body),{prefixes:['42/a.pdf']});
  }
});

test('Storage JSON cannot choose another bucket, copy destination, traversal or arbitrary command', async () => {
  const badSign=[{expiresIn:120,bucketId:'crm-archive'},{expiresIn:120,sourceBucket:'crm-archive'},{expiresIn:120,destinationBucket:'samples'},{expiresIn:120,path:'../crm-archive/a'},{expiresIn:0},{expiresIn:3601},{expiresIn:'120'},[],null];
  const badDelete=[{prefixes:['42/a.pdf'],bucketId:'crm-archive'},{prefixes:['../crm-archive/a']},{prefixes:['a/%2e%2e/b']},{prefixes:['a\\b']},{prefixes:['']},{prefixes:[]},{prefixes:'42/a.pdf'},null];
  for(const [path,method,values] of [['/db/storage/v1/object/sign/drawings/a.pdf','POST',badSign],['/db/storage/v1/object/samples','DELETE',badDelete]])for(const body of values){stub();const r=await proxyDb(req('https://kvant-zip.pages.dev'+path,{method,body:JSON.stringify(body)}),{});assert.equal(r.status,400);assert.equal(seen.length,0);}
  for(const path of ['/db/storage/v1/object/sign/drawings/a.pdf?bucketId=crm-archive','/db/storage/v1/object/drawings/a.pdf?token=whatever']){stub();const r=await proxyDb(req('https://kvant-zip.pages.dev'+path,{method:'POST',body:'{"expiresIn":120}'}),{});assert.equal(r.status,403);assert.equal(seen.length,0);}
});

test('Storage control body is byte bounded even without Content-Length', async () => {
  for(const declared of [undefined,'65537']){stub();const headers=declared?{'Content-Length':declared}:{};const r=await proxyDb(req('https://kvant-zip.pages.dev/db/storage/v1/object/sign/drawings/a.pdf',{method:'POST',headers,body:JSON.stringify({padding:'a'.repeat(65536)})}),{});assert.equal(r.status,400);assert.equal(seen.length,0);}
});

test('gt_notes still stamps actual author and refuses delete', async () => {
  stub();const r=await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/gt_notes',{method:'POST',body:JSON.stringify({author:'forged@example.test',text:'note',scope:'Mars 100',at:'2000-01-01'})}),{}, {email:'real@example.test'});
  assert.equal(r.status,200);const body=JSON.parse(seen[0].init.body);assert.equal(body.author,'real@example.test');assert.notEqual(body.at,'2000-01-01');
  stub();assert.equal((await proxyDb(req('https://kvant-zip.pages.dev/db/rest/v1/gt_notes',{method:'DELETE'}),{})).status,405);assert.equal(seen.length,0);
});

test('anonymous requests cannot reach the proxy or an asset', async () => {
  stub();for(const path of ['/db/rest/v1/positions','/db/rest/v1/archive_text_units','/db/storage/v1/object/crm-archive/a']){
    const r=await worker.fetch(req('https://kvant-zip.pages.dev'+path),{ASSETS:{fetch:()=>{throw new Error('No asset');}}});assert.equal(r.status,403);
  }assert.equal(seen.length,0);
});
