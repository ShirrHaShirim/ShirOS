/* No cookies, account credentials, network scraping, or hidden chat endpoints. */
let enabled=false,last='',lastUrl='';
chrome.runtime.onMessage.addListener((message,_sender,reply)=>{
  if(message.type==='toggle'){enabled=!enabled;last='';lastUrl='';reply({enabled});if(enabled)snapshot();}
});
async function snapshot(){
  if(!enabled)return;
  if(lastUrl&&lastUrl!==location.href){enabled=false;last='';return;}
  lastUrl=location.href;
  const id=location.pathname.match(/\/c\/([a-zA-Z0-9-]+)/)?.[1];if(!id)return;
  const messages=[...document.querySelectorAll('[data-message-author-role]')].map(n=>({role:n.getAttribute('data-message-author-role'),text:n.innerText}));
  if(!messages.length)return;
  const body=`# ${document.title}\n\n来源：${location.href}\n\n浏览器快照：仅包含页面已加载的消息，当前分支；附件及未加载历史请使用官方导出。\n\n`+messages.map(m=>`## ${m.role}\n\n${m.text}\n`).join('\n');
  if(body===last)return;
  try{const result=await chrome.runtime.sendMessage({type:'snapshot',id,body});if(result?.ok)last=body;}catch{}
}
setInterval(snapshot,20000);
