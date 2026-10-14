/* Checks run against a separately installed, disposable tester database. */
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),{chromium}=require('playwright');
(async()=>{
 const session=JSON.parse(fs.readFileSync(process.argv[2])),output=process.argv[3];fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[],reads=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/ui-api/music/list'))reads.push(r.postDataJSON());});
  const base=`http://127.0.0.1:${session.port}`,headers={'X-Shiros-Token':session.token};
  const call=async(action,data)=>{const response=await page.request.post(base+'/ui-api/music/'+action,{headers,data});assert(response.ok(),action+' '+response.status());return response.json();};
  await page.addInitScript(()=>localStorage.setItem('shiros-locale','en'));
  await page.goto(base+'/#token='+session.token);await page.locator('[data-view=music]').click();await page.locator('.music-card').first().waitFor();
  assert.equal(await page.locator('#music-grid .music-work-group').count(),0);
  const borodin=(await call('list',{kind:'recording',query:'Borodin',root_recordings:true,limit:10})).items.find(i=>i.child_recording_count===3);
  assert(borodin,'Bundled Borodin root recording');
  const searched=page.waitForResponse(r=>r.url().endsWith('/ui-api/music/list')&&r.request().postDataJSON().query==='Borodin');await page.locator('#music-search').fill('Borodin');await searched;const card=page.locator(`.music-card[data-music-id="${borodin.id}"]`);await card.waitFor();
  const started=Date.now();await card.click();await page.locator('.music-recording-hero h2').waitFor();const headerMs=Date.now()-started;
  await page.locator('.music-subwork-row').first().waitFor();assert.equal(await page.locator('.music-subwork-row').count(),3);
  assert.equal(await page.locator('.music-subwork-row .music-score,.music-subwork-row .music-unrated').count(),0);
  await page.screenshot({path:path.join(output,'recording.png')});
  const before=reads.length;await page.locator('.music-browse-heading button').first().click();await card.waitFor();assert(!reads.slice(before).some(r=>r.root_recordings));
  const transitions=[];
  for(const type of ['work','people','recording','work','people']){const start=Date.now();await page.locator(`[data-library=${type}]`).click();await page.locator('.music-card').first().waitFor();transitions.push({library:type,firstCardMs:Date.now()-start});}
  await page.locator('#music-search').fill('Mozart');const mozart=(await call('list',{kind:'person',query:'Mozart',limit:10})).items.find(i=>/Mozart/.test(i.title));assert(mozart);
  const mozartCard=page.locator(`.music-card[data-music-id="${mozart.id}"]`);await mozartCard.waitFor();await mozartCard.click();await page.locator('.music-profile-hero h2').waitFor();
  const portrait=page.locator('.music-profile-portrait img');await portrait.waitFor();await portrait.evaluate(img=>img.decode());assert(await portrait.evaluate(img=>img.naturalWidth>0));
  await page.screenshot({path:path.join(output,'person.png')});
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:'zh'}})));await page.locator('.music-browse-heading button').first().click();await mozartCard.waitFor();
  await page.locator('[data-library=work]').click();const workSearch=page.waitForResponse(r=>r.url().endsWith('/ui-api/music/list')&&r.request().postDataJSON().query==='1001');await page.locator('#music-search').fill('1001');const workResults=await(await workSearch).json();const workCard=page.locator(`.music-card[data-music-id="${workResults.items[0].id}"]`);await workCard.waitFor();await workCard.click();await page.locator('.music-work-tabs').waitFor();await page.locator('.music-subwork-row').first().waitFor();assert.equal(await page.locator('.music-subwork-row').count(),6);
  const releases=await call('list',{kind:'release',query:'João Barradas',limit:30});assert(releases.items.some(i=>i.title.includes('Space Within')));
  const releasedetail=(await call('detail',{id:releases.items.find(i=>i.title.includes('Space Within')).id})).item;assert(releasedetail.sources.length===0);
  const rated=(await call('list',{kind:'release',sort:'rating_desc',limit:1})).items[0];const ratedDetail=(await call('detail',{id:rated.id})).item;assert(ratedDetail.reviews.length===0);
  assert.deepEqual(errors,[]);const result={version:'0.7.15',recordingHeaderMs:headerMs,cachedBackListReads:0,libraryTransitions:transitions,recordingChildren:3,workChildren:6,childRatingsAbsent:true,portraitLoaded:true,relatedNameSearch:true,publicPlaybackLinks:false,reviewsIncluded:false,errors};
  fs.writeFileSync(path.join(output,'ui-result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.name==='AssertionError'?e.stack:String(e.message).split('\n')[0]+' '+(e.stack?.match(/at .*test_ui_packaged.cjs:\d+:\d+/)?.[0]||''));process.exitCode=1});
