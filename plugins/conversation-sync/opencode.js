import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const configPath=()=>process.env.SHIROS_CAPTURE_CONFIG||path.join(os.homedir(),'.config','ShirOS','capture.json');
async function pairs(){try{const config=JSON.parse(await fs.readFile(configPath(),'utf8'));return Array.isArray(config)?config:config.pairings||[config];}catch{return [];}}
async function attachment(part,pair){
  const url=part.url||'',name=part.filename||part.name||'attachment.bin',result={id:part.id||createHash('sha256').update(url||name).digest('hex'),filename:name};
  if(/^https?:/.test(url))result.reference=url.split('?')[0];
  if(!pair.include_files)return result;
  if(url.startsWith('data:')&&url.includes(';base64,'))result.data=url.split(';base64,')[1];
  else if(url.startsWith('file:')&&pair.attachment_root){
    try{const requested=fileURLToPath(url),real=await fs.realpath(requested),root=await fs.realpath(pair.attachment_root),relative=path.relative(root,real);
      if(relative&&!relative.startsWith('..')&&!path.isAbsolute(relative)&&!(await fs.lstat(requested)).isSymbolicLink()&&(await fs.stat(real)).size<=50*1024*1024)result.data=(await fs.readFile(real)).toString('base64');
    }catch{}
  }return result;
}
export const ShirOSCapture=async({client})=>{
  const timers=new Map();
  async function capture(id){
    // Never ask the host for an unpaired conversation.
    const pair=(await pairs()).find(x=>x.provider==='opencode'&&x.conversation_id===id);if(!pair)return;
    const endpoint=new URL(pair.endpoint);if(endpoint.protocol!=='http:'||endpoint.hostname!=='127.0.0.1'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash||!/^\/capture\/[0-9a-f-]+$/.test(endpoint.pathname))return;
    const result=await client.session.messages({path:{id}}),rows=result.data||result;
    const messages=[];
    for(const row of rows){if(!['user','assistant'].includes(row.info?.role))continue;const parts=row.parts||[];messages.push({id:row.info.id,role:row.info.role,text:parts.filter(x=>x.type==='text').map(x=>x.text).join('\n'),observed_at:row.info.time?.created?new Date(row.info.time.created).toISOString():null,attachments:await Promise.all(parts.filter(x=>x.type==='file').map(x=>attachment(x,pair)))});}
    for(let n=0;n<messages.length;n+=100){const response=await fetch(endpoint.href,{method:'POST',headers:{'Content-Type':'application/json','X-Capture-Token':pair.token},body:JSON.stringify({provider:'opencode',conversation_id:id,messages:messages.slice(n,n+100),message_order:messages.map(x=>x.id)})});if(!response.ok)throw new Error('ShirOS capture rejected: '+response.status);}
  }
  return {event:async({event})=>{
    if(!['message.updated','message.part.updated','session.idle','session.status'].includes(event.type))return;
    const props=event.properties||{},id=props.sessionID||props.info?.sessionID||props.part?.sessionID;if(!id)return;
    clearTimeout(timers.get(id));timers.set(id,setTimeout(()=>{timers.delete(id);capture(id).catch(()=>client.app?.log({body:{service:'shiros-capture',level:'warn',message:'Conversation sync failed. Check ShirOS permission and connection.'}}));},event.type==='session.idle'?0:2000));
  }};
};
