/* Real nested group reads; all test writes stay in browser memory. */
const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium}=require('playwright');
(async()=>{
 const session=JSON.parse(fs.readFileSync(process.env.SHIROS_CATALOG_SESSION||'.local/catalog-ui-session.json'));
 const plan=JSON.parse(fs.readFileSync('docs/music-work-groups-v0717.json'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:1000},reducedMotion:'reduce'}),errors=[],writes=[];
  let fixture;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/ui-api/music/**',async route=>{
   const action=route.request().url().split('/').pop(),body=route.request().postDataJSON();
   if(action==='taxonomy-seed')return route.fulfill({json:{seeded:0}});
   if(action==='save'){writes.push(body);return route.fulfill({json:{item:{...fixture,...body.object,revision:fixture.revision+1}}});}
   assert(['list','detail','relation-context','profile-sections','relation-options','taxonomy','image-read','stats','listening'].includes(action),'Unexpected action '+action);return route.continue();
  });
  await page.goto(`http://127.0.0.1:${session.port}/#token=${session.token}`);
  const call=(action,payload)=>page.evaluate(async([action,payload])=>{const r=await fetch('/ui-api/music/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')},body:JSON.stringify(payload)});if(!r.ok)throw Error(action+' '+r.status);return r.json();},[action,payload]);
  const chamber='5d5dc955-703d-49a5-9ded-13cc5a423e1f',early=plan.groups[0].id,quartet='acec541f-8a52-4756-98df-f48aaa11404a';
  await page.locator('[data-view=music]').click();await page.locator('[data-library=work]').click();await page.locator('#music-grid .music-card').first().waitFor();
  assert(await page.locator('#music-grid .music-card').first().isVisible());
  assert.equal(await page.locator('.music-catalog-directory,.music-catalog-folder').count(),0);
  assert.equal(await page.locator('#music-grid .music-card:not(:has(.music-cover))').count(),0);
  await page.screenshot({path:'.local/work-groups-library.png'});
  await page.locator('#music-search').fill('Beethoven');await page.locator(`.music-card[data-music-id="${chamber}"]`).click();await page.locator(`.music-card[data-music-id="${early}"]`).waitFor();
  const groupNames=await page.locator('#music-grid .music-card strong').allTextContents();assert.deepEqual(groupNames.slice(0,3),plan.groups.map(g=>g.title));assert.equal(await page.locator('#music-grid .music-card .music-cover').count(),4);
  await page.screenshot({path:'.local/work-groups-chamber.png'});
  await page.locator(`.music-card[data-music-id="${early}"]`).click();await page.waitForFunction(()=>document.querySelectorAll('#music-grid .music-card').length===6);
  assert.equal(await page.locator('#music-grid .music-card .music-cover').count(),6);
  assert.match((await page.locator('#music-grid .music-card strong').allTextContents())[0],/No\. 1 /);
  await page.locator(`.music-card[data-music-id="${quartet}"]`).click();await page.locator('.music-work-hero .music-details-link').click();await page.locator('#music-detail .music-actions').waitFor();
  fixture=(await call('detail',{id:quartet})).item;await page.locator('#music-detail').getByRole('button',{name:'编辑资料',exact:true}).click();const editor=page.locator('.music-entry-dialog');await editor.waitFor();
  assert.equal(await editor.locator('.music-catalog-fields').count(),0);assert.equal(await editor.getByLabel('作品形态',{exact:true}).locator('option[value=folder]').count(),1);
  await editor.getByRole('button',{name:'保存档案',exact:true}).click();assert.equal(writes.length,1);assert(!Object.hasOwn(writes[0].object,'catalog_primary'));
  await page.locator('.music-detail-heading button').click();await page.locator('.music-browse-heading button').first().click();await page.waitForFunction(()=>document.querySelectorAll('#music-grid .music-card').length===6);
  await page.locator('.music-browse-heading button').first().click();await page.locator(`.music-card[data-music-id="${early}"]`).waitFor();
  await page.setViewportSize({width:390,height:900});await page.screenshot({path:'.local/work-groups-mobile.png'});assert(await page.locator('#music-grid').evaluate(n=>n.scrollWidth<=n.clientWidth+2));
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'en'}})));assert.equal(await page.locator('.music-work-hero .music-details-link').innerText(),'View information');
  const restored=(await call('detail',{id:chamber})).item;assert.equal(restored.work_form,'folder');assert(!restored.catalog_redirect);assert.equal(restored.child_works.length,4);
  const numbered=new Set();for(const group of plan.groups){const result=await call('list',{kind:'work',parent_work_id:group.id,sort:'work_order'});assert.equal(result.total,group.members.length);for(const item of result.items)numbered.add(item.id);}assert.equal(numbered.size,16);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({defaultCardsVisible:true,sidebarUnchanged:true,coverFramesAtEveryLevel:true,nestedGroups:[6,5,5],separateGrosseFuge:true,noForcedCatalogueEditor:true,cachedReturn:true,languages:['zh','en'],mobileWidth:390,oldIdRestored:true,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(String(e.stack||e.message).replace(/([#&]token=)[A-Za-z0-9_-]+/g,'$1[redacted]'));process.exitCode=1;});

