/* npm install --prefix /tmp/pf-browser playwright esbuild react react-dom react-native-web
   Run the local synthetic preview_app, then NODE_PATH=/tmp/pf-browser/node_modules node tests/browser/refresh.cjs
   Tests real server HTML/JS; native UIRefreshControl/HealthKit still require an iPhone. */
const assert=require('node:assert/strict');
const {chromium,webkit}=require('playwright');
const base=process.env.PF_PREVIEW_URL||'http://127.0.0.1:8852';
const output=process.env.PF_PREVIEW_OUTPUT||'/tmp';
async function check(engine,name,width){
 const browser=await engine.launch({headless:true});
 try{
 const page=await browser.newPage({viewport:{width,height:852},isMobile:true,hasTouch:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/sante',{waitUntil:'networkidle'});
 await page.waitForFunction(()=>window.PFViz&&window.htmx&&window.paceforgeRefresh);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'horizontal overflow');
 await page.evaluate(()=>{window.refreshCount=0;window.stableDocument=document;document.addEventListener('pf:refreshed',()=>window.refreshCount++);document.querySelector('#entrainement').open=true;document.querySelector('#forme').open=true;document.querySelector('#sommeil').open=false;});
 // A vertical swipe, even if it drifts sideways afterwards, must not select.
 const selector='#recuperation figure[data-viz]';
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
 assert.equal(await page.locator('#sommeil').getAttribute('open'),null);
 assert.notEqual(await page.locator('#entrainement').getAttribute('open'),null);
 assert.equal(await page.locator(selector+' input').inputValue(),selected);
 assert.ok(Math.abs((await page.evaluate(()=>scrollY))-oldY)<3,'scroll anchor moved');
 // HX-Refresh is caught before HTMX can reload the document.
 assert.equal(await page.evaluate(()=>{const e=new CustomEvent('htmx:beforeOnLoad',{cancelable:true,bubbles:true,detail:{elt:{id:'sante-sync'},xhr:{getResponseHeader:()=> 'true'}}});document.dispatchEvent(e);return e.defaultPrevented;}),true);
 await page.waitForFunction(()=>refreshCount===2);
 // Several updates do not leave stale chart observers in the live page.
 for(let i=0;i<3;i++)await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 assert.equal(await page.locator('#main-content').count(),1);
 assert.equal(await page.locator(selector+' input').inputValue(),selected);
 // Offline leaves the rendered data intact and offers a retry.
 await page.route('**/sante',route=>route.abort('internetdisconnected'));
 const unchanged=await page.locator('#main-content').innerHTML();
 await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
 assert.equal(await page.locator('#main-content').innerHTML(),unchanged);
 assert.ok(await page.getByRole('button',{name:'Réessayer',exact:true}).isVisible());
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
 await page.evaluate(()=>document.documentElement.classList.add('dark'));
 await page.screenshot({path:output+`/sante-${name}-${width}.png`});
 await page.goto(base+'/activities',{waitUntil:'networkidle'});
 const more=page.getByRole('button',{name:/Semaines précédentes/});
 if(await more.count()){
  await more.click();await page.waitForTimeout(400);
  const rows=await page.locator('[id^="activity-row-"]').count();
  await page.evaluate(()=>window.paceforgeRefresh.refresh(false));
  assert.equal(await page.locator('[id^="activity-row-"]').count(),rows,'older activity pages disappeared');
 }
 assert.deepEqual(errors,[]);console.log(`${name} ${width}px: refresh, gestures, scroll, offline, account and pagination passed`);
 }finally{await browser.close();}
}
(async()=>{for(const width of [320,393])await check(chromium,'chromium',width);if(process.env.PF_WEBKIT==='1')await check(webkit,'webkit',393);})().catch(e=>{console.error(e);process.exitCode=1});
