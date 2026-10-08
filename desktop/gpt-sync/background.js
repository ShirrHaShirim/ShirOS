chrome.action.onClicked.addListener(async tab=>{
  try{const result=await chrome.tabs.sendMessage(tab.id,{type:'toggle'});await chrome.action.setBadgeText({tabId:tab.id,text:result.enabled?'ON':''});}catch{await chrome.action.setBadgeText({tabId:tab.id,text:'?'});}
});
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(message.type!=='snapshot'||!sender.tab||!sender.url?.startsWith('https://chatgpt.com/')||!/^[a-zA-Z0-9-]{1,100}$/.test(message.id)||typeof message.body!=='string'||message.body.length>5000000)return;
  const bytes=new TextEncoder().encode(message.body);let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  chrome.downloads.download({url:'data:text/markdown;base64,'+btoa(binary),filename:'ShirOS-GPT/'+message.id+'.md',conflictAction:'overwrite',saveAs:false}).then(()=>reply({ok:true})).catch(()=>reply({ok:false}));return true;
});
