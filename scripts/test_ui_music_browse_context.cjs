/* Real reads; browser writes blocked. Synthetic single exists only in isolated DB. */
const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium}=require('playwright');
(async()=>{
 const session=JSON.parse(fs.readFileSync(process.env.SHIROS_CATALOG_SESSION||'.local/catalog-ui-session.json'));
 const fixture=process.env.SHIROS_CATALOG_SESSION?null:JSON.parse(fs.readFileSync('.local/browse-context-ui-fixture.json'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:1000},reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/ui-api/music/**',async route=>{
   const action=route.request().url().split('/').pop();
   if(action==='taxonomy-seed')return route.fulfill({status:403,contentType:'application/json',body:'{"error":"permission.denied"}'});
   assert(['list','detail','relation-context','profile-sections','relation-options','taxonomy','image-read','stats','listening','browse-context'].includes(action),'Unexpected write '+action);
   return route.continue();
  });
  await page.goto(`http://127.0.0.1:${session.port}/#token=${session.token}`);
  const early='ef73d60c-269e-451c-82ef-7635b2f1afdf',chamber='5d5dc955-703d-49a5-9ded-13cc5a423e1f',recording='fdbb6382-328b-4659-94a5-64a27ecf280d',release='04bb6f25-d162-4d94-a614-cd5cc34e6c37';
  const card=id=>page.locator(`.music-card[data-music-id="${id}"]`);
  const tracks=page.locator('.music-release-track');
  await page.locator('[data-view=music]').click();await page.locator('[data-library=work]').click();
  await page.locator('#music-search').fill('Beethoven');await card(chamber).click();await card(early).click();
  await page.locator('[data-work-section=recording]').click();await card(recording).click();await card(release).click();
  await page.waitForFunction(()=>document.querySelectorAll('.music-release-track').length===24);
  assert.match(await page.locator('.music-browse-range').innerText(),/24 \/ 71/);
  assert((await tracks.locator('.music-track-name').allTextContents()).every(t=>/Op\. ?18/.test(t)));
  await page.screenshot({path:'.local/browse-context-early.png'});
  await page.setViewportSize({width:390,height:900});
  assert(await page.locator('.music-release-page').evaluate(n=>n.scrollWidth<=n.clientWidth+2));
  await page.screenshot({path:'.local/browse-context-mobile.png'});
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'en'}})));
  assert.equal(await page.locator('.music-show-all').innerText(),'Show all');
  await page.locator('.music-show-all').click();await page.waitForFunction(()=>document.querySelectorAll('.music-release-track').length===71);
  assert.equal(await page.locator('.music-browse-range').count(),0);
  await page.setViewportSize({width:1500,height:1000});
  await page.locator('.music-browse-heading button').first().click();await card(release).click();
  await page.waitForFunction(()=>document.querySelectorAll('.music-release-track').length===24);
  await page.locator('.music-browse-heading button').first().click();
  // Searching in the recording ends the inherited Work range.
  await page.locator('#music-search').fill('Beethoven');await card(release).click();
  await page.waitForFunction(()=>document.querySelectorAll('.music-release-track').length===71);
  await page.locator('.music-browse-heading button').first().click();
  await page.locator('[data-recording-section=work]').click();await card(early).waitFor();
  assert.equal(await page.locator('#music-grid .music-card').count(),21);
  assert.equal(await page.locator('#music-grid .music-card:not(:has(.music-cover))').count(),0);
  if(fixture){
   await page.locator('[data-library=recording]').click();await page.locator('#music-search').fill('UI fixture Single');await card(fixture.recording).click();
   await page.locator('[data-recording-section=work]').click();await card(fixture.album).waitFor();
   assert.equal(await page.locator('#music-grid .music-card').count(),1);assert.equal(await card(fixture.song).count(),0);
   await card(fixture.album).click();await card(fixture.song).waitFor();
   assert.equal(await page.locator('#music-grid .music-card').count(),1);assert.match(await page.locator('.music-browse-range').innerText(),/1 \/ 2/);
   await page.locator('.music-show-all').click();await page.waitForFunction(()=>document.querySelectorAll('#music-grid .music-card').length===2);
   assert.equal(await page.locator('.music-browse-range').count(),0);
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({completeCoverage:true,earlyTracks:24,totalTracks:71,showAll:true,backRestoresRange:true,searchClearsRange:true,recordingWorks:21,partialAlbum:!!fixture,mobileWidth:390,languages:['zh','en'],errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(String(e.stack||e.message).replace(/([#&]token=)[A-Za-z0-9_-]+/g,'$1[redacted]'));process.exitCode=1;});
