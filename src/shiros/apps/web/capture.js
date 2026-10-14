/* Grants are created only by this authenticated workbench; producer tokens are single-scope. */
(() => {
  const ui = window.ShirosI18n.message;
  'use strict';
  const el=(tag,label='',cls='')=>{const n=document.createElement(tag);window.ShirosI18n.text(n, label);n.className=cls;return n;};
  const labels={gpt:'ChatGPT',codex:'Codex',opencode:'OpenCode',deepseek:'DeepSeek','deepseek-harness':'DeepSeek Harness'};
  async function api(action,data={}){const r=await fetch('/ui-api/capture/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify(data)}),body=await r.json();if(!r.ok)throw new Error(body.error||ui("操作未完成"));return body;}
  const feedback=(e,anchor)=>window.ShirosFeedback.show(e.message||String(e),anchor);
  function field(form,label,type='text',value=''){const line=el('label',label,'capture-field'),input=el('input');input.type=type;input.value=value;line.append(input);form.append(line);return input;}
  function pairingPanel(dialog,grant){
    const pairing={...grant,endpoint:location.origin+'/capture/'+grant.grant_id},result=el('section','','capture-pairing');
    result.append(el('h3',ui("配对信息")),el('p',ui("复制到插件并保存。重新生成配对后，旧配对失效；授权范围、已有记忆与附件关联保留。请勿公开配对信息。"),'desktop-settings-help'));
    const text=el('textarea');text.readOnly=true;text.value=JSON.stringify(pairing,null,2);window.ShirosI18n.attribute(text, 'aria-label', ui("插件配对信息"));result.append(text);
    const copy=el('button',ui("复制配对信息"),'secondary-button');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(text.value);window.ShirosI18n.text(copy, ui("已复制"));}catch{text.focus();text.select();}};result.append(copy);dialog.append(result);
  }
  async function reissueDialog(grant,refresh){
    const dialog=el('dialog','','capture-dialog'),head=el('header','','capture-dialog-heading'),close=el('button',ui("关闭"),'secondary-button');head.append(el('h2',ui("重新生成配对")),close);close.onclick=()=>dialog.close();dialog.append(head);
    dialog.append(el('p',ui("为现有授权生成新配对。旧配对将失效，采集范围与已有内容保留。"),'desktop-settings-help'));
    const create=el('button',ui("生成新配对"),'primary-button');create.onclick=async()=>{create.disabled=true;try{const pair=await api('reissue',{id:grant.id});create.remove();pairingPanel(dialog,pair);await refresh();}catch(error){feedback(error,dialog);create.disabled=false;}};dialog.append(create);
    dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }
  async function authorize(parent,refresh){
    const dialog=el('dialog','','capture-dialog'),head=el('header','','capture-dialog-heading'),title=el('h2',ui("授权对话采集")),close=el('button',ui("关闭"),'secondary-button');title.id='capture-grant-title';dialog.setAttribute('aria-labelledby',title.id);close.onclick=()=>dialog.close();head.append(title,close);dialog.append(head);
    const form=el('form','','capture-form'),line=el('label',ui("来源平台"),'capture-field'),provider=el('select');for(const [value,name]of Object.entries(labels)){const option=el('option',name);option.value=value;provider.append(option);}line.append(provider);form.append(line);
    const scopeLine=el('label',ui("采集范围"),'capture-field'),scope=el('select');for(const [value,label]of [['single',ui("仅一个对话")],['all',ui("此平台的批量采集")]]){const option=el('option',label);option.value=value;scope.append(option);}scopeLine.append(scope);form.append(scopeLine);
    const name=field(form,ui("名称（可选，采集时自动使用原对话标题）")),conversation=field(form,ui("对话地址或编号"));conversation.required=true;name.maxLength=180;conversation.maxLength=500;
    const source=field(form,ui("本机会话记录文件（网页插件可留空）"));window.ShirosI18n.attribute(source, 'placeholder', ui("选择的单个 .jsonl / .json 文件绝对路径"));
    const native=field(form,ui("桌面窗口本机接口（可选，与记录文件二选一）"));window.ShirosI18n.attribute(native, 'placeholder', 'http://127.0.0.1:9222');form.append(el('p',ui("桌面窗口接口需要客户端已开启本机调试连接；不会替你启动或重启其他客户端。"),'desktop-settings-help'));
    const filesLabel=el('label','','capture-checkbox'),files=el('input');files.type='checkbox';filesLabel.append(files,el('span',ui("允许采集此对话的附件原件")));form.append(filesLabel);
    const root=field(form,ui("本机附件目录（可选）"));root.disabled=true;files.onchange=()=>root.disabled=!files.checked;
    const explain=el('p',ui("普通内容自动入库，敏感内容待审。无法取得的附件只保留引用。撤销后停止采集，已保存内容保留。"),'desktop-settings-help');form.append(explain);
    function range(){const bulk=scope.value==='all';for(const input of [conversation,source,native,root]){input.closest('label').hidden=bulk;input.disabled=bulk||(input===root&&!files.checked);}conversation.required=!bulk;window.ShirosI18n.text(explain, bulk?ui("允许插件逐条读取此平台的网页对话。点击插件“批量导入”才开始；网页只能覆盖侧栏可加载范围，完整账号请导入官方导出包。普通内容自动入库，敏感内容待审。"):ui("仅授权这个对话。普通内容自动入库，敏感内容待审；撤销后停止采集，已保存内容保留。"));}scope.onchange=range;provider.onchange=()=>{scopeLine.hidden=!['gpt','deepseek'].includes(provider.value);if(scopeLine.hidden)scope.value='single';range();};
    const submit=el('button',ui("允许并生成配对信息"),'primary-button');submit.type='submit';form.append(submit);dialog.append(form);
    form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{
      const bulk=scope.value==='all',grant=await api('grant',{provider:provider.value,all_conversations:bulk,conversation_id:bulk?'':conversation.value.trim(),title:name.value.trim(),source_path:bulk?null:source.value.trim()||null,native_endpoint:bulk?null:native.value.trim()||null,include_files:files.checked,attachment_root:!bulk&&files.checked?(root.value.trim()||null):null});
      form.hidden=true;pairingPanel(dialog,grant);await refresh();
    }catch(e){
      if(e.message==='capture.already_authorized'){
        try{const data=await api('list'),raw=conversation.value.trim(),id=raw.match(/\/c\/([^/?#]+)/)?.[1]||raw,existing=data.items.find(g=>g.enabled&&g.provider===provider.value&&(scope.value==='all'?g.is_bulk:g.conversation_id===id))||data.items.find(g=>g.enabled&&g.provider===provider.value&&g.is_bulk);
          form.querySelector('.capture-existing')?.remove();const notice=el('section','','capture-existing');notice.append(el('p',existing?.is_bulk?ui("此对话已有平台批量授权。可使用原配对，或为这条批量授权生成新配对。"):ui("此对话已有有效授权。可使用原配对，或生成新配对。"),'desktop-settings-help'));
          if(existing){const retry=el('button',ui("重新生成已有配对"),'secondary-button');retry.type='button';retry.onclick=()=>reissueDialog(existing,refresh).catch(error=>feedback(error,dialog));notice.append(retry);}form.append(notice);
        }catch(error){feedback(error,dialog);}
      }else feedback(e,dialog);submit.disabled=false;}};
    dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();name.focus();
  }
  function mount(container){
    const section=el('section','','desktop-settings-section capture-settings');section.append(el('h3',ui("插件与对话同步")),el('p',ui("授权单个对话或平台批量采集；自动使用原对话标题，正文进入记忆库，附件进入文件库并关联。默认关闭。"),'desktop-settings-help'));
    const actions=el('div','','capture-settings-actions'),add=el('button',ui("授权对话"),'secondary-button'),reload=el('button',ui("刷新连接"),'secondary-button'),importAll=el('button',ui("导入全部对话"),'secondary-button');add.type=reload.type=importAll.type='button';importAll.onclick=()=>importDialog();actions.append(add,importAll,reload);section.append(actions);
    const deletedLabel=el('label','','capture-checkbox'),showDeleted=el('input');showDeleted.type='checkbox';deletedLabel.append(showDeleted,el('span',ui("显示已删除的授权记录")));section.append(deletedLabel);
    const list=el('div','','capture-grants');section.append(list);container.append(section);
    async function refresh(){const data=await api('list',{include_deleted:showDeleted.checked});list.replaceChildren();if(!data.items.length)list.append(el('p',ui("暂无授权记录。"),'desktop-settings-help'));
      for(const grant of data.items){const row=el('article','','capture-grant'),info=el('div');info.append(el('strong',grant.title),el('p',ui("{0} · {1}{2}{3}", labels[grant.provider], grant.deleted?ui("已删除"):grant.enabled?ui("已授权"):ui("已撤销"), grant.is_bulk?ui(" · 批量范围"):'', grant.include_files?ui(" · 包含附件"):'')));if(grant.last_error)info.append(el('p',ui("同步未完成：{0}", grant.last_error),'capture-grant-error'));else if(grant.last_seen_at)info.append(el('p',ui("最近同步：{0}", window.ShirosI18n.date(grant.last_seen_at,{timeZone:'Asia/Shanghai'}))));
        row.append(info);if(grant.enabled){const renew=el('button',ui("重新生成配对"),'secondary-button');renew.type='button';renew.onclick=()=>reissueDialog(grant,refresh).catch(error=>feedback(error,section));row.append(renew);const revoke=el('button',ui("撤销授权"),'secondary-button');revoke.type='button';revoke.onclick=async()=>{revoke.disabled=true;try{await api('revoke',{id:grant.id});await refresh();}catch(e){feedback(e,section);revoke.disabled=false;}};row.append(revoke);}else{const remove=el('button',grant.deleted?ui("恢复记录"):ui("删除记录"),grant.deleted?'secondary-button':'danger-button');remove.type='button';window.ShirosI18n.attribute(remove, 'title', grant.deleted?ui("恢复显示，保持撤销状态"):ui("移除授权记录，保留已导入的记忆和文件"));remove.onclick=async()=>{remove.disabled=true;try{await api(grant.deleted?'restore':'delete',{id:grant.id});await refresh();}catch(e){feedback(e,section);remove.disabled=false;}};row.append(remove);}list.append(row);}
    }
    showDeleted.onchange=()=>refresh().catch(e=>feedback(e,section));add.onclick=()=>authorize(section,refresh).catch(e=>feedback(e,section));reload.onclick=()=>refresh().catch(e=>feedback(e,section));refresh().catch(e=>feedback(e,section));
  }
  document.addEventListener('shiros-capture-settings',event=>mount(event.detail.container));
  document.addEventListener('shiros-memory-selected',async event=>{
    try{const data=await api('links',{memory_id:event.detail.id});if(!data.items.length||!event.detail.panel.isConnected)return;
      const section=el('section','','capture-linked-files');section.append(el('h3',ui("关联文件")));
      for(const item of data.items){const row=el('div','','capture-linked-file');row.append(el('span',item.filename));
        if(item.file_id){const button=el('button',ui("下载"),'secondary-button');button.onclick=async()=>{try{const r=await fetch('/ui-api/files/read',{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify({id:item.file_id})});if(!r.ok)throw new Error(ui("文件不可用"));const url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download=item.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(e){feedback(e,section);}};row.append(button);}else row.append(el('small',item.state==='deleted'?ui("原件已删除"):ui("仅有引用，原件尚未取得")));section.append(row);
      }event.detail.panel.append(section);
    }catch(e){feedback(e,event.detail.panel);}
  });
  function importDialog(){
    const dialog=el('dialog','','capture-dialog'),head=el('header','','capture-dialog-heading'),close=el('button',ui("关闭"),'secondary-button');head.append(el('h2',ui("导入全部对话")),close);dialog.append(head);close.onclick=()=>dialog.close();
    dialog.append(el('p',ui("选择 ChatGPT 导出的 ZIP 或 conversations.json。按对话分别存入记忆库并自动使用原标题；同一对话更新原记忆，普通内容自动通过，敏感内容待审。原文件会保存在文件库。"),'desktop-settings-help'));
    const form=el('form','','capture-form'),choose=el('label',ui("选择导出文件"),'secondary-button capture-file-picker'),input=el('input');input.type='file';input.accept='.json,.zip';input.required=true;choose.append(input);form.append(choose);
    const fileName=el('span',ui("尚未选择文件"),'desktop-settings-help');form.append(fileName);input.onchange=()=>window.ShirosI18n.text(fileName, input.files[0]?.name||ui("尚未选择文件"));
    const line=el('label','','capture-checkbox'),files=el('input');files.type='checkbox';line.append(files,el('span',ui("同时导入 ZIP 中可匹配的附件原件")));form.append(line,el('p',ui("找不到的附件仅保留引用。单个导入文件不超过 50 MB；更大的导出包请解压后选择 conversations.json。"),'desktop-settings-help'));
    const submit=el('button',ui("导入全部对话"),'primary-button'),status=el('p','','desktop-settings-help');submit.type='submit';form.append(submit,status);dialog.append(form);
    form.onsubmit=async event=>{event.preventDefault();const file=input.files[0];if(!file)return;submit.disabled=input.disabled=files.disabled=true;close.disabled=true;try{if(file.size>50*1024*1024)throw new Error(ui("文件超过 50 MB，请解压 ZIP 后选择 conversations.json。"));window.ShirosI18n.text(status, ui("正在逐条导入，请保持窗口打开…"));const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error(ui("无法读取文件。")));reader.readAsDataURL(file);});const response=await fetch('/ui-api/conversations/import',{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify({filename:file.name,data,include_files:files.checked})}),result=await response.json();if(!response.ok)throw new Error(result.error||ui("导入未完成"));window.ShirosI18n.text(status, ui("已处理 {0}/{1} 个对话 · 新增或更新 {2} 条 · 待审 {3} 条 · 重复 {4} 条 · 未完成 {5} 个。", result.imported, result.conversations, result.changed, result.pending, result.unchanged, result.failed.length));if(result.privacy_omitted_messages)status.textContent+=ui(" 隐私未收录 {0} 条消息（保留占位）。", result.privacy_omitted_messages);if(result.privacy_omitted_titles)status.textContent+=ui(" 原始标题未收录 {0} 个。", result.privacy_omitted_titles);if(result.failed.length)status.textContent+=ui(" 未完成项目：{0}", window.ShirosI18n.join(result.failed.slice(0,5).map(x=>ui('第 {0} 项 ({1})',x.index,x.code)),', '));document.dispatchEvent(new CustomEvent('shiros-files-open'));}catch(error){feedback(error,dialog);window.ShirosI18n.text(status, ui("导入未完成，可重试；已保存的同一文件不会重复导入。"));}finally{submit.disabled=input.disabled=files.disabled=close.disabled=false;}};
    dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }
  window.ShirosCapture={importAll:importDialog,showFileLinks:async(fileId)=>{
    const data=await api('links',{file_id:fileId}),dialog=el('dialog','','capture-dialog'),head=el('header','','capture-dialog-heading'),close=el('button',ui("关闭"),'secondary-button');head.append(el('h2',ui("关联对话")),close);close.onclick=()=>dialog.close();dialog.append(head);
    const seen=new Set();for(const item of data.items){const key=item.memory_id||item.id;if(seen.has(key))continue;seen.add(key);const row=el('div','','capture-linked-file');row.append(el('span',ui("{0} · {1}", labels[item.provider], item.title)));if(item.memory_id){const open=el('button',ui("查看记忆"),'secondary-button');open.onclick=()=>{dialog.close();document.dispatchEvent(new CustomEvent('shiros-open-memory',{detail:{id:item.memory_id}}));};row.append(open);}else row.append(el('small',ui("记忆待审核")));dialog.append(row);}if(!data.items.length)dialog.append(el('p',ui("暂无可见的关联记忆。"),'desktop-settings-help'));dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }};
})();
