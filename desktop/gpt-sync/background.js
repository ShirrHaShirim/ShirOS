/* Read only after explicit Start; batch navigation uses a separate, owned tab. */
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const infoUrl=provider=>provider==='gpt'?'https://chatgpt.com':'https://chat.deepseek.com';
const VERSION='0.7.4.1';
let job=null,ownedTab=null,discoveryTab=null;
function endpoint(pair){const u=new URL(pair.endpoint);if(u.hostname!=='127.0.0.1'||u.protocol!=='http:'||!/^\/capture\/[0-9a-f-]{36}$/.test(u.pathname)||u.username||u.password||u.search||u.hash)throw new Error('配对地址无效。');return u.href;}
function pageKey(url,provider){const u=new URL(url),ok=provider==='gpt'?u.protocol==='https:'&&u.hostname==='chatgpt.com':provider==='deepseek'?u.protocol==='https:'&&u.hostname==='chat.deepseek.com':['127.0.0.1','localhost'].includes(u.hostname);if(!ok)throw new Error('来源页面与授权平台不匹配。');return u.searchParams.get('session')||u.searchParams.get('sessionId')||u.pathname.match(/\/c\/([^/]+)\/?$/)?.[1]||u.pathname.replace(/\/$/,'').split('/').pop();}
function failure(code,message,retryable=false){return Object.assign(new Error(message),{code,retryable});}
async function request(pair,path='',body={}){
  for(let attempt=0;attempt<3;attempt++){
    try{
      const r=await fetch(endpoint(pair)+path,{method:'POST',headers:{'Content-Type':'application/json','X-Capture-Token':pair.token},body:JSON.stringify(body),credentials:'omit',signal:AbortSignal.timeout(60000)});
      let data;try{data=await r.json();}catch{throw failure('capture.http_'+r.status,'ShirOS 返回了无效响应（HTTP '+r.status+'）。',r.status>=500||r.status===429);}
      if(!r.ok){const code=/^(capture|privacy|memory|files|intake|permission)\.[a-z_]+$/.test(data?.error||'')?data.error:'capture.http_'+r.status;throw failure(code,code==='capture.schema_not_ready'?'ShirOS 数据库尚未完成升级。':code==='capture.storage_unavailable'?'ShirOS 存储暂不可用。':code==='privacy.persistence_denied'?'ShirOS 隐私检查拒绝保存。请确认本机运行端已更新；不要发送对话正文或密钥。':code,r.status>=500||r.status===429);}
      if(!data||typeof data!=='object'||Array.isArray(data))throw failure('capture.invalid_response','ShirOS 返回了无效响应。');
      return data;
    }catch(error){
      if(!error.code)error=failure('capture.network_unavailable','无法连接 ShirOS，请确认桌面端正在运行。',true);
      if(!error.retryable||attempt===2)throw error;
      await pause(400*(attempt+1));
    }
  }
}
function samePage(actual,expected){const a=new URL(actual),b=new URL(expected),path=u=>/^\/g\/g-p-[^/]+\/project\/?$/.test(u.pathname)?u.pathname.replace(/\/project\/?$/,''):u.pathname.replace(/\/$/,'');return a.origin===b.origin&&path(a)===path(b);}
function missingReceiver(error){return /receiving end|could not establish connection|message port closed|frame.*removed/i.test(error?.message||'');}
async function inject(id){
  await chrome.scripting.executeScript({target:{tabId:id},func:()=>{try{window.shirosCaptureController?.dispose?.();}catch{}delete window.shirosCaptureController;}});
  await chrome.scripting.executeScript({target:{tabId:id},files:['content.js']});
}
async function pageMessage(id,message,pair,expected){
  for(let attempt=0;attempt<3;attempt++){
    const tab=await chrome.tabs.get(id);pageKey(tab.url,pair.provider);
    if(expected&&!samePage(tab.url,expected)&&tab.status==='loading')throw failure('capture.page_loading','页面仍在加载。',true);
    if(expected&&!samePage(tab.url,expected))throw failure('capture.page_changed','采集页面发生跳转，已停止读取这个页面。');
    try{return await chrome.tabs.sendMessage(id,message);}catch(error){
      if(!missingReceiver(error)||attempt===2)throw failure('capture.receiver_unavailable','页面采集器连接失败，请刷新网页或允许扩展访问此网站。');
      if(tab.status==='complete'){
        try{await inject(id);}
        catch{throw failure('capture.site_permission','扩展无法访问页面，请在 Chrome 扩展设置中允许访问 ChatGPT / DeepSeek。');}
      }
      await pause(200*(attempt+1));
    }
  }
}
async function savedPair(provider,conversation,root){const stored=await chrome.storage.local.get('pairings'),pairs=Array.isArray(stored.pairings)?stored.pairings.filter(p=>p&&typeof p==='object'&&typeof p.endpoint==='string'&&typeof p.token==='string'&&p.token&&typeof p.conversation_id==='string'&&p.conversation_id):[];return pairs.find(p=>p.provider===provider&&(root?p.endpoint===root:p.conversation_id===conversation))||(!root&&pairs.find(p=>p.provider===provider&&p.all_conversations===true));}
async function persist(){if(job)await chrome.storage.session.set({bulkJob:{running:job.running,total:job.total,completed:job.completed,failed:job.failed,pendingConversations:job.pendingConversations,privacyOmitted:job.privacyOmitted,error:job.error,failures:job.failures,cancelled:job.cancelled,paused:job.paused,projects:job.projects,projectFailed:job.projectFailed,started:job.started,phase:job.phase,discovery:job.discovery,current:job.current}});}
async function ready(id,url,pair){for(let n=0;n<90;n++){if(job.cancelled)throw new Error('已停止批量采集。');try{const info=await pageMessage(id,{type:'capture-info'},pair,url);if(samePage(info.url,url)&&info.page_ready==='complete'){if(info.version!==VERSION){await inject(id);continue;}const nodes=await pageMessage(id,{type:'capture-diagnostics'},pair,url);if(nodes.candidate_nodes||n>=10)return info;}}catch(error){if(['capture.page_changed','capture.site_permission'].includes(error.code))throw error;}await pause(500);}throw new Error('页面未及时加载，请登录后重试。');}
async function batch(pair,tabId){
  if(job?.running)throw new Error('已有批量采集正在运行。');
  if(!pair?.all_conversations||!['gpt','deepseek'].includes(pair.provider))throw new Error('请在 ShirOS 设置中明确授权此平台的批量采集。');
  const auth=await request(pair,'/authorize');if(!auth.all_conversations||auth.provider!==pair.provider)throw new Error('批量授权无效。');
  const current=await chrome.tabs.get(tabId);pageKey(current.url,pair.provider);
  const info=await pageMessage(tabId,{type:'capture-info'},pair,current.url);pageKey(info.url,pair.provider);
  job={running:true,total:0,completed:0,failed:0,pendingConversations:0,privacyOmitted:0,error:'',failures:[],cancelled:false,paused:false,projects:0,projectFailed:0,started:Date.now(),phase:'discovering',discovery:{pages:0,truncated:false,counts:[]}};await persist();
  runBatch(pair,tabId).catch(async error=>{job.error=error.message;job.paused=!job.cancelled;job.running=false;await persist();});return {ok:true};
}
async function runBatch(pair,tabId){
  const keepAlive=setInterval(()=>persist().catch(()=>{}),2000);
  try{
    const initial=await chrome.tabs.get(tabId);
    ownedTab=(await chrome.tabs.create({url:'about:blank',active:false})).id;
    await chrome.tabs.update(ownedTab,{url:initial.url});
    await ready(ownedTab,initial.url,pair);
    discoveryTab=ownedTab;
    const found=await pageMessage(ownedTab,{type:'capture-discover',pair},pair,initial.url);discoveryTab=null;
    if(!found?.ok)throw new Error(found?.error||'无法识别对话目录。');
    const discovered=new Map(),queue=[],queued=new Set();
    function merge(result){
      for(const item of result.links||[]){try{const id=pageKey(item.url,pair.provider);if(!/\/c\/[^/]+\/?$/.test(new URL(item.url).pathname))continue;if(!discovered.has(id)||item.url.includes('/g/'))discovered.set(id,item);}catch{}}
      for(const item of [...result.projects||[],...result.directories||[]]){try{const u=new URL(item.url);if(u.origin!==new URL(infoUrl(pair.provider)).origin||u.search||u.hash||!/^\/(?:g\/g-p-[^/]+(?:\/project)?|projects|history|chats)\/?$/.test(u.pathname))continue;const key=u.pathname.replace(/\/project\/?$/,'').replace(/\/$/,'');if(!queued.has(key)){queued.add(key);queue.push(item);}}catch{}}
      job.total=discovered.size;job.discovery.pages++;job.discovery.truncated||=!!result.diagnostics?.truncated;
      if(job.discovery.counts.length<100)job.discovery.counts.push(result.diagnostics||{conversations:(result.links||[]).length,projects:(result.projects||[]).length});
    }
    merge(found);await persist();
    for(let n=0;n<queue.length&&!job.cancelled;n++){
      if(n>=500){job.discovery.truncated=true;break;}
      const directory=queue[n];try{await request(pair,'/authorize');await chrome.tabs.update(ownedTab,{url:directory.url});await ready(ownedTab,directory.url,pair);discoveryTab=ownedTab;const result=await pageMessage(ownedTab,{type:'capture-discover',pair},pair,directory.url);discoveryTab=null;if(!result?.ok)throw failure(result?.code||'capture.directory_unavailable',result?.error||'对话目录未加载。');merge(result);if(directory.url.includes('/g/'))job.projects++;}
      catch(error){if(!job.cancelled){job.projectFailed++;job.error=error.message;job.discovery.truncated=true;}if(error.message.includes('not_authorized'))job.cancelled=true;if(error.retryable||['capture.receiver_unavailable','capture.site_permission'].includes(error.code)){job.paused=true;throw error;}}
      await persist();
    }
    const links=[...discovered.values()].filter(x=>{try{pageKey(x.url,pair.provider);return true;}catch{return false;}});
    if(!links.length)throw new Error('侧栏未发现对话。整个账号请使用导出包导入。');
    job.total=links.length;job.phase='capturing';await persist();
    if(job.cancelled)return;
    let recognitionFailures=0;
    for(let index=0;index<links.length&&!job.cancelled;index++){
      try{
        await request(pair,'/authorize');
        await chrome.tabs.update(ownedTab,{url:links[index].url});
        await ready(ownedTab,links[index].url,pair);
        const result=await pageMessage(ownedTab,{type:'capture-start',pair,autoScroll:true},pair,links[index].url);if(!result?.ok)throw new Error(result?.error||'无法开始采集。');
        let info,restarts=0;
        for(let n=0;n<2400&&!job.cancelled;n++){
          await pause(500);info=await pageMessage(ownedTab,{type:'capture-info'},pair,links[index].url);
          job.current={index:index+1,scan:info.scan,roles:info.roles};
          if(!info.enabled&&!job.cancelled){if(restarts++>=2)throw failure('capture.receiver_unavailable','页面采集器反复中断，已暂停。');const resumed=await pageMessage(ownedTab,{type:'capture-start',pair,autoScroll:true},pair,links[index].url);if(!resumed?.ok)throw new Error(resumed?.error||'无法恢复同步。');continue;}
          if(info.lastError&&!info.waiting&&(!info.scrolling||!info.lastError.startsWith('尚未识别消息')))throw failure(info.lastErrorCode||'capture.page_error',info.lastError,/^capture\.(?:http_5|storage_unavailable|schema_not_ready|network_unavailable)/.test(info.lastErrorCode||''));
          if(!info.waiting&&!info.scrolling&&info.count)break;
        }
        if(job.cancelled)break;
        if(!info?.count||info.scrolling)throw new Error('历史加载未完成，未标记为成功。');
        const final=await pageMessage(ownedTab,{type:'capture-finish'},pair,links[index].url);
        if(!final?.ok)throw failure(final?.code||'capture.page_error',final?.error||'最后一批消息未保存。',/^capture\.(?:http_5|storage_unavailable|schema_not_ready|network_unavailable)/.test(final?.code||''));job.completed++;job.pendingConversations+=final.pendingReview?1:0;job.privacyOmitted+=final.privacyOmissions||0;recognitionFailures=0;job.error='';
      }catch(error){if(job.cancelled)break;job.failed++;job.error=error.message;const diagnostics=await chrome.tabs.sendMessage(ownedTab,{type:'capture-diagnostics'}).catch(()=>null);if(job.failures.length<20)job.failures.push({index:index+1,error:error.message,diagnostics});await chrome.tabs.sendMessage(ownedTab,{type:'capture-stop'}).catch(()=>{});if(error.message.includes('not_authorized'))job.cancelled=true;if(error.retryable||['capture.receiver_unavailable','capture.site_permission'].includes(error.code)){job.paused=true;job.error=error.message+' 批量已暂停，已保存记录保留。';await persist();break;}if(['capture.answers_missing','capture.top_incomplete','capture.history_incomplete','capture.scroll_blocked'].includes(error.code)||error.message.startsWith('尚未识别消息')||error.message.includes('页面未及时加载'))recognitionFailures++;else recognitionFailures=0;if(recognitionFailures>=2){job.paused=true;job.error='连续两个对话未完整识别，批量已暂停。请复制识别诊断以检查提问、回答和滚动状态。';await persist();break;}}
      await persist();
    }
  }finally{
    clearInterval(keepAlive);discoveryTab=null;
    if(ownedTab!==null){try{await chrome.tabs.remove(ownedTab);}catch{}ownedTab=null;}
    job.running=false;await persist();
  }
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  const supported=['capture-snapshot','capture-check','capture-all','capture-all-stop','capture-all-status'];if(!supported.includes(message.type))return;
  (async()=>{
    if(message.type==='capture-all-status'){const {bulkJob}=await chrome.storage.session.get('bulkJob');return {ok:true,version:VERSION,job:job|| (bulkJob?.running?{...bulkJob,running:false,error:'浏览器中断了批量任务，可重新开始，已保存消息不会重复。'}:bulkJob)};}
    if(message.type==='capture-all-stop'){if(job){job.cancelled=true;if(discoveryTab!==null)await chrome.tabs.sendMessage(discoveryTab,{type:'capture-discover-stop'}).catch(()=>{});if(ownedTab!==null)await chrome.tabs.sendMessage(ownedTab,{type:'capture-stop'}).catch(()=>{});await persist();}return {ok:true};}
    if(message.type==='capture-all'){const pair=await savedPair(message.provider,null,message.endpoint);return batch(pair,message.tabId);}
    if(!sender.tab)throw new Error('来源页面无效。');
    if(message.type==='capture-check'){const pair=await savedPair(message.provider,message.conversation_id,message.endpoint);if(!pair)throw new Error('此对话尚未授权。');if(pageKey(sender.url,message.provider)!==message.conversation_id)throw new Error('当前页面与授权不匹配。');const auth=await request(pair,'/authorize');if(auth.provider!==message.provider||(!auth.all_conversations&&auth.conversation_id!==message.conversation_id))throw new Error('capture.conversation_mismatch');return auth;}
    const packet=message.packet;if(!packet||typeof packet!=='object'||!packet.provider||!packet.conversation_id)throw failure('capture.invalid_packet','采集消息缺少对话信息，未提交。');const pair=await savedPair(packet.provider,packet.conversation_id,message.endpoint);
    if(!pair)throw new Error('此对话尚未授权，请在 ShirOS 设置里授权并配对。');
    if(pageKey(sender.url,packet.provider)!==packet.conversation_id)throw new Error('当前页面与采集的对话不匹配。');
    if(!pair.all_conversations&&pair.conversation_id!==packet.conversation_id)throw new Error('capture.conversation_mismatch');
    return request(pair,'',packet);
  })().then(data=>reply({ok:true,...data})).catch(error=>reply({ok:false,error:error.message,code:error.code,retryable:!!error.retryable}));return true;
});
