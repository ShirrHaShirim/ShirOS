/* Binary archive: never execute imported content. */
(() => {
  'use strict';
  const $=s=>document.querySelector(s), el=(tag,label='')=>{const e=document.createElement(tag);e.textContent=label;return e;};
  const headers=()=>({'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''});
  async function request(path,data={}) {const r=await fetch('/ui-api/'+path,{method:'POST',headers:headers(),body:JSON.stringify(data)});if(!r.ok){const d=await r.json();throw new Error(d.error==='files.invalid_conversations'?'请选择 ChatGPT 导出的 conversations.json 或包含它的 ZIP。':'操作失败：'+d.error);}return r;}
  function download(blob,name){const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
  async function exportMemory(id){const r=await request('export-md',id?{id}:{});download(await r.blob(),id?'shiros-memory.md':'shiros-memories.md');}
  let offset=0,total=0;
  async function run(fn){try{await fn();}catch(e){$('#files-message').textContent=e.message;}}
  async function refresh(){const data=await(await request('files/list',{query:$('#files-search').value,offset,limit:50})).json(),list=$('#files-list');total=data.total;list.replaceChildren();$('#files-page').textContent=`${total} 个文件 · 第 ${Math.floor(offset/50)+1} 页`;$('#files-prev').disabled=offset===0;$('#files-next').disabled=offset+50>=total;
    if(!data.items.length)list.append(el('p','暂无文件。导入文档、图片、音频或其他文件，长期保留原件。'));
    for(const item of data.items){const row=el('article');row.className='file-row';const info=el('div');info.append(el('h3',item.filename),el('p',`${(item.size/1024).toFixed(1)} KB · ${new Date(item.updated_at).toLocaleString()}`));const buttons=el('div');buttons.className='heading-actions';const get=el('button','下载');get.className='secondary-button';get.onclick=()=>run(async()=>download(await(await request('files/read',{id:item.id})).blob(),item.filename));const del=el('button','删除');del.className='secondary-button';del.onclick=()=>run(async()=>{if(confirm('删除文件「'+item.filename+'」？此操作无法撤销。')){await request('files/delete',{id:item.id});await refresh();}});buttons.append(get,del);row.append(info,buttons);list.append(row);}
  }
  async function upload(input,action){const files=[...input.files];input.value='';for(const file of files){if(file.size>50*1024*1024)throw new Error('单个文件最大 50 MB。');$('#files-message').textContent='正在导入：'+file.name;const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});const result=await(await request('files/'+action,{filename:file.name,data})).json();$('#files-message').textContent=action==='conversation-import'?`已保存原件，并导入 ${result.conversations} 段对话的全部文本节点。`:'已导入：'+file.name;}offset=0;await refresh();}
  $('#export-memories-md').onclick=()=>run(()=>exportMemory());
  document.addEventListener('shiros-files-open',()=>run(refresh));
  $('#files-upload').onchange=()=>run(()=>upload($('#files-upload'),'upload'));
  $('#conversation-upload').onchange=()=>run(()=>upload($('#conversation-upload'),'conversation-import'));
  $('#files-search').onchange=()=>{offset=0;run(refresh);};
  $('#files-prev').onclick=()=>{offset=Math.max(0,offset-50);run(refresh);};$('#files-next').onclick=()=>{offset+=50;run(refresh);};
  document.addEventListener('shiros-memory-selected',event=>{const b=el('button','导出此记忆 .md');b.className='secondary-button';b.onclick=()=>run(()=>exportMemory(event.detail.id));event.detail.panel.append(b);});
})();
