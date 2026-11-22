/* A deployment as a browser meets it: for some seconds the proxy answers in the app's place (docs/ops-runbook.md,
   « Déployer »). The real service worker (app/static/sw.js) and the real refresh script (app/static/js/refresh.js)
   run against a local server that plays both the app and the proxy; no preview app is needed.
   NODE_PATH=<where playwright is> node tests/browser/deploy-retry.cjs   (PF_WEBKIT=1 adds WebKit) */
const assert=require('node:assert/strict'),fs=require('node:fs'),http=require('node:http'),path=require('node:path');
const {chromium,webkit}=require('playwright');
const root=path.join(__dirname,'..','..');
const CSP={'Content-Security-Policy':"default-src 'self' 'unsafe-inline'"};  // every answer of the app carries one
const state={awayUntil:0,awayStatus:503,hits:{}};
function page(build){return `<!doctype html><title>PaceForge</title><main id="main-content" data-account="1" data-build="1"><h1>Santé</h1><p id="served">${build}</p></main>
<form method="post" action="/save"><button id="save">Enregistrer</button></form>
<script>navigator.serviceWorker.register('/sw.js');</script><script src="/refresh.js"></script>`;}
const server=http.createServer((request,response)=>{
 const url=new URL(request.url,'http://x'),key=request.method+' '+url.pathname;
 state.hits[key]=(state.hits[key]||0)+1;
 if(url.pathname==='/sw.js'){response.writeHead(200,{'Content-Type':'text/javascript',...CSP});return response.end(fs.readFileSync(path.join(root,'app/static/sw.js')));}
 if(url.pathname==='/refresh.js'){response.writeHead(200,{'Content-Type':'text/javascript',...CSP});return response.end(fs.readFileSync(path.join(root,'app/static/js/refresh.js')));}
 // the proxy: no application header, its own short text
 if(Date.now()<state.awayUntil){response.writeHead(state.awayStatus,{'Content-Type':'text/plain'});return response.end(state.awayStatus===404?'404 page not found':'no available server');}
 if(url.pathname==='/missing'){response.writeHead(404,{'Content-Type':'text/html',...CSP});return response.end('<h1>Page introuvable</h1>');}
 if(url.pathname==='/busy'){response.writeHead(503,{'Content-Type':'text/html',...CSP});return response.end('<h1>Indisponible</h1>');}
 response.writeHead(200,{'Content-Type':'text/html; charset=utf-8',...CSP});response.end(page(state.hits[key]));
});
function away(ms,status=503){state.awayUntil=Date.now()+ms;state.awayStatus=status;state.hits={};}
async function check(engine,name){
 const browser=await engine.launch({headless:true});
 try{
  const context=await browser.newContext(),tab=await context.newPage();
  const base='http://localhost:'+server.address().port;
  await tab.goto(base+'/sante');
  await tab.evaluate(()=>navigator.serviceWorker.ready);
  await tab.reload();  // the worker controls the pages opened after it is active
  assert.ok(await tab.evaluate(()=>!!navigator.serviceWorker.controller),'the worker controls the page');

  // a page asked for while the app is away: it loads for the time the app is away, then it is there
  for(const status of [503,502,504,404]){
   away(2500,status);const started=Date.now();
   const answer=await tab.goto(base+'/sante?during='+status);
   assert.equal(answer.status(),200,name+': the proxy\'s '+status+' reached the page');
   assert.ok(Date.now()-started>=2400,'answered before the app was back');
   assert.ok(state.hits['GET /sante']>=3,'asked again: '+state.hits['GET /sante']);
   assert.equal(await tab.locator('h1').textContent(),'Santé');
  }
  // the app's own answers are final: asked once, shown as they are
  for(const [target,status] of [['/missing',404],['/busy',503]]){
   state.hits={};const answer=await tab.goto(base+target);
   assert.equal(answer.status(),status);assert.equal(state.hits['GET '+target],1,target+' was asked again');
  }
  // a form is never sent twice: its 503 is the browser's to show
  await tab.goto(base+'/sante');away(1500);
  const [posted]=await Promise.all([tab.waitForResponse(r=>r.request().method()==='POST'),tab.click('#save')]);
  assert.equal(posted.status(),503);await tab.waitForLoadState();
  assert.equal(state.hits['POST /save'],1,'a POST was repeated');
  await tab.waitForTimeout(1600);

  // the in-place refresh: its page request is repeated until the app answers, and nothing is reported
  await tab.goto(base+'/sante');
  const before=await tab.locator('#served').textContent();
  const notices=[];
  await tab.exposeFunction('noticed',text=>notices.push(text));
  await tab.evaluate(()=>new MutationObserver(()=>{const t=document.querySelector('.pf-refresh-status');if(t&&!t.hidden)window.noticed(t.textContent);}).observe(document.body,{subtree:true,childList:true,attributes:true}));
  away(2500);const started=Date.now();
  await tab.evaluate(()=>window.paceforgeRefresh.refresh(false));
  assert.ok(Date.now()-started>=2400,'the refresh gave up before the app was back');
  assert.notEqual(await tab.locator('#served').textContent(),before,'the page was not refreshed');
  assert.deepEqual(notices,[],'a notice was shown for an outage the retry covered');
  // an answer of the app that is an error is reported at once, as before
  await tab.route('**/sante',route=>route.request().resourceType()==='fetch'?route.fulfill({status:500,headers:CSP,body:'x'}):route.continue());
  const failed=Date.now();
  await tab.evaluate(()=>window.paceforgeRefresh.refresh(false));
  assert.ok(Date.now()-failed<900,'a failure of the app was waited for');
  assert.deepEqual(notices.slice(-1).map(t=>t.slice(0,27)),['Actualisation indisponible.']);
  console.log(name+': a page and a refresh asked for during a deployment wait for the app (502, 503, 504, the proxy\'s 404); the app\'s own 404 and 503, and a form, are never repeated');
 }finally{await browser.close();}
}
server.listen(0,'127.0.0.1',async()=>{
 try{
  if(process.env.PF_WEBKIT!=='only')await check(chromium,'chromium');
  if(process.env.PF_WEBKIT)await check(webkit,'webkit');
 }catch(e){console.error(e);process.exitCode=1;}finally{server.close();}
});
