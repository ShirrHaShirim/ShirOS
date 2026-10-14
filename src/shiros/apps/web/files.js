/* Imported content is downloaded, never executed. */
(() => {
  const ui = window.ShirosI18n.message;
  'use strict';
  const $=s=>document.querySelector(s), el=(tag,label='',cls='')=>{const n=document.createElement(tag);window.ShirosI18n.text(n, label);n.className=cls;return n;};
  async function request(path,data={}) {
    const r=await window.ShirosMotion.request('/ui-api/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify(data)});
    if(!r.ok){const d=await r.json();throw new Error(d.error==='files.invalid_conversations'?ui("请选择 ChatGPT 导出的 conversations.json 或包含它的 ZIP。"):ui("操作失败：")+(d.error||r.status));}return r;
  }
  function download(blob,name){const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
  async function exportMemory(id){download(await(await request('export-md',id?{id}:{})).blob(),id?'shiros-memory.md':'shiros-memories.md');}
  const notify=(message,anchor=$('#files-view'),severity='error')=>window.ShirosFeedback.show(message,anchor,severity);
  async function run(fn,anchor){try{await fn();}catch(e){notify(e.message||ui("操作失败，请重试。"),anchor);}}
  let offset=0,total=0,revision=0,searchTimer,uploading=false,layout='list';
  try{layout=localStorage.getItem('shiros-files-layout')==='grid'?'grid':'list';}catch(_){}
  function setLayout(value){layout=value;$('#files-list').dataset.layout=value;for(const key of ['list','grid'])$('#files-view-'+key).setAttribute('aria-pressed',String(key===value));try{localStorage.setItem('shiros-files-layout',value);}catch(_){}}
  const sizeLabel=b=>b<1024?`${b} B`:b<1048576?`${(b/1024).toFixed(1)} KB`:`${(b/1048576).toFixed(1)} MB`;
  function fileMark(name){const ext=name.includes('.')?name.split('.').pop().toLowerCase():'';const type=/^(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(ext)?'image':/^(mp3|flac|wav|m4a|ogg|aac)$/.test(ext)?'audio':/^(zip|7z|rar|gz|tar)$/.test(ext)?'archive':'document';return el('span',ext?ext.slice(0,6).toUpperCase():'FILE','file-mark file-mark-'+type);}
  function confirmDelete(item,trigger){
    const dialog=el('dialog','','file-delete-dialog'),title=el('h2',ui("删除文件")),copy=el('p',ui("删除「{0}」？此操作无法撤销。", item.filename)),actions=el('div','','dialog-actions');
    title.id='file-delete-title';dialog.setAttribute('aria-labelledby',title.id);
    const cancel=el('button',ui("取消"),'secondary-button'),submit=el('button',ui("删除文件"),'danger-button');cancel.type=submit.type='button';cancel.onclick=()=>dialog.close();
    submit.onclick=async()=>{submit.disabled=true;try{await request('files/delete',{id:item.id});dialog.close();await refresh();}catch(e){notify(e.message,dialog.open?dialog:$('#files-view'));submit.disabled=false;}};
    actions.append(cancel,submit);dialog.append(title,copy,actions);document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();if(trigger.isConnected)trigger.focus();},{once:true});dialog.showModal();cancel.focus();
  }
  function rowFor(item){
    const row=el('article','','file-row'),info=el('div','','file-info'),title=el('h3',item.filename);window.ShirosI18n.attribute(title, 'title', item.filename);
    const date=new Date(item.updated_at),time=el('time',Number.isNaN(date.getTime())?ui("时间未知"):window.ShirosI18n.date(item.updated_at,{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}));if(!Number.isNaN(date.getTime()))time.dateTime=date.toISOString();
    const meta=el('div','','file-meta');meta.append(el('span',sizeLabel(item.size)),time);info.append(title,meta);
    const actions=el('div','','file-actions'),get=el('button',ui("下载"),'secondary-button'),del=el('button',ui("删除"),'file-delete-button');get.type=del.type='button';window.ShirosI18n.attribute(get, 'aria-label', ui("下载 {0}", item.filename));window.ShirosI18n.attribute(del, 'aria-label', ui("删除 {0}", item.filename));
    get.onclick=()=>run(async()=>{get.disabled=true;try{download(await(await request('files/read',{id:item.id})).blob(),item.filename);}finally{get.disabled=false;}});del.onclick=()=>confirmDelete(item,del);actions.append(get,del);if(item.external_key?.startsWith('capture:')||item.external_key?.startsWith('conversation-export:')){const links=el('button',ui("关联记忆"),'secondary-button');links.onclick=()=>run(()=>window.ShirosCapture.showFileLinks(item.id));actions.prepend(links);}row.append(fileMark(item.filename),info,actions);return row;
  }
  async function refresh(){
    const current=++revision,query=$('#files-search').value.trim(),data=await(await request('files/list',{query,offset,limit:50})).json();if(current!==revision)return;total=data.total;
    if(offset>=total&&offset>0){offset=Math.max(0,Math.floor((total-1)/50)*50);return refresh();}
    const fragment=document.createDocumentFragment();
    if(!data.items.length){const empty=el('div','','files-empty');empty.append(el('span','▤','files-empty-symbol'),el('h2',query?ui("没有匹配的文件"):ui("文件库为空")),el('p',query?ui("尝试其他文件名，或清除搜索条件。"):ui("导入文档、图片、音频或其他文件，保留原件。")));const button=el('button',query?ui("清除搜索"):ui("导入文件"),'secondary-button');button.type='button';button.onclick=()=>{if(query){$('#files-search').value='';offset=0;run(refresh);}else $('#files-upload').click();};empty.append(button);fragment.append(empty);}else data.items.forEach(item=>fragment.append(rowFor(item)));
    $('#files-list').replaceChildren(fragment);window.ShirosI18n.text($('#files-total'), ui("{0} 个文件", total));window.ShirosI18n.text($('#files-page'), ui("{0} / {1}", Math.floor(offset/50)+1, Math.max(1,Math.ceil(total/50))));$('#files-prev').disabled=offset===0;$('#files-next').disabled=offset+50>=total;
  }
  async function upload(input,action){
    const files=[...input.files];input.value='';if(!files.length||uploading)return;uploading=true;$('#files-import').disabled=$('#files-conversation-import').disabled=true;let count=0,conversations=0;
    try{for(const file of files){if(file.size>50*1024*1024)throw new Error(ui("单个文件最大 50 MB。"));window.ShirosI18n.text($('#files-message'), ui("正在导入 {0} / {1}：{2}", count+1, files.length, file.name));const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error(ui("无法读取文件，请重试。")));reader.readAsDataURL(file);});const result=await(await request('files/'+action,{filename:file.name,data})).json();count++;conversations+=result.conversations||0;}notify(action==='conversation-import'?ui("已保存原件，并导入 {0} 段对话的文本节点。", conversations):ui("已导入 {0} 个文件。", count),$('#files-view'),'success');}
    finally{uploading=false;$('#files-import').disabled=$('#files-conversation-import').disabled=false;window.ShirosI18n.text($('#files-message'), '');offset=0;await refresh();}
  }
  setLayout(layout);$('#files-view-list').onclick=()=>setLayout('list');$('#files-view-grid').onclick=()=>setLayout('grid');$('#files-import').onclick=()=>$('#files-upload').click();window.ShirosI18n.text($('#files-conversation-import'), ui("导入全部对话"));$('#files-conversation-import').onclick=()=>window.ShirosCapture.importAll();
  $('#export-memories-md').onclick=()=>run(()=>exportMemory(),$('.view:not(.hidden)'));document.addEventListener('shiros-files-open',()=>run(refresh));$('#files-upload').onchange=()=>run(()=>upload($('#files-upload'),'upload'));$('#conversation-upload').onchange=()=>run(()=>upload($('#conversation-upload'),'conversation-import'));
  $('#files-search').oninput=()=>{clearTimeout(searchTimer);++revision;offset=0;searchTimer=setTimeout(()=>run(refresh),220);};$('#files-prev').onclick=()=>{offset=Math.max(0,offset-50);run(refresh);};$('#files-next').onclick=()=>{offset+=50;run(refresh);};
  document.addEventListener('shiros-memory-selected',event=>{const b=el('button',ui("导出此记忆 .md"),'secondary-button');b.onclick=()=>run(()=>exportMemory(event.detail.id),event.detail.panel);event.detail.panel.append(b);});
})();
