/* Uses read-only catalogue queries. All test saves stay in an in-memory browser fixture. */
const fs=require('fs'),assert=require('assert/strict'),{chromium}=require('playwright');
(async()=>{
 const session=JSON.parse(fs.readFileSync('.local/browser-session/session.json'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[],fixtures=new Map(),writes=[];
  page.on('pageerror',e=>errors.push(e.stack));
  await page.addInitScript(()=>localStorage.setItem('shiros-locale','en'));
  await page.route('**/ui-api/music/**',async route=>{
   const action=route.request().url().split('/').pop(),body=route.request().postDataJSON();
   if(action==='taxonomy-seed')return route.fulfill({json:{seeded:0}});
   if(action==='save'){
    writes.push(body);const value={...template,...body.object,id:body.id||'f0000000-0000-4000-8000-000000000015',revision:(body.revision||0)+1};
    value.relations=value.relations.map(r=>({...r,title:titles.get(r.object_id)||r.object_id}));
    value.contributors=value.relations.filter(r=>['composer','arranger'].includes(r.role)).map(r=>({id:r.object_id,title:r.title,role:r.role,origin_kind:'work'}));
    fixtures.set(value.id,value);return route.fulfill({json:{item:value}});
   }
   if(action==='detail'&&fixtures.has(body.id))return route.fulfill({json:{item:fixtures.get(body.id)}});
   if(!['list','detail','relation-options','relation-context','profile-sections','taxonomy','image-read','stats','listening','export'].includes(action))throw Error('Unexpected live mutation '+action);
   return route.continue();
  });
  await page.goto(`http://127.0.0.1:${session.port}/#token=${session.token}`);
  const call=(action,payload)=>page.evaluate(async([action,payload])=>{const r=await fetch('/ui-api/music/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')},body:JSON.stringify(payload)});if(!r.ok)throw Error(action+' '+r.status);return r.json();},[action,payload]);
  const source=(await call('list',{kind:'work',query:'BWV 1001',limit:1})).items[0];
  const template=(await call('detail',{id:source.id})).item;
  const people=(await call('relation-options',{kinds:['person','group','organization'],limit:100})).items,titles=new Map(people.map(row=>[row.id,row.title]));
  await page.locator('[data-view=music]').click();await page.locator('.music-card').first().waitFor();
  assert.match(await page.locator('#view-title').innerText(),/Music/);
  assert(!/[\u3400-\u9fff]/u.test(await page.locator('#music-search').getAttribute('placeholder')));
  assert.equal(await page.locator('#music-grid .music-work-group').count(),0);
  await page.locator('#music-new').click();const dialog=page.locator('.music-entry-dialog');await dialog.waitFor();
  await dialog.getByLabel('Type',{exact:true}).selectOption('work');
  await dialog.getByLabel('Name',{exact:true}).fill('测试名称 remains original');
  await dialog.getByLabel('Description',{exact:true}).fill('用户说明不翻译');
  await dialog.locator('.music-category-picker input[value=classical]').check();
  const roleButtons=dialog.locator('.music-credit-choice');await roleButtons.filter({hasText:'Arranger'}).waitFor();
  await dialog.locator('[data-role=arranger]').click();
  await dialog.locator('.music-association-choice input').first().waitFor();
  const first=await dialog.locator('.music-association-choice input').first().getAttribute('data-target-id');
  await dialog.locator('.music-association-choice input').first().check();
  await dialog.locator('.music-association-pager').getByRole('button',{name:'Next',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.music-association-pager span')?.textContent?.startsWith('51'));
  const second=await dialog.locator('.music-association-choice input').first().getAttribute('data-target-id');
  await dialog.locator('.music-association-choice input').first().check();
  await dialog.locator('[data-role=composer]').click();
  await page.waitForFunction(()=>document.querySelector('.music-association-pager span')?.textContent?.startsWith('1'));
  await dialog.locator(`input[data-target-id="${first}"]`).check();
  assert.equal(await dialog.locator('.music-selection-chip').count(),3);
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'zh'}})));
  assert.equal(await dialog.getByLabel('名称',{exact:true}).inputValue(),'测试名称 remains original');
  assert.equal(await dialog.getByLabel('介绍与来源',{exact:true}).inputValue(),'用户说明不翻译');
  assert.equal(await dialog.locator('.music-selection-chip').count(),3);
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'en'}})));
  await dialog.locator('.music-association-addbar button').click();
  assert.equal(await dialog.locator('.music-relation-editor').count(),3);
  await dialog.locator('[data-role=arranger]').click();
  await dialog.locator(`input[data-target-id="${first}"]:disabled`).waitFor();
  assert(await dialog.locator(`input[data-target-id="${first}"]`).isChecked());
  const evidence=dialog.locator('.music-relation-editor').first();await evidence.locator('summary').first().click();
  await evidence.getByLabel('Relationship source',{exact:true}).fill('Synthetic source 原文');
  // Switch language without destroying inputs or pending relationship notes.
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'zh'}})));
  assert.equal(await evidence.getByLabel('关系来源',{exact:true}).inputValue(),'Synthetic source 原文');
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'en'}})));
  await dialog.locator('.music-association-editor').scrollIntoViewIfNeeded();await page.screenshot({path:'.local/v0715/associations-en.png'});
  await page.setViewportSize({width:700,height:950});
  assert(await dialog.evaluate(x=>x.scrollWidth<=x.clientWidth+1));await page.screenshot({path:'.local/v0715/associations-narrow.png'});
  await dialog.getByRole('button',{name:'Save entry',exact:true}).click();await dialog.waitFor({state:'detached'});
  assert.equal(writes.length,1);assert.equal(writes[0].object.title,'测试名称 remains original');
  assert.deepEqual(writes[0].object.relations.map(r=>[r.object_id,r.role]),[[first,'arranger'],[second,'arranger'],[first,'composer']]);
  assert.equal(writes[0].object.relations[0].source_text,'Synthetic source 原文');
  await page.locator('#music-detail').getByRole('button',{name:'Edit information',exact:true}).click();
  await dialog.waitFor();assert.equal(await dialog.locator('.music-relation-editor').count(),3);
  assert.equal(await dialog.locator('.music-relation-editor').first().getByLabel('Relationship source',{exact:true}).inputValue(),'Synthetic source 原文');
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal(await page.locator('#music-detail .music-link-list,#music-detail .music-release-tracks').count(),0);
  assert.deepEqual(errors,[]);const result={checks:['English navigation and search','No inline library child expansion','Selection across pages and roles','Language switching preserves user text and controls','Already-linked indicator','Source note preserved after save/reopen','Narrow editor no horizontal overflow','Compact detail panel'],mockedSaves:writes.length,realCatalogueWrites:0,errors};
  fs.writeFileSync('.local/v0715/ui-result.json',JSON.stringify(result,null,2));console.log(result);
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
