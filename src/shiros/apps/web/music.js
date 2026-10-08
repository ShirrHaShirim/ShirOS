/* Native music module. Remote links never imply observed playback. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const kinds = {release:'专辑 / 发行',work:'作品',recording:'录音 / 演出',person:'音乐家',group:'乐团 / 乐队',organization:'厂牌 / 机构',track:'曲目'};
  const statuses = {wishlist:'想听',listening:'在听',listened:'听过'};
  const roles = {composer:'作曲',conductor:'指挥',performer:'演奏',singer:'演唱',ensemble:'乐团',label:'厂牌',work:'作品',recording:'录音',reissue_of:'再版自',parent_work:'上级作品'};
  const messages = {'music.revision_conflict':'记录已被更新，请重新打开后再保存。','music.featured_requires_five_stars':'精选需要当前个人评分为五星。','music.invalid_import':'文件格式不符，请检查字段和评分范围。','music.invalid_url':'请填写有效的 HTTP / HTTPS 音源链接。','permission.denied':'当前身份没有此操作权限。','privacy.unsafe_music':'内容未通过现有隐私检查，请检查后重试。'};
  let view='all', offset=0, total=0, tags=[], sequence=0, listMode=false, selectedTag=null, selectedGenre=null, untagged=false; const imageCache=new Map();
  const el = (tag, text, cls) => { const x=document.createElement(tag); if(text!==undefined)x.textContent=text; if(cls)x.className=cls; return x; };
  const note = (s) => { $('#music-message').textContent=s; };
  const date = (s) => new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
  async function api(action, payload={}) {
    const response=await fetch(`/ui-api/music/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok) throw new Error(messages[data.error]|| (String(data.error).startsWith('music.import_row_') ? `导入第 ${data.error.split('_').at(-1)} 行无效或重复，请检查 ID、标题、状态和评分。` : '操作失败，请检查内容并重试。'));
    return data;
  }
  const guarded = (fn) => async () => { try { await fn(); } catch(e) { note(e.message); } };
  const button = (label, fn, cls='secondary-button') => {const b=el('button',label,cls);b.type='button';b.addEventListener('click',guarded(fn));return b;};
  function options(select, values, current) { for(const [id,name] of Object.entries(values)) {const o=el('option',name);o.value=id;select.append(o);} if(current!==undefined)select.value=current; }
  function field(form,label,type='text',value='') { const l=el('label',label,'music-field');const i=el(type==='textarea'?'textarea':type==='select'?'select':'input');if(i.tagName==='INPUT')i.type=type;i.value=value??'';l.append(i);form.append(l);return i; }
  function dialog(title) {
    const d=el('dialog',undefined,'music-dialog');const form=el('form');form.append(el('h2',title));d.append(form);document.body.append(d);
    d.addEventListener('close',()=>d.remove()); d.addEventListener('cancel',()=>d.close());
    return {d,form};
  }
  function submit(d,form,label,fn) {
    const err=el('p','', 'music-form-error');err.setAttribute('role','alert');form.append(err);
    const row=el('div',undefined,'music-actions');const save=el('button',label,'primary-button');save.type='submit';row.append(button('取消',()=>d.close()),save);form.append(row);
    form.addEventListener('submit',async(e)=>{e.preventDefault();save.disabled=true;err.textContent='保存中…';try {const message=await fn();d.close();note(message||'已保存');}catch(error){err.textContent=error.message;}finally{save.disabled=false;}});d.showModal();
  }
  function nodePath(node) {const parts=[],seen=new Set();while(node&&!seen.has(node.id)){seen.add(node.id);parts.unshift(node.name);node=tags.find(t=>t.id===node.parent_id);}return parts.join(' / ');}
  function tree(target,namespace,onSelect,selected) {
    target.replaceChildren();
    function append(parent,host,seen=new Set()) {
      for(const node of tags.filter(t=>t.namespace===namespace&&(t.parent_id||null)===parent)) {
        if(seen.has(node.id))continue;seen.add(node.id);
        const children=tags.some(t=>t.parent_id===node.id);
        if(children){const group=el('details');group.open=true;const summary=el('summary');summary.append(button(node.name,()=>onSelect(node),'tree-filter'+(selected===node.id?' selected':'')));group.append(summary);const nested=el('div',undefined,'classification-children');group.append(nested);host.append(group);append(node.id,nested,seen);}
        else host.append(button(node.name,()=>onSelect(node),'tree-filter'+(selected===node.id?' selected':'')));
      }
    }
    append(null,target);if(!target.childElementCount)target.append(el('p','暂无分类','taxonomy-help'));
  }
  function renderTrees() {
    tree($('#music-tag-tree'),'tag',async node=>{selectedTag=node.id;untagged=false;offset=0;renderTrees();await load();},selectedTag);
    tree($('#music-genre-tree'),'genre',async node=>{selectedGenre=selectedGenre===node.id?null:node.id;offset=0;renderTrees();await load();},selectedGenre);
    $('#music-untagged').classList.toggle('selected',untagged);
    $('#music-all-tags').classList.toggle('selected',!untagged&&!selectedTag&&!selectedGenre);
  }
  async function loadTags() { tags=(await api('taxonomy')).items;renderTrees(); }
  async function cover(item,hero=false) {
    const box=el('div',undefined,'music-cover'+(hero?' music-hero':''));box.dataset.kind=item.kind;
    if(!item.image_id){box.textContent=item.title.slice(0,1);return box;}
    try {
      if(!imageCache.has(item.image_id))imageCache.set(item.image_id,(async()=>{
        const r=await fetch('/ui-api/music/image-read',{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify({id:item.image_id})});
        if(!r.ok)throw new Error('图片加载失败');return URL.createObjectURL(await r.blob());
      })());
      const img=el('img');img.alt=item.title;img.src=await imageCache.get(item.image_id);box.append(img);
    }catch(e){imageCache.delete(item.image_id);box.textContent=item.title.slice(0,1);note(e.message);}
    return box;
  }
  function picker(form,namespace,assigned) {
    const section=el('section',undefined,'taxonomy-picker');section.append(el('h3',namespace==='tag'?'私人标签':'Genre · 音乐风格'));
    const chosen=new Set((assigned||[]).map(t=>t.id));
    const none=el('label',undefined,'taxonomy-none');const clear=el('input');clear.type='checkbox';none.append(clear,el('span',namespace==='tag'?'无标签':'不指定 Genre'));section.append(none);
    const choices=el('div',undefined,'classification-tree');section.append(choices);
    function render(){clear.checked=chosen.size===0;choices.replaceChildren();const seen=new Set();
      function append(parent,host){for(const node of tags.filter(t=>t.namespace===namespace&&(t.parent_id||null)===parent)){
        if(seen.has(node.id))continue;seen.add(node.id);const l=el('label',undefined,'taxonomy-choice'),cb=el('input');cb.type='checkbox';cb.checked=chosen.has(node.id);l.append(cb,el('span',node.name));cb.addEventListener('change',()=>{if(cb.checked)chosen.add(node.id);else chosen.delete(node.id);clear.checked=chosen.size===0;});
        if(tags.some(t=>t.parent_id===node.id)){const group=el('details');group.open=true;const head=el('summary');head.append(l);group.append(head);const nest=el('div',undefined,'classification-children');group.append(nest);host.append(group);append(node.id,nest);}else host.append(l);
      }}append(null,choices);if(!choices.childElementCount)choices.append(el('p','暂无私人标签，可在“分类目录”新增。','taxonomy-help'));
    }clear.addEventListener('change',()=>{if(clear.checked){chosen.clear();render();}else if(chosen.size===0)clear.checked=true;});render();form.append(section);return ()=>[...chosen];
  }
  async function manageTaxonomy() {
    const {d,form}=dialog('管理音乐标签与 Genre');const ns=field(form,'目录','select');options(ns,{tag:'私人标签',genre:'Genre · 音乐风格'});
    const existing=field(form,'编辑已有分类，或新增','select'),name=field(form,'名称','text'),parent=field(form,'上级分类','select');name.required=true;name.maxLength=64;
    function parents(){const old=parent.value;parent.replaceChildren();options(parent,{'':'根目录'});for(const n of tags.filter(t=>t.namespace===ns.value&&t.id!==existing.value))options(parent,{[n.id]:nodePath(n)});parent.value=old;}
    function populate(){existing.replaceChildren();options(existing,{'':'＋ 新增分类'});for(const n of tags.filter(t=>t.namespace===ns.value))options(existing,{[n.id]:nodePath(n)});name.value='';parents();}
    ns.addEventListener('change',populate);existing.addEventListener('change',()=>{const n=tags.find(t=>t.id===existing.value);name.value=n?.name||'';parents();parent.value=n?.parent_id||'';});populate();
    submit(d,form,'保存分类',async()=>{const current=tags.find(t=>t.id===existing.value);await api('taxonomy-save',{id:current?.id||null,revision:current?.revision||null,namespace:ns.value,name:name.value,parent_id:parent.value||null});await loadTags();await load();});
  }
  function imageImport(item){const {d,form}=dialog('导入封面 / 头像');form.append(el('p','支持 JPG、PNG、WebP，最大 8 MB。保留比例，图片元数据会移除。','subheading'));const file=field(form,'选择图片','file');file.accept='image/jpeg,image/png,image/webp';file.required=true;
    const preview=el('img',undefined,'image-import-preview');preview.alt='待导入图片预览';preview.hidden=true;form.append(preview);let encoded='';
    file.addEventListener('change',guarded(async()=>{const f=file.files[0];encoded='';if(!f)return;if(f.size>8*1024*1024)throw new Error('图片超过 8 MB，请选择较小图片。');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(f);});encoded=String(data).split(',')[1];if(preview.src.startsWith('blob:'))URL.revokeObjectURL(preview.src);preview.src=URL.createObjectURL(f);preview.hidden=false;}));
    d.addEventListener('close',()=>{if(preview.src.startsWith('blob:'))URL.revokeObjectURL(preview.src);});submit(d,form,'保存图片',async()=>{if(!encoded)throw new Error('请先选择图片。');await api('image-upload',{id:item.id,revision:item.revision,image_data:encoded,filename:file.files[0].name});await after(item.id);});
  }
  async function load() {
    const seq=++sequence;note('加载中…');
    const r=await api('list',{query:$('#music-search').value,view,kind:$('#music-kind').value,status:$('#music-status').value||null,tag_id:selectedTag,genre_id:selectedGenre,untagged,offset,limit:24});
    if(seq!==sequence)return;
    total=r.total;const grid=$('#music-grid');grid.replaceChildren();grid.classList.toggle('music-list-mode',listMode);
    for(const item of r.items) {
      const b=el('button',undefined,'music-card');b.type='button';
      const artwork=await cover(item);
      const copy=el('div',undefined,'music-card-copy');copy.append(el('strong',item.title),el('small',[kinds[item.kind],item.release_year,statuses[item.status],item.rating?'★'.repeat(item.rating):null].filter(Boolean).join(' · ')));
      b.append(artwork,copy);b.addEventListener('click',guarded(()=>open(item.id)));grid.append(b);
    }
    if(!r.items.length)grid.append(el('p','还没有匹配的音乐。可以新建档案，或导入豆瓣收藏。','music-empty'));
    $('#music-page').textContent=`${total?offset+1:0}–${Math.min(offset+24,total)} / ${total}`;$('#music-prev').disabled=offset===0;$('#music-next').disabled=offset+24>=total;
    note('');
  }
  async function stats() {const r=await api('stats'),s=r.listening;$('#music-summary').textContent=`已记录 ${s.events} 次聆听 · 已知实际时长 ${Math.round((s.actual_seconds||0)/60)} 分钟 · ${s.events-s.known_duration_events} 次时长未知。仅统计已登记记录。`;}
  async function open(id) {
    const item=(await api('detail',{id})).item;const panel=$('#music-detail');panel.replaceChildren();
    const hero=await cover(item,true);panel.append(hero,el('p',kinds[item.kind],'eyebrow'),el('h2',item.title));
    panel.append(el('p',[item.release_year,item.catalogue,item.barcode,statuses[item.status]].filter(Boolean).join(' · ')));
    if(item.description)panel.append(el('p',item.description,'music-description'));
    if(item.genres?.length)panel.append(el('p','Genre · '+item.genres.map(t=>t.name).join(' / '),'music-genres'));if(!item.tags.length)panel.append(el('p','无标签','music-tags'));if(item.tags.length)panel.append(el('p',item.tags.map(t=>t.name).join(' / '),'music-tags'));
    const actions=el('div',undefined,'music-actions');if(item.kind!=='track')actions.append(button('编辑资料',()=>edit(item)));actions.append(button('评价',()=>review(item)),button('添加音源',()=>source(item)));
    if(['release','recording','track'].includes(item.kind))actions.append(button('登记聆听',()=>listen(item)));
    actions.append(button(item.image_id?'替换图片':'导入图片',()=>imageImport(item)));if(item.image_id)actions.append(button('移除当前图片',async()=>{await api('image-remove',{id:item.id,revision:item.revision});await after(item.id);}));panel.append(actions);
    if(item.kind==='release') {
      const members=el('div',undefined,'music-actions');
      for(const [key,label] of [['featured','精选'],['frequent','常用']]) {const current=item.memberships.find(m=>m.library===key),active=!!current?.active;
        members.append(button(active?`移出${label}`:`加入${label}`,()=>membership(item,key,!active)));
        if(key==='featured'&&active&&item.rating!==5)panel.append(el('p','原精选记录已保留；当前评分不足五星，已退出符合资格的精选视图。','music-form-error'));
      }panel.append(members);
    }
    panel.append(el('h3','当前评价'));
    panel.append(el('p',item.reviews.length?`${item.rating?'★'.repeat(item.rating):'未评分'} · ${item.reviews[0].comment||'暂无短评'}`:'尚未评价'));
    if(item.douban_id)panel.append(el('p',`豆瓣 #${item.douban_id} · ${statuses[item.douban_status]} · ${item.douban_rating||'未评分'} 星 · ${item.marked_at||'标记时间未知'}（收藏状态不代表真实播放）`));
    if(item.relations.length||item.related.length) {panel.append(el('h3','关联档案'));const related=el('div',undefined,'music-relations');for(const r of item.relations)related.append(button(`${roles[r.role]} · ${r.title}`,()=>open(r.object_id)));for(const r of item.related)related.append(button(`${r.title} · ${roles[r.role]}`,()=>open(r.id)));panel.append(related);}
    if(item.tracks.length){panel.append(el('h3','曲目'));const tracks=el('ol');for(const t of item.tracks) {const li=el('li');li.append(button(`${t.disc}.${t.position} · ${t.title}${t.duration_seconds?` · ${Math.floor(t.duration_seconds/60)}:${String(t.duration_seconds%60).padStart(2,'0')}`:' · 时长未知'}`,()=>open(t.id)));tracks.append(li);}panel.append(tracks);}
    panel.append(el('h3','播放来源'));
    for(const s of item.sources) {const a=el('a',`${s.platform} ↗`,'music-source');a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';panel.append(a);}
    panel.append(el('p','通过外部平台打开音源；打开链接不会自动生成聆听记录。','subheading'));
    panel.append(el('h3','最近聆听'));for(const e of item.listening)panel.append(el('p',`${date(e.started_at)} · ${e.platform} · ${e.actual_seconds?`${e.actual_seconds} 秒实际时长`:'时长未知'}${e.completed?' · 手动标记完成':''}`));
    const history=el('details');history.append(el('summary',`评分与操作历史 · 修订 ${item.revision}`));for(const r of item.reviews)history.append(el('p',`${date(r.created_at)} · ${r.rating||'未评分'} 星 · ${r.comment} · ${r.origin}`));for(const h of item.history)history.append(el('p',`v${h.revision} · ${h.action} · ${date(h.created_at)} · 来源 ${h.source_id}`));panel.append(history);
  }
  async function after(id) {await load();await open(id);await stats();}
  async function edit(item=null) {
    const {d,form}=dialog(item?'编辑资料':'新建音乐档案');
    const kind=field(form,'类型','select');options(kind,kinds,item?.kind||$('#music-kind').value);kind.querySelector('option[value="track"]').remove();kind.disabled=!!item;
    const title=field(form,'名称','text',item?.title);title.required=true;title.maxLength=500;
    const release=el('div');form.append(release);const year=field(release,'发行年份（未知留空）','number',item?.release_year);year.min=1;year.max=9999;
    const barcode=field(release,'条码','text',item?.barcode);const description=field(release,'介绍','textarea',item?.description);const state=field(release,'聆听状态','select');options(state,{'':'未标记',...statuses},item?.status||'');
    const catalogue=field(form,'作品编号 / 目录号','text',item?.catalogue);
    const tagSelection=picker(form,'tag',item?.tags);const genreSelection=picker(form,'genre',item?.genres);
    const relations=el('div');relations.append(el('h3','关联音乐家、作品与录音'));form.append(relations);const chosen=(item?.relations||[]).map(r=>({object_id:r.object_id,role:r.role,title:r.title}));
    const chosenBox=el('div');relations.append(chosenBox);
    const renderChosen=()=>{chosenBox.replaceChildren();chosen.forEach((r,index)=>chosenBox.append(button(`${roles[r.role]} · ${r.title} ×`,()=>{chosen.splice(index,1);renderChosen();})));};renderChosen();
    const role=field(relations,'关系角色','select');options(role,roles);
    const target=field(relations,'已有档案','select');options(target,{'':'请选择'});
    const objects=[];for(const k of Object.keys(kinds).filter(k=>k!=='track'))objects.push(...(await api('list',{kind:k,limit:100})).items);
    const targetKinds={composer:['person'],conductor:['person'],performer:['person','group'],singer:['person'],ensemble:['group'],label:['organization'],work:['work'],recording:['recording'],reissue_of:['release'],parent_work:['work']}; const refreshTargets=()=>{const old=target.value;target.replaceChildren();options(target,{'':'请选择'});for(const o of objects.filter(o=>o.id!==item?.id&&targetKinds[role.value].includes(o.kind)))options(target,{[o.id]:`${kinds[o.kind]} · ${o.title}`});if([...target.options].some(o=>o.value===old))target.value=old;};role.addEventListener('change',refreshTargets);refreshTargets();
    relations.append(button('添加关联',()=>{if(!target.value)throw new Error('请选择一个已有档案。');if(!chosen.some(x=>x.object_id===target.value&&x.role===role.value))chosen.push({object_id:target.value,role:role.value,title:objects.find(o=>o.id===target.value).title});renderChosen();}));relations.append(el('p','不存在的音乐家或作品可先用“新建”登记，再在此关联。','subheading'));
    let track;
    if(!item){track=field(release,'曲目：每行 名称 | 秒数（未知留空）','textarea');track.placeholder='第一乐章 | 420\n第二乐章 |';}
    const toggle=()=>{release.hidden=kind.value!=='release';};kind.addEventListener('change',toggle);toggle();
    submit(d,form,'保存档案',async()=> {
      const isRelease=kind.value==='release';const tracks=isRelease&&track?track.value.split('\n').filter(x=>x.trim()).map((line,i)=>{const [name,seconds]=line.split('|');return {title:name.trim(),disc:1,position:i+1,duration_seconds:seconds?.trim()?Number(seconds):null};}):[];
      const object={kind:kind.value,title:title.value,description:isRelease?description.value:'',release_year:isRelease&&year.value?Number(year.value):null,barcode:isRelease?barcode.value||null:null,catalogue:['release','work'].includes(kind.value)?catalogue.value||null:null,status:isRelease?state.value||null:null,tag_ids:tagSelection(),genre_ids:genreSelection(),relations:chosen.map(({object_id,role})=>({object_id,role})),tracks};
      const result=await api('save',{id:item?.id||null,revision:item?.revision||null,object});await after(result.item.id);
    });
  }
  function review(item){const {d,form}=dialog('个人评价');const rating=field(form,'评分','select');options(rating,{'':'未评分','1':'★','2':'★★','3':'★★★','4':'★★★★','5':'★★★★★'},item.rating||'');const comment=field(form,'评价','textarea',item.reviews[0]?.comment);submit(d,form,'保存评价',async()=>{await api('review',{id:item.id,revision:item.revision,rating:rating.value?Number(rating.value):null,comment:comment.value});await after(item.id);});}
  function membership(item,library,active){const {d,form}=dialog(`${active?'加入':'移出'}${library==='featured'?'精选':'常用'}`);const reason=field(form,'理由（可选）','textarea');form.append(el('p','成员变动会留存历史。常用不会因缺少播放记录自动移除。','subheading'));submit(d,form,'确认',async()=>{await api('membership',{id:item.id,revision:item.revision,library,active,reason:reason.value});await after(item.id);});}
  function source(item){const {d,form}=dialog('添加外部音源');const url=field(form,'音源链接','url');url.required=true;const platform=field(form,'平台','text','external');platform.required=true;submit(d,form,'保存音源',async()=>{await api('source',{id:item.id,revision:item.revision,url:url.value,platform:platform.value});await after(item.id);});}
  function listen(item){const {d,form}=dialog('手动登记真实聆听');const when=field(form,'开始时间（北京时间）','datetime-local',new Date(Date.now()+8*3600000).toISOString().slice(0,16));when.required=true;const secs=field(form,'实际收听秒数（未知留空）','number');secs.min=1;const platform=field(form,'播放平台','text','manual');platform.required=true;const done=field(form,'已完成本次聆听','checkbox');submit(d,form,'登记',async()=>{await api('listen',{id:item.id,revision:item.revision,started_at:new Date(when.value+':00+08:00').toISOString(),timezone:'Asia/Shanghai',actual_seconds:secs.value?Number(secs.value):null,platform:platform.value,completed:done.checked});await after(item.id);});}
  function importing(){const {d,form}=dialog('导入豆瓣收藏');form.append(el('p','每行需要 douban_id、title、status（想听 / 在听 / 听过）；可选 rating（1–5 或空）、comment、marked_at、release_year。JSON 使用数组或 {"items": [...]}。首次继承评分；再次导入保留手动评价。','subheading'));const file=field(form,'CSV / JSON 文件','file');file.accept='.csv,.json';const format=field(form,'格式','select');options(format,{json:'JSON',csv:'CSV'});const content=field(form,'也可粘贴内容','textarea');let filename='douban.json',previewed='';const preview=el('p');form.append(preview);file.addEventListener('change',guarded(async()=>{const f=file.files[0];if(!f)return;if(f.size>300000)throw new Error('请将大文件拆为每批最多 1000 条、300 KB 的文件。');filename=f.name;format.value=f.name.toLowerCase().endsWith('.csv')?'csv':'json';content.value=await f.text();}));form.append(button('预览导入',async()=>{const r=await api('import-preview',{content:content.value,format:format.value,filename});previewed=format.value+'\n'+content.value;preview.textContent=`已验证 ${r.count} 条：${r.items.slice(0,5).map(x=>x.object.title).join('、')}`;}));submit(d,form,'确认导入',async()=>{if(previewed!==format.value+'\n'+content.value)throw new Error('请先预览当前文件。');const r=await api('import',{content:content.value,format:format.value,filename});await load();return `导入完成：新增 ${r.created}，更新 ${r.updated}，未变 ${r.unchanged}`;});}
  $('#music-new').addEventListener('click',guarded(()=>edit()));$('#music-import').addEventListener('click',importing);
  $('#music-export').addEventListener('click',guarded(async()=>{const data=await api('export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='shiros-music.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);note('音乐档案已导出');}));
  for(const b of $('#music-tabs').querySelectorAll('button'))b.addEventListener('click',guarded(async()=>{view=b.dataset.library;offset=0;for(const x of $('#music-tabs').children)x.classList.toggle('active',x===b);if(view!=='all')$('#music-kind').value='release';await load();}));
  for(const id of ['music-kind','music-status'])$('#'+id).addEventListener('change',guarded(async()=>{offset=0;await load();}));
  let timer;$('#music-search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(guarded(async()=>{offset=0;await load();}),250);});
  $('#music-prev').addEventListener('click',guarded(async()=>{offset=Math.max(0,offset-24);await load();}));$('#music-next').addEventListener('click',guarded(async()=>{offset+=24;await load();}));$('#music-layout').addEventListener('click',guarded(async()=>{listMode=!listMode;$('#music-layout').textContent=listMode?'网格':'列表';await load();}));
  $('#music-all-tags').addEventListener('click',guarded(async()=>{selectedTag=null;selectedGenre=null;untagged=false;offset=0;renderTrees();await load();}));
  $('#music-untagged').addEventListener('click',guarded(async()=>{selectedTag=null;untagged=true;offset=0;renderTrees();await load();}));
  $('#music-taxonomy-manage').addEventListener('click',guarded(manageTaxonomy));
  document.addEventListener('shiros-music-open',guarded(async()=>{try{await api('taxonomy-seed');}catch(e){/* Readers use existing catalogues without write rights. */}await loadTags();await load();await stats();}));
})();
