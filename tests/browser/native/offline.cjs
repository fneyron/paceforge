// Actual App + File/SecureStore contracts mocked; no HealthKit or native filesystem runtime.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const bundle=fs.readFileSync(process.env.NATIVE_PREVIEW_BUNDLE||'/tmp/paceforge-native-preview.js','utf8');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const p=await browser.newPage({viewport:{width:393,height:852}}),errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  await p.route('http://native.test/**',r=>r.fulfill({contentType:'text/html',body:'<html><body style="margin:0"><div id="app" style="height:100dvh;display:flex"></div><script src="/bundle.js"></script></body></html>'}));
  await p.route('**/bundle.js',r=>r.fulfill({contentType:'text/javascript',body:bundle}));
  await p.goto('http://native.test/?unpaired=1');await p.waitForFunction(()=>window.actual);
  await p.evaluate(()=>navigate('https://paceforge.fr/simulator/routes/2'));await p.waitForTimeout(50);
  // Save must wait for the current form; delayed/repeated replies are one operation.
  await p.evaluate(()=>{window.holdLeave=true;window.holdOffline=true;followLink('paceforge://offline/save/2');});
  assert.equal(await p.evaluate(()=>!!window.offlineReply),false);
  await p.evaluate(()=>finishLeave());await p.waitForFunction(()=>window.offlineReply);
  assert.ok(await p.getByText('Enregistrement du plan sur le téléphone…').isVisible());
  await p.evaluate(()=>{void offlineReply();void offlineReply();});
  await p.getByRole('heading',{name:'Course de montagne'}).waitFor();
  assert.ok(await p.getByText('00:01 · J+1 · km 10').isVisible());
  assert.ok(await p.getByText('Barrière 02:00 · marge 119 min').isVisible());
  assert.equal(await p.locator('[data-testid="mock-webview"]').count(),1);
  assert.equal(await p.locator('[data-testid="mock-webview"]').isVisible(),true); // kept mounted, hidden by opacity/accessibility
  await p.screenshot({path:'/tmp/paceforge-offline-plan.png'});
  const beforeReturn=await p.evaluate(()=>navigations.length);
  await p.getByRole('tab',{name:'Courses',exact:true}).click();
  assert.equal(await p.evaluate(()=>navigations.length),beforeReturn,'closing an offline copy must reuse its still-mounted course');
  assert.equal(await p.evaluate(()=>new URL(actual).pathname),'/simulator/routes/2');
  // Relaunch, no successful page/account response: the committed copy survives.
  await p.addInitScript(()=>{window.holdNavigation=true;});
  await p.reload();await p.waitForFunction(()=>window.pageFail);
  await p.evaluate(()=>pageFail());
  await p.getByRole('button',{name:'Plans hors connexion',exact:true}).click();
  await p.getByRole('button',{name:/Course de montagne/}).click();
  await p.getByRole('heading',{name:'Course de montagne'}).waitFor();
  // Failed disk and SecureStore writes keep the previous committed slot.
  await p.getByRole('tab',{name:'Courses',exact:true}).click();
  await p.evaluate(()=>{window.holdNavigation=false;navigate('https://paceforge.fr/simulator/routes/2');window.failDisk=true;});
  await p.waitForTimeout(50);
  await p.evaluate(()=>followLink('paceforge://offline/save/2'));
  await p.getByText(/La copie n’a pas pu être enregistrée/).waitFor();
  await p.evaluate(()=>{window.failDisk=false;window.failSecure=true;followLink('paceforge://offline/save/2');});
  await p.waitForTimeout(100);
  await p.reload();await p.waitForFunction(()=>window.pageFail);await p.evaluate(()=>pageFail());
  await p.getByRole('button',{name:'Plans hors connexion',exact:true}).click();
  assert.ok(await p.getByRole('button',{name:/Course de montagne/}).isVisible());
  // Changing account removes the old library, including after another relaunch.
  await p.getByRole('tab',{name:'Santé',exact:true}).click();
  await p.evaluate(()=>{window.accountId=2;navigate('https://paceforge.fr/sante');});
  await p.waitForTimeout(100);
  await p.evaluate(()=>followLink('paceforge://offline'));
  await p.getByText(/Ouvre une course avec une connexion/).waitFor();
  assert.equal(await p.getByRole('button',{name:/Course de montagne/}).count(),0);
  await p.reload();await p.waitForFunction(()=>window.pageFail);await p.evaluate(()=>pageFail());
  await p.getByRole('button',{name:'Plans hors connexion',exact:true}).click();
  assert.ok(await p.getByText(/Ouvre une course avec une connexion/).isVisible());
  // A logout request clears copies even if its response is lost.
  await p.getByRole('tab',{name:'Courses',exact:true}).click();
  await p.evaluate(()=>{window.holdNavigation=false;navigate('https://paceforge.fr/simulator/routes/2');});
  await p.waitForTimeout(50);await p.evaluate(()=>followLink('paceforge://offline/save/2'));
  await p.getByRole('heading',{name:'Course de montagne'}).waitFor();
  await p.evaluate(()=>followLink('https://paceforge.fr/auth/logout'));await p.waitForTimeout(100);
  await p.reload();await p.waitForFunction(()=>window.pageFail);await p.evaluate(()=>pageFail());
  await p.getByRole('button',{name:'Plans hors connexion',exact:true}).click();
  assert.ok(await p.getByText(/Ouvre une course avec une connexion/).isVisible());
  assert.deepEqual(errors,[]);
  console.log('Offline plans: save ordering, duplicate reply, midnight, relaunch without network, interrupted writes, account change and logout passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
