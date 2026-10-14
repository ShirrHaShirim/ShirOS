let tab,info,lastDiagnostic=null,notice=null;
const VERSION='0.7.4.1';
const $=s=>document.querySelector(s);
function tell(text,error=false){notice={text,error,until:Date.now()+6000};$('#status').textContent=text;$('#status').dataset.error=String(error);}
async function send(type,extra={}){if(!tab?.id)throw new Error('页面尚未连接，请稍后再试。');const response=await chrome.tabs.sendMessage(tab.id,{type,...extra});if(!response||typeof response!=='object')throw new Error('页面采集器未返回数据，请刷新网页后重新打开扩展。');return response;}
function validPairs(value){return Array.isArray(value)?value.filter(p=>p&&typeof p==='object'&&typeof p.endpoint==='string'&&typeof p.token==='string'&&p.token&&typeof p.conversation_id==='string'&&p.conversation_id&&typeof p.provider==='string'):[];}
async function currentPage(){const current=await send('capture-info');if(current.version!==VERSION)throw new Error('页面仍在使用旧采集器，请刷新 ChatGPT 网页后再试。');if(!current.provider)throw new Error('请在 ChatGPT 或 DeepSeek 页面打开扩展。');return current;}
async function update(){
  if(notice&&notice.until<Date.now())notice=null;
  info=await currentPage();
  $('#current').textContent=info.title;
  $('#current').title=info.title;
  const titleParts=String(info.title||'').split(/[｜|]/).map(x=>x.trim()),canonical=titleParts.length===3&&titleParts.every(Boolean)&&['完整记录','完整对话'].includes(titleParts[2]);
  const domain=canonical?titleParts[0]:'对话',topic=canonical?titleParts[1]:String(info.title||'未命名对话').replace(/[｜|]/g,' ').replace(/\s+/g,' ').trim();
  $('#memory-title').hidden=!info.is_conversation;$('#memory-title').textContent=`记忆标题：${domain.slice(0,40)}｜${topic.slice(0,150)||'未命名对话'}｜完整对话`;
  $('#single-sync').hidden=!info.is_conversation;
  $('#start').disabled=!info.is_conversation;
  $('#stop').disabled=!info.enabled;
  const stage=info.scan?.phase==='to-top'?'正在回到顶部并加载较早历史':info.scan?.phase==='reading'?'正在逐段读取到末尾':info.scan?.phase==='saving'?'正在保存完整会话':info.scan?.phase==='complete'?(info.pendingReview?'已提交 ShirOS 待审':'已保存到 ShirOS'):info.scan?.phase==='incomplete'?'历史读取未完成':'持续同步中';
  $('#status').textContent=info.lastError||(!info.is_conversation?'可从首页或目录直接批量导入对话。':info.waiting?'正在等待页面呈现消息…':info.enabled?`${stage} · 提问 ${info.roles?.user||0} · 回答 ${info.roles?.assistant||0} · 已提交 ${info.submitted||0} 条${info.privacyOmissions?' · 隐私未收录 '+info.privacyOmissions+' 条（保留占位）':''}${info.privacyTitleOmitted?' · 原始标题未收录':''}${info.excludedMessages?' · 已跳过 '+info.excludedMessages+' 条已拒绝消息':''}。`:'同步未开启。');
  $('#scroll').disabled=!info.enabled||info.scrolling||info.waiting;
  lastDiagnostic=await send('capture-diagnostics');
  $('#diagnostics').textContent=JSON.stringify(lastDiagnostic,null,2);
  const {job}=await chrome.runtime.sendMessage({type:'capture-all-status'});
  const counts=job?.running&&job.current?.roles?job.current.roles:info.roles;
  $('#role-counts').hidden=!info.is_conversation&&!(job?.running&&job.current);
  $('#user-count').textContent=counts?.user||0;$('#assistant-count').textContent=counts?.assistant||0;
  if(notice)$('#status').textContent=notice.text;
  $('#status').dataset.error=String(notice?notice.error:!!info.lastError);
  $('#bulk-status').hidden=!job;$('#bulk-status').dataset.error=String(!!job?.error);
  $('#bulk-progress').hidden=!job?.total;$('#bulk-progress').max=Math.max(1,job?.total||1);$('#bulk-progress').value=(job?.completed||0)+(job?.failed||0);
  $('#all').disabled=!!job?.running||!['gpt','deepseek'].includes(info.provider);
  $('#all-stop').disabled=!job?.running;
  $('#bulk-status').textContent=job?`${job.running?(job.phase==='discovering'?'正在读取对话目录':'批量采集中'):job.paused?'批量已暂停':job.cancelled?'批量已停止':'批量结束'} · 已发现 ${job.total} · 成功 ${job.completed} · 未完成 ${job.failed}${job.current&&job.running?' · 第 '+job.current.index+' 条 · 提问 '+(job.current.roles?.user||0)+' / 回答 '+(job.current.roles?.assistant||0)+(job.current.scan?.phase==='to-top'?' · 返回顶部':job.current.scan?.phase==='reading'?' · 顺序读取':job.current.scan?.phase==='saving'?' · 保存':''):''}${job.pendingConversations?' · 待审 '+job.pendingConversations+' 会话':''}${job.privacyOmitted?' · 隐私未收录 '+job.privacyOmitted+' 条消息':''}${job.projects?' · 已检查项目 '+job.projects:''}${job.projectFailed?' · 目录未完成 '+job.projectFailed:''}${job.discovery?.truncated?' · 部分目录未读完，可重试或导入官方导出包':''}${job.error?' · '+job.error:''}`:'';
}
async function run(fn){try{await fn();}catch(e){tell(e.message,true);}}
$('#save').onclick=()=>run(async()=>{const pair=JSON.parse($('#pairing').value);if(!validPairs([pair]).length)throw new Error('配对信息无效。');const url=new URL(pair.endpoint);if(url.hostname!=='127.0.0.1'||url.protocol!=='http:'||!/^\/capture\/[0-9a-f-]{36}$/.test(url.pathname)||url.username||url.password||url.search||url.hash)throw new Error('配对信息无效。');const {pairings}=await chrome.storage.local.get('pairings');const items=validPairs(pairings).filter(x=>x.provider!==pair.provider||x.conversation_id!==pair.conversation_id);items.push(pair);await chrome.storage.local.set({pairings:items});$('#pairing').value='';tell('配对已保存，点击开始同步后才采集。');});
$('#start').onclick=()=>run(async()=>{const current=await currentPage();if(!current.is_conversation||!current.conversation_id)throw new Error('首页请使用批量导入对话。');const stored=await chrome.storage.local.get('pairings'),pairs=validPairs(stored.pairings);const pair=pairs.find(x=>x.conversation_id===current.conversation_id&&x.provider===current.provider)||pairs.find(x=>x.all_conversations===true&&x.provider===current.provider);if(!pair)throw new Error('请在 ShirOS 设置中授权此对话，并粘贴配对信息。');const result=await send('capture-start',{pair,autoScroll:$('#auto-scroll').checked});if(!result.ok)throw new Error(result.error||'无法开始同步，请刷新网页后重试。');await update();});
$('#all').onclick=()=>run(async()=>{const current=await currentPage(),stored=await chrome.storage.local.get('pairings'),pair=validPairs(stored.pairings).find(x=>x.all_conversations===true&&x.provider===current.provider);if(!pair)throw new Error('请在 ShirOS 设置里选择“此平台的批量采集”，生成并保存配对信息。');const result=await chrome.runtime.sendMessage({type:'capture-all',provider:pair.provider,endpoint:pair.endpoint,tabId:tab.id});if(!result?.ok)throw new Error(result?.error||'批量采集器未连接。');await update();});
$('#all-stop').onclick=()=>run(async()=>{await chrome.runtime.sendMessage({type:'capture-all-stop'});await update();});
$('#stop').onclick=()=>run(async()=>{await send('capture-stop');await update();});$('#scroll').onclick=()=>run(()=>send('capture-scroll'));
$('#copy-diagnostics').onclick=()=>run(async()=>{const {job,version}=await chrome.runtime.sendMessage({type:'capture-all-status'}),data={extension:{manifest:chrome.runtime.getManifest().version,popup:VERSION,worker:version},current:lastDiagnostic,batch:job?{total:job.total,completed:job.completed,failed:job.failed,paused:job.paused,running:job.running,phase:job.phase,current:job.current,discovery:job.discovery,failures:job.failures}:null};try{await navigator.clipboard.writeText(JSON.stringify(data,null,2));$('#copy-diagnostics').textContent='已复制';}catch{const selection=getSelection(),range=document.createRange();range.selectNodeContents($('#diagnostics'));selection.removeAllRanges();selection.addRange(range);$('#status').textContent='诊断已选中，请复制。';}});
run(async()=>{[tab]=await chrome.tabs.query({active:true,currentWindow:true});if(!tab?.id)throw new Error('请先打开 ChatGPT 首页或对话页面。');try{await update();}catch{await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{try{window.shirosCaptureController?.dispose?.();}catch{}delete window.shirosCaptureController;}});await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});await update();}setInterval(()=>run(update),1000);});
