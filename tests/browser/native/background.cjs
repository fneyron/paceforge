// Actual App + background engine; HealthKit, the task scheduler, the keychain and files are mocked.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const bundle=fs.readFileSync(process.env.NATIVE_PREVIEW_BUNDLE||'/tmp/paceforge-native-preview.js','utf8');
const pair=JSON.stringify({token:'fixture',user_id:1,name:'Athlete'});
const plan={version:1,user_id:1,route_id:2,name:'Course de montagne',saved_at:'2026-10-12T20:00:00Z',race_date:null,start_s:75600,
 planned:true,distance_km:30,elevation_m:1100,points:[]};
const stored=(extra={})=>({'paceforge.phone.v1':pair,'paceforge.phone.v1.auto':'true',
 'paceforge.phone.v1.bg':JSON.stringify({userId:1,enabled:true,blocked:'',hashes:{},hashedAt:0,tokens:{},retryAt:0}),
 'paceforge.offline.index.v1':JSON.stringify({userId:1,slot:0}),...extra});
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  async function page(query,secure){
   const p=await browser.newPage({viewport:{width:393,height:852}}),errors=[];
   p.on('pageerror',e=>errors.push(e.message));p.errors=errors;
   await p.addInitScript(([secure,library])=>{if(!sessionStorage.getItem('secure')){sessionStorage.setItem('secure',secure);
    sessionStorage.setItem('file:///documents/paceforge-plans-v1-0.json',library);}},[JSON.stringify(secure),JSON.stringify({userId:1,plans:[plan]})]);
   await p.route('http://native.test/**',r=>r.fulfill({contentType:'text/html',body:'<html><body style="margin:0"><div id="app" style="height:100dvh;display:flex"></div><script src="/bundle.js"></script></body></html>'}));
   await p.route('**/bundle.js',r=>r.fulfill({contentType:'text/javascript',body:bundle}));
   await p.goto('http://native.test/'+query);await p.waitForFunction(()=>window.healthChange);
   return p;
  }
  // A weigh-in wakes the app in the background: no WebView, no account page, one short sync.
  const woken=await page('?state=background',stored());
  await woken.waitForTimeout(200);
  assert.equal(await woken.locator('[data-testid="mock-webview"]').count(),0,'a background launch must not load Santé');
  assert.deepEqual(await woken.evaluate(()=>navigations),[]);
  assert.deepEqual(await woken.evaluate(()=>registered),[],'background delivery is configured from the foreground only');
  await woken.evaluate(()=>{healthChange();healthChange();});
  await woken.waitForFunction(()=>writes.some(u=>u.endsWith('/api/mobile/daily')),null,{timeout:5000});
  await woken.waitForTimeout(300);
  assert.equal(await woken.evaluate(()=>collects),1,'changes reported together make one run');
  assert.deepEqual(await woken.evaluate(()=>writes.filter(u=>!u.endsWith('/api/mobile/daily'))),[],'no account check, rotation or night upload without the screen');
  const receipt=JSON.parse(await woken.evaluate(()=>JSON.parse(sessionStorage.getItem('secure'))['paceforge.phone.v1.sync']));
  assert.equal(receipt.trigger,'background');assert.equal(receipt.complete,true);
  // Opening the app afterwards shows the screen. The fresh receipt needs no immediate
  // foreground sync, and identical data is not sent twice.
  await woken.evaluate(()=>foreground());
  await woken.waitForFunction(()=>window.actual==='https://paceforge.fr/sante');
  await woken.waitForTimeout(300);
  assert.equal(await woken.evaluate(()=>window.nonce),undefined,'no account check right after a background sync');
  assert.deepEqual(await woken.evaluate(()=>registered.map(r=>r[0])),['healthkit','register'],'registered again from the foreground');
  assert.deepEqual(await woken.evaluate(()=>registered[1]),['register','paceforge-health-sync',60]);
  await woken.evaluate(()=>healthChange());await woken.waitForTimeout(2000);
  assert.equal(await woken.evaluate(()=>writes.filter(u=>u.endsWith('/api/mobile/daily')).length),1,'unchanged data is not sent again');
  assert.deepEqual(woken.errors,[]);await woken.close();

  // Locked phone: the keychain cannot be read. Never « not paired », nothing read, and the
  // offline copies survive until the app is opened and unlocked.
  const locked=await page('?state=background&locked=1',stored());
  await locked.evaluate(()=>healthChange());await locked.waitForTimeout(2000);
  assert.equal(await locked.evaluate(()=>collects),0);assert.deepEqual(await locked.evaluate(()=>writes),[]);
  await locked.evaluate(()=>{unlock();foreground();});
  await locked.waitForFunction(()=>window.nonce);
  await locked.evaluate(()=>reply());await locked.waitForFunction(()=>collects===1);
  await locked.evaluate(()=>followLink('paceforge://offline'));
  await locked.getByRole('button',{name:/Course de montagne/}).waitFor();
  assert.deepEqual(locked.errors,[]);await locked.close();

  // Even if the account answer arrives while the copies are still unreadable, they are kept.
  const racing=await page('?locked=1',stored());
  await racing.waitForFunction(()=>window.actual);await racing.waitForTimeout(300);
  assert.equal(await racing.evaluate(()=>JSON.parse(sessionStorage.getItem('secure'))['paceforge.offline.index.v1']),JSON.stringify({userId:1,slot:0}));
  assert.ok(await racing.evaluate(()=>sessionStorage.getItem('file:///documents/paceforge-plans-v1-0.json')?.includes('Course de montagne')),'locked load must not wipe the copies');
  await racing.evaluate(()=>{unlock();background();foreground();});
  await racing.waitForTimeout(300);
  await racing.evaluate(()=>followLink('paceforge://offline'));
  await racing.getByRole('button',{name:/Course de montagne/}).waitFor();
  await racing.close();

  // A refused token in the background stops background sync and asks to reconnect.
  const refused=await page('?state=background',stored());
  await refused.evaluate(()=>{window.failStatus=401;healthChange();});
  await refused.waitForFunction(()=>registered.some(r=>r[0]==='unregister'),null,{timeout:5000});
  assert.deepEqual(await refused.evaluate(()=>registered.map(r=>r[0])),['healthkit off','unregister']);
  await refused.evaluate(()=>{window.failStatus=0;foreground();});
  await refused.getByRole('button',{name:'Réglages et connexions',exact:true}).click();
  await refused.waitForFunction(()=>window.connectionNonce);
  await refused.evaluate(()=>reply({type:'connections',name:'Athlete',providers:{coros:'disconnected',garmin:'disconnected',strava:'disconnected'}}));
  await refused.getByText('Compte à confirmer',{exact:true}).waitFor();
  assert.equal(await refused.evaluate(()=>registered.filter(r=>r[0]==='register').length),0,'never registered again while the token is refused');
  await refused.close();

  // Logging out of the web account stops background sync, until the account is confirmed again.
  const logout=await page('',stored());
  await logout.waitForFunction(()=>registered.some(r=>r[0]==='register'));
  await logout.evaluate(()=>followLink('https://paceforge.fr/auth/logout'));
  await logout.waitForFunction(()=>registered.some(r=>r[0]==='unregister'));
  assert.equal(JSON.parse(await logout.evaluate(()=>JSON.parse(sessionStorage.getItem('secure'))['paceforge.phone.v1.bg'])).blocked,'logout');
  await logout.evaluate(()=>reply());
  await logout.waitForFunction(()=>registered.filter(r=>r[0]==='register').length===2);
  await logout.close();
  console.log('Background sync: launch without screen, one run per wake, skip unchanged, locked keychain and offline copies, refused token, logout passed (native APIs mocked).');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
