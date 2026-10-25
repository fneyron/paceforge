/* npm install --prefix /tmp/pf-browser playwright esbuild react react-dom react-native-web
   Run the local synthetic preview_app, then NODE_PATH=/tmp/pf-browser/node_modules node tests/browser/refresh.cjs
   Tests real server HTML/JS; native UIRefreshControl/HealthKit still require an iPhone. */
const assert=require('node:assert/strict');
const {chromium,webkit}=require('playwright');
const base=process.env.PF_PREVIEW_URL||'http://127.0.0.1:8852';
const output=process.env.PF_PREVIEW_OUTPUT||'/tmp';
async function checkinRefresh(page){
 // The feel in one tap: no draft to keep, but a tap is newer than a GET in flight.
 const pressed=()=>page.evaluate(()=>document.querySelector('#ressenti [aria-pressed="true"]')?.textContent.trim()||'');
 const storage=()=>page.evaluate(()=>({local:JSON.stringify(localStorage),session:JSON.stringify(sessionStorage)}));
 if(await pressed()){
  await page.locator('#ressenti button[value="clear"]').click();
  await page.waitForFunction(()=>!document.querySelector('#ressenti [aria-pressed="true"]'));
 }
 const stored=await storage();
 const badDots=()=>page.locator('#ressenti-courbe .pf-feel-dot.is-bad').count();
 // A late GET must not restore an earlier answer, or its track, after a completed tap.
 async function tapDuringGet(name,expected){
  let fetched,release;
  const ready=new Promise(resolve=>fetched=resolve),hold=new Promise(resolve=>release=resolve);
  await page.route('**/sante',async route=>{const response=await route.fetch();const body=await response.text();fetched();await hold;await route.fulfill({response,body});});
  await page.evaluate(()=>{window.checkinRefreshPromise=window.paceforgeRefresh.refresh(false)});
  await ready;
  await page.locator('#ressenti').getByRole('button',{name}).click();
  await page.waitForFunction(text=>(document.querySelector('#ressenti [aria-pressed="true"]')?.textContent.trim()||'')===text,expected);
  const dots=await badDots();
  await page.evaluate(()=>window.savedCheckinNode=document.querySelector('#ressenti'));
  release();await page.evaluate(()=>window.checkinRefreshPromise);await page.unroute('**/sante');
  assert.equal(await page.evaluate(()=>document.querySelector('#ressenti')===window.savedCheckinNode),true,'stale GET replaced the tap on '+name);
  assert.equal(await badDots(),dots,'stale GET replaced the track of dots after '+name);
 }
 await tapDuringGet('Cassé','Cassé');
 await tapDuringGet('Cassé','');
 assert.deepEqual(await storage(),stored,'self-report copied into browser storage');
 // A refresh during the POST keeps its live HTMX target until the reply arrives.
 let posted,releasePost;
 const postReady=new Promise(resolve=>posted=resolve),postHold=new Promise(resolve=>releasePost=resolve);
 await page.route('**/sante/ressenti',async route=>{const response=await route.fetch();const body=await response.text();posted();await postHold;await route.fulfill({response,body});});
 await page.locator('#ressenti').getByRole('button',{name:'Moyen'}).click();
 await postReady;
 await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 releasePost();
 await page.waitForFunction(()=>document.querySelector('#ressenti [aria-pressed="true"]')?.textContent.trim()==='Moyen');
 await page.unroute('**/sante/ressenti');
 await tapDuringGet('Moyen','');
 // A new calendar day takes the page's form, never the old day's.
 const day=await page.locator('#ressenti input[name="day"]').inputValue();
 const nextDay=new Date(Date.parse(day)+86400000).toISOString().slice(0,10);
 await page.route('**/sante',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace(`name="day" value="${day}"`,`name="day" value="${nextDay}"`)});});
 await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 assert.equal(await page.locator('#ressenti input[name="day"]').inputValue(),nextDay);
 await page.unroute('**/sante');
 await page.goto(base+'/sante',{waitUntil:'networkidle'});
}
async function check(engine,name,width){
 const browser=await engine.launch({headless:true,...(name==='webkit'&&process.env.PF_WEBKIT_EXECUTABLE?{executablePath:process.env.PF_WEBKIT_EXECUTABLE}:{})});
 try{
 const page=await browser.newPage({viewport:{width,height:852},isMobile:true,hasTouch:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/sante',{waitUntil:'networkidle'});
 await page.waitForFunction(()=>window.PFViz&&window.htmx&&window.paceforgeRefresh);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'horizontal overflow');
 await page.evaluate(()=>{window.refreshCount=0;window.stableDocument=document;document.addEventListener('pf:refreshed',()=>window.refreshCount++);document.querySelector('#comprendre-indicateurs').open=true;});
 // A vertical swipe, even if it drifts sideways afterwards, must not select.
 const selector='#recuperation-detail figure[data-viz]';
 const before=await page.locator(selector+' input').inputValue();
 await page.locator(selector+' svg.pf-viz-svg').evaluate(svg=>{
  const r=svg.getBoundingClientRect();for(const [type,x,y]of[['pointerdown',20,20],['pointermove',23,45],['pointermove',120,60],['pointerup',120,60]])svg.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerType:'touch',pointerId:1,clientX:r.x+x,clientY:r.y+y}));
 });
 assert.equal(await page.locator(selector+' input').inputValue(),before);
 // A tap selects; three rapid moves are batched but the last one is retained.
 await page.locator(selector+' svg.pf-viz-svg').evaluate(svg=>{const r=svg.getBoundingClientRect();for(const type of ['pointerdown','pointerup'])svg.dispatchEvent(new PointerEvent(type,{bubbles:true,pointerType:'touch',pointerId:1,clientX:r.x+80,clientY:r.y+30}));});
 const selected=await page.locator(selector+' input').inputValue();assert.notEqual(selected,before);
 await page.evaluate(()=>document.querySelector('#recuperation').scrollIntoView({behavior:'instant'}));
 await page.waitForTimeout(250);
 const oldY=await page.evaluate(()=>scrollY);
 // Never replace the page while the finger is down.
 await page.evaluate(()=>{window.dispatchEvent(new Event('touchstart'));window.refreshPromise=window.paceforgeRefresh.refresh(false);});
 await page.waitForTimeout(500);assert.equal(await page.evaluate(()=>refreshCount),0);
  await page.evaluate(()=>{const event=new Event('touchend');Object.defineProperty(event,'touches',{value:[{}]});window.dispatchEvent(event);});
  await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>refreshCount),0,'second finger still touching');
 await page.evaluate(()=>window.dispatchEvent(new Event('touchend')));
 await page.evaluate(()=>window.refreshPromise);
 assert.equal(await page.evaluate(()=>refreshCount),1);
 assert.equal(await page.evaluate(()=>document===window.stableDocument),true);
 // the refresh status is a polite region in the page from its load, empty and hidden until it speaks
 assert.equal(await page.evaluate(()=>{const t=document.querySelector('.pf-refresh-status');return t.parentElement.getAttribute('role')==='status'&&t.parentElement.getAttribute('aria-live')==='polite'}),true);
 assert.notEqual(await page.locator('#comprendre-indicateurs').getAttribute('open'),null);
 assert.ok(await page.locator('#sommeil-detail [data-range-panel]:not([hidden]) figure').isVisible());
 assert.ok(await page.locator('#charge-detail figure').isVisible());
 assert.equal(await page.locator(selector+' input').inputValue(),selected);
 assert.ok(Math.abs((await page.evaluate(()=>scrollY))-oldY)<3,'scroll anchor moved');
 // HX-Refresh is caught before HTMX can reload the document.
 assert.equal(await page.evaluate(()=>{const e=new CustomEvent('htmx:beforeOnLoad',{cancelable:true,bubbles:true,detail:{elt:{id:'sante-sync'},xhr:{getResponseHeader:()=> 'true'}}});document.dispatchEvent(e);return e.defaultPrevented;}),true);
 await page.waitForFunction(()=>refreshCount===2);
 // Several updates do not leave stale chart observers in the live page.
 for(let i=0;i<3;i++)await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 assert.equal(await page.locator('#main-content').count(),1);
 assert.equal(await page.locator(selector+' input').inputValue(),selected);
 await checkinRefresh(page);
 // Offline leaves the rendered data intact, with a single connection link.
 await page.route('**/sante',route=>route.abort('internetdisconnected'));
 const unchanged=await page.locator('#main-content').innerHTML();
 await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 assert.equal(await page.locator('#main-content').innerHTML(),unchanged);
 assert.ok(await page.locator('.pf-refresh-status').getByRole('link',{name:'Connexions',exact:true}).isVisible());
 await page.unroute('**/sante');
 // A different account must never inherit cached activities/disclosures.
 await page.route('**/sante',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text()).replace(/data-account="\d+"/,'data-account="999"')});});
 await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 assert.equal(await page.locator('#main-content').innerHTML(),unchanged);
 assert.match(await page.locator('.pf-refresh-status').innerText(),/compte a changé/);
 await page.unroute('**/sante');
 // One manual action queues watches and Strava exactly once; double pulls join it.
 const posts=[];
 await page.route('**/sante/sync',route=>{posts.push('watch');return route.fulfill({json:{watches:[],since:new Date().toISOString(),errors:[]}})});
 await page.route('**/api/sync',route=>{posts.push('strava');return route.fulfill({json:{received:0,errors:[]}})});
 await page.evaluate(()=>Promise.all([window.paceforgeRefresh.refresh(true),window.paceforgeRefresh.refresh(true)]));
 assert.deepEqual(posts.sort(),['strava','watch']);
 assert.equal(await page.locator('.pf-refresh-status').isVisible(),false,'no success overlay');
 // Native v2 has one status surface in App, including failures. No web overlay.
 await page.evaluate(()=>{window.paceforgeNativeRefresh=2;window.nativeMessages=[];window.ReactNativeWebView={postMessage:s=>nativeMessages.push(JSON.parse(s))};});
 await page.route('**/sante',route=>route.abort('internetdisconnected'));
 await page.evaluate(()=>window.paceforgeRefresh.refresh(true));
 assert.equal(await page.locator('.pf-refresh-status').isVisible(),false);
 assert.equal(await page.evaluate(()=>nativeMessages.filter(m=>m.type==='refresh-notice').length),1);
 assert.deepEqual(await page.evaluate(()=>nativeMessages.filter(m=>m.type==='refresh-state').map(m=>m.active)),[true,false]);
 await page.unroute('**/sante');
 // A successful replacement must explicitly clear the previous native warning.
 await page.evaluate(()=>{window.nativeMessages=[];return window.paceforgeRefresh.refresh(true)});
 assert.deepEqual(await page.evaluate(()=>nativeMessages.filter(m=>m.type==='refresh-notice').map(m=>m.message)),['']);
 await page.evaluate(()=>document.documentElement.classList.add('dark'));
 await page.screenshot({path:output+`/sante-${name}-${width}.png`});
 await page.goto(base+'/activities',{waitUntil:'networkidle'});
 const more=page.getByRole('button',{name:/Semaines précédentes/});
 if(await more.count()){
  const initialRows=await page.locator('[id^="activity-row-"]').count();
  await more.click();
  await page.waitForFunction(n=>document.querySelectorAll('[id^="activity-row-"]').length>n,initialRows);
  const rows=await page.locator('[id^="activity-row-"]').count();
  await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
  assert.equal(await page.locator('[id^="activity-row-"]').count(),rows,'older activity pages disappeared');
 }
 assert.deepEqual(errors,[]);console.log(`${name} ${width}px: refresh, gestures, scroll, offline, account and pagination passed`);
 }finally{await browser.close();}
}
(async()=>{if(process.env.PF_WEBKIT!=='only')for(const width of [320,393])await check(chromium,'chromium',width);if(['1','only'].includes(process.env.PF_WEBKIT))await check(webkit,'webkit',393);})().catch(e=>{console.error(e);process.exitCode=1});
