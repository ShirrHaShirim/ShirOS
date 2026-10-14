/* No chat is read until Start is pressed; no hidden endpoints or account credentials. */
(() => {
  const VERSION='0.7.4.1';
  if(window.shirosCaptureController?.version===VERSION)return;
  try{window.shirosCaptureController?.dispose?.();}catch{}
  let enabled=false,pair=null,startingUrl='',scrolling=false,scrollStop=false,timer=null,lastError='',lastErrorCode='',lastSync=null,epoch=0,flushing=false,discoveryStop=false,waiting=false;
  const messages=new Map(),sent=new Map(),localFiles=new Map();
  let messageOrder=[],previousVisible=[],previousTop=Infinity,sentOrder='',excludedMessages=0,pendingReview=false,privacyOmissions=0,privacyTitleOmitted=false;
  const attachmentBytes=new Map();
  const fingerprint=text=>[2166136261,3335557771].map(seed=>Array.from(text).reduce((h,c)=>Math.imul(h^c.charCodeAt(0),16777619)>>>0,seed).toString(16).padStart(8,'0')).join('');
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const MODERN_USER='[data-thread-user-message-navigation-content]';
  const USER_BUBBLE='[data-user-message-bubble],.bg-user-message,.text-user-message,[class*="bubble-"],.whitespace-pre-wrap';
  const MODERN_BODY='[data-markdown-copy],[class*="MarkdownRoot-"]';
  const MODERN_TURN='[data-turn-key],[data-content-search-turn-key],[data-chatgpt-conversation-selection-target]';
  let scanState={phase:'idle',topReached:false,bottomReached:false,truncated:false,steps:0},fullScanRequested=false;
  const GPT_SELECTORS='[data-message-author-role],[data-author-role],[data-message-id],[data-role="user"],[data-role="assistant"],[data-testid^="conversation-turn"],[data-turn],[data-testid="user-message"],[data-testid="assistant-message"],main article,.markdown,'+USER_BUBBLE+','+MODERN_USER+','+MODERN_BODY+','+MODERN_TURN;
  const BOUNDARY='[data-message-author-role],[data-author-role],[data-testid^="conversation-turn"],[data-turn],[data-message-id],article';
  let lastScan={candidates:0,hidden:0,unknown_role:0,empty:0,recognized:0};
  function parentOf(node){try{return node.parentElement||node.getRootNode()?.host||node.ownerDocument?.defaultView?.frameElement||null;}catch{return null;}}
  function excluded(node){for(let p=node;p;p=parentOf(p))if(p.matches('textarea,[contenteditable=true],nav,aside,header,[role=button],[role=toolbar],[role=menu],[data-composer-input],[data-chatgpt-composer],[data-app-shell-titlebar],[aria-hidden=true]'))return true;return false;}
  let styleCache=null,rootCache=null,rootMemo=null,rootMemoAt=0;
  let scrollAxes=new WeakMap();
  const styleOf=node=>{if(styleCache?.has(node))return styleCache.get(node);const result=(node.ownerDocument.defaultView||window).getComputedStyle(node);styleCache?.set(node,result);return result;};
  function roots(){if(rootMemo&&Date.now()-rootMemoAt<1000)return rootMemo;const found=[],seen=new Set();function visit(root){if(!root||seen.has(root)||found.length>=100)return;seen.add(root);found.push(root);for(const node of [...root.querySelectorAll('*')].slice(0,30000)){if(node.shadowRoot)visit(node.shadowRoot);if(node.tagName==='IFRAME'){try{const url=new URL(node.src||'about:blank',location.href);if(url.origin===location.origin||url.protocol==='about:')visit(node.contentDocument);}catch{}}}}visit(document);rootMemo=found;rootMemoAt=Date.now();return found;}
  function queryAll(selector,contexts=roots()){return [...new Set(contexts.flatMap(root=>[...root.querySelectorAll(selector)]))];}
  function schema(root){let budget=55;function node(n,depth){if(!n||!budget--||depth>4)return null;const item={tag:n.tagName.toLowerCase()};const id=n.getAttribute('data-testid');if(id&&/^[a-z-]{1,60}$/i.test(id))item.testid=id;const attrs=[...n.attributes].map(a=>a.name).filter(a=>/^(data-|role|aria-)/.test(a));if(attrs.length)item.attributes=attrs.slice(0,8);const children=[...n.children].filter(x=>!['SCRIPT','STYLE','SVG','NOSCRIPT'].includes(x.tagName));if(children.length)item.children=children.slice(0,5).map(c=>node(c,depth+1)).filter(Boolean);return item;}return node(root,0);}
  function info(){const u=new URL(location.href),id=u.searchParams.get('session')||u.searchParams.get('sessionId')||u.pathname.match(/\/c\/([^/]+)\/?$/)?.[1]||(u.hostname==='chatgpt.com'?'':u.pathname.replace(/\/$/,'').split('/').pop());
    const active=[...document.querySelectorAll('nav a[href],aside a[href]')].find(a=>new URL(a.href,location.href).pathname===u.pathname);
    const title=(active?.getAttribute('title')||active?.innerText||document.title).replace(/\s*[-–|]\s*ChatGPT\s*$/i,'').trim();
    return{url:u.origin+u.pathname,conversation_id:id,is_conversation:!!id,provider:u.hostname==='chatgpt.com'?'gpt':u.hostname==='chat.deepseek.com'?'deepseek':null,title:id?(title||'对话记录'):(u.pathname==='/'?'ChatGPT 首页':'ChatGPT 目录'),version:VERSION,page_ready:document.readyState};}
  function diagnostics(){const contexts=roots(),frames=queryAll('iframe',contexts).map(frame=>{let host='';try{host=new URL(frame.src||'about:blank',location.href).host;}catch{}let readable=false;try{readable=!!frame.contentDocument;}catch{}return{host:host||'inline',readable};});return{version:VERSION,provider:info().provider,conversation_id:info().conversation_id,
    main:document.querySelectorAll('main').length,role_nodes:document.querySelectorAll('[data-message-author-role]').length,
    turn_nodes:document.querySelectorAll('article[data-turn],article[data-testid^="conversation-turn"]').length,
    candidate_nodes:queryAll(GPT_SELECTORS+',.ds-markdown',contexts).length,
    roots:contexts.length,frame_details:frames,frames:frames.length,
    markers:{message_ids:queryAll('[data-message-id]',contexts).length,markdown:queryAll('.markdown',contexts).length,user_text:queryAll('.whitespace-pre-wrap',contexts).length,articles:queryAll('article',contexts).length},
    last_scan:lastScan,data_attributes:[...new Set(queryAll('main *',contexts).flatMap(n=>[...n.attributes].map(a=>a.name).filter(a=>a.startsWith('data-'))))].slice(0,40),
    class_markers:[...new Set(queryAll('main *',contexts).flatMap(n=>[...n.classList].filter(c=>/message|markdown|prose|chat|turn|conversation|bubble|user|assistant/i.test(c)&&/^[a-z][a-z0-9_-]{1,80}$/i.test(c))))].slice(0,40),
    modern_markers:{user:queryAll(MODERN_USER,contexts).length,answers:queryAll(MODERN_BODY,contexts).length,turns:queryAll(MODERN_TURN,contexts).length,timeline:queryAll('[data-app-action-timeline-scroll]',contexts).length},
    scan:scanState,scroll:scrollDetails(),roles:roleCounts(),message_schema:queryAll(MODERN_USER+','+MODERN_BODY,contexts).filter(n=>!excluded(n)&&!hidden(n)).slice(0,4).map(schema),
    main_schema:schema(document.querySelector('main')||document.body)};}
  function ancestor(node,selector){for(let p=node;p;p=parentOf(p))if(p.matches?.(selector))return p;return null;}
  const ROLE_SELECTOR='[data-message-author-role],[data-author-role],[data-role="user"],[data-role="assistant"],[data-turn="user"],[data-turn="assistant"],[data-testid="user-message"],[data-testid="assistant-message"]';
  function userBoundary(node){for(let p=node;p;p=parentOf(p)){
    if(p.matches(USER_BUBBLE)&&!ancestor(p,MODERN_BODY+',.markdown,.ds-markdown'))return p;
    // This marker can wrap the entire thread in newer ChatGPT layouts.
    if(p.matches(MODERN_USER)&&!p.querySelector(MODERN_BODY)&&p.querySelectorAll(MODERN_TURN).length<2)return p;
  }return null;}
  function roleOf(node){
    if(!node)return null;
    const explicit=ancestor(node,ROLE_SELECTOR+',[data-chatgpt-agent-turn-start]');
    if(explicit?.hasAttribute('data-chatgpt-agent-turn-start'))return 'assistant';
    const kind=explicit?.dataset.messageAuthorRole||explicit?.dataset.authorRole||explicit?.dataset.role||explicit?.dataset.turn||node.dataset.chatFlowKind||'';
    if(/^(user|assistant|tool)(?:$|-)/i.test(kind))return kind.match(/^(user|assistant|tool)/i)[1].toLowerCase();
    if(explicit?.dataset.testid==='user-message')return 'user';
    if(explicit?.dataset.testid==='assistant-message')return 'assistant';
    if(userBoundary(node))return 'user';
    if(node.matches(MODERN_BODY)||node.classList.contains('markdown')||node.classList.contains('ds-markdown'))return 'assistant';
    const heading=node.querySelector('h1,h2,h3,h4,h5,h6,[data-testid="author"]')?.textContent?.trim()||node.getAttribute('aria-label')||'';
    if(/^(you said|你说|您说)/i.test(heading))return 'user';if(/^(chatgpt said|chatgpt.*说|assistant)/i.test(heading))return 'assistant';
    const marker=(node.dataset.testid||node.dataset.chatAnchorKey||node.className||'').toString();
    return /user-message/i.test(marker)?'user':/assistant-message|ds-markdown/i.test(marker)?'assistant':node.classList.contains('whitespace-pre-wrap')?'user':null;
  }
  function unitOf(candidate){
    if(candidate.querySelector(ROLE_SELECTOR))return null;
    const agent=ancestor(candidate,'[data-chatgpt-agent-turn-start]');
    if(agent?.querySelector(MODERN_BODY))return agent;
    const explicit=ancestor(candidate,ROLE_SELECTOR);
    if(explicit)return explicit;
    let user=userBoundary(candidate);
    if(user){while(parentOf(user)?.matches?.(USER_BUBBLE)&&!parentOf(user).querySelector(MODERN_BODY))user=parentOf(user);return user;}
    if(candidate.querySelector(ROLE_SELECTOR+','+MODERN_USER+','+USER_BUBBLE)&&!candidate.matches(MODERN_BODY))return null;
    const body=ancestor(candidate,MODERN_BODY+',.markdown,.whitespace-pre-wrap');
    const turn=ancestor(candidate,MODERN_TURN+',[data-testid^="conversation-turn"],[data-turn],article');
    if(body&&turn){
      // One turn may hold both the question and answer. Do not label the entire turn as user.
      const mixed=!!turn.querySelector(MODERN_USER+','+ROLE_SELECTOR+','+USER_BUBBLE);
      if(mixed)return body;
      return turn;
    }
    if(body)return body;
    if(turn){if(turn.querySelector(MODERN_BODY)&&turn.querySelector(MODERN_USER))return null;return turn;}
    return candidate;
  }
  function roleCounts(){const counts={user:0,assistant:0,tool:0};for(const message of messages.values())counts[message.role]=(counts[message.role]||0)+1;return counts;}
  function root(){
    if(rootCache?.isConnected&&rootCache.clientHeight>0&&rootCache.scrollHeight>rootCache.clientHeight+2)return rootCache;
    const contexts=roots(),ancestors=new Set(),candidates=queryAll(GPT_SELECTORS,contexts).filter(n=>!hidden(n)&&!excluded(n));
    for(const message of candidates){let n=parentOf(message);while(n){ancestors.add(n);n=parentOf(n);}}
    // Inspect message ancestors only; main * can contain tens of thousands of unrelated widgets.
    const nodes=[...new Set([...ancestors,...queryAll('[data-app-action-timeline-scroll],[data-app-shell-focus-area],main',contexts)])].filter(x=>!excluded(x)&&!hidden(x)&&x.clientHeight>0&&/auto|scroll|hidden/.test(styleOf(x).overflowY)&&x.scrollHeight>x.clientHeight+2);
    function score(node){let depth=0;for(let p=node;p;p=parentOf(p))depth++;return candidates.filter(c=>node.contains(c)).length*100+(node.hasAttribute('data-app-action-timeline-scroll')?10:0)+depth;}
    rootCache=nodes.sort((a,b)=>score(b)-score(a))[0]||contexts.find(r=>r.nodeType===9&&r.querySelector(GPT_SELECTORS))?.scrollingElement||document.scrollingElement;
    return rootCache;
  }
  function scrollAxis(pane,probe=false){
    if(scrollAxes.has(pane))return scrollAxes.get(pane);
    let reversed=pane.scrollTop<0||styleOf(pane).flexDirection==='column-reverse';
    if(probe&&pane.scrollHeight>pane.clientHeight+2){
      const original=pane.scrollTop,behavior=pane.style.scrollBehavior,snap=pane.style.scrollSnapType;
      pane.style.scrollBehavior='auto';pane.style.scrollSnapType='none';
      pane.scrollTop=1;const positive=pane.scrollTop;pane.scrollTop=-1;const negative=pane.scrollTop;
      reversed=negative<0&&positive<=0;pane.scrollTop=original;
      pane.style.scrollBehavior=behavior;pane.style.scrollSnapType=snap;
    }
    if(probe&&pane.scrollHeight>pane.clientHeight+2)scrollAxes.set(pane,reversed);
    return reversed;
  }
  function scrollPosition(pane){const extent=Math.max(0,pane.scrollHeight-pane.clientHeight);return Math.max(0,Math.min(extent,scrollAxis(pane)?extent+pane.scrollTop:pane.scrollTop));}
  function scrollDetails(){const pane=root(),style=styleOf(pane);return {tag:pane.tagName.toLowerCase(),attributes:[...pane.attributes].map(a=>a.name).filter(n=>/^(data-|role)/.test(n)).slice(0,12),top:Math.round(pane.scrollTop),position:Math.round(scrollPosition(pane)),reversed:scrollAxis(pane),height:pane.scrollHeight,viewport:pane.clientHeight,overflow:style.overflowY,flex_direction:style.flexDirection};}
  const bytesToBase64=bytes=>{let binary='';for(let n=0;n<bytes.length;n+=32768)binary+=String.fromCharCode(...bytes.subarray(n,n+32768));return btoa(binary);};
  async function rememberFile(file){if(!enabled||!pair.include_files||file.size>50*1024*1024)return;localFiles.set(file.name,{id:'upload:'+file.name,filename:file.name,data:bytesToBase64(new Uint8Array(await file.arrayBuffer()))});}
  const fileChange=e=>{if(enabled&&e.target.matches?.('input[type=file]'))for(const file of e.target.files||[])rememberFile(file);};document.addEventListener('change',fileChange,true);
  function hidden(node){for(let p=node;p;p=parentOf(p)){const style=styleOf(p);if(style.display==='none'||style.visibility==='hidden'||p.hidden||p.getAttribute('aria-hidden')==='true')return true;}return false;}
  function bodyText(node){
    const parts=[],walker=node.ownerDocument.createTreeWalker(node,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){
      const text=walker.currentNode,parent=text.parentElement;
      if(!parent||ancestor(parent,'button,[role=button],[role=toolbar],script,style,textarea,nav,aside,[hidden],[aria-hidden=true],[contenteditable=true],.turn-action-controls')||hidden(parent))continue;
      const heading=ancestor(parent,'h1,h2,h3,h4,h5,h6');
      if(heading&&/^(you said|chatgpt said|你说|您说|ChatGPT.*说)[:：]?$/i.test(heading.textContent.trim()))continue;
      if(parent.tagName==='BR')parts.push('\n');
      parts.push(text.textContent);
      if(/^(P|DIV|LI|PRE|TR|H[1-6])$/.test(parent.tagName))parts.push('\n');
    }
    return parts.join('').replace(/\n{3,}/g,'\n\n');
  }
  function collect(){styleCache=new Map();try{collectRaw();}finally{styleCache=null;}}
  function collectRaw(){
    if(!enabled)return;
    if(info().url!==startingUrl){stop();lastError='页面已切换，同步已停止。新对话需要单独授权。';return;}
    // Project pages may have multiple main containers or portal-rendered turns.
    const selectors=pair.provider==='gpt'?GPT_SELECTORS:pair.provider==='deepseek-harness'?'[data-chat-anchor-key]':'[data-message-author-role],[data-role="user"],[data-role="assistant"],[data-testid="user-message"],[data-testid="assistant-message"],.ds-markdown,[class*="user-message"]';
    const visited=new Set(),seats=new Set(),visible=[];lastScan={candidates:0,hidden:0,unknown_role:0,empty:0,recognized:0};
    for(const candidate of queryAll(selectors).sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top)){
      lastScan.candidates++;
      const node=unitOf(candidate);if(!node)continue;
      // A newly opened background tab may have no layout rectangles yet.
      if(visited.has(node))continue;
      if(hidden(node)||excluded(node)){lastScan.hidden++;continue;}
      const seat=node;
      if(seats.has(seat))continue;
      const role=roleOf(node)||roleOf(candidate);
      if(!['user','assistant','tool'].includes(role)){lastScan.unknown_role++;continue;}
      const bodySelector=MODERN_BODY+',.markdown,.whitespace-pre-wrap,[data-testid="message-content"]';
      const bodies=node.matches(bodySelector)?[node]:[...node.querySelectorAll(bodySelector)].filter(n=>!n.parentElement?.closest(bodySelector)&&(!roleOf(n)||roleOf(n)===role));
      const text=(bodies.length?bodies.map(bodyText).join('\n'):bodyText(node)).trim();
      const modern=ancestor(node,'[data-content-search-unit-key],[data-turn-key],[data-content-search-turn-key]');
      const modernKey=modern?.dataset.contentSearchUnitKey||modern?.dataset.turnKey||modern?.dataset.contentSearchTurnKey;
      const testid=seat.dataset.testid;
      const stableId=node.dataset.messageId||node.dataset.chatAnchorKey||ancestor(node,'[data-message-id]')?.dataset.messageId||seat.dataset.turnId||(modernKey?role+':turn:'+modernKey:null)||(/^conversation-turn-\d+$/.test(testid||'')?testid:null);
      // Never assign a DOM position to an ID: recycled/virtualized nodes can represent another message.
      const id=stableId||role+':snapshot:'+fingerprint(text);
      const attachments=[];
      const fileContainer=ancestor(node,'[data-chatgpt-agent-turn-start],'+MODERN_TURN+',[data-testid^="conversation-turn"],[data-turn],article')||userBoundary(node)||seat;
      for(const link of fileContainer.querySelectorAll('a[download],a[href*="attachment"],a[href*="file-"]')){
        const name=link.download||link.textContent.trim()||'attachment';const file=localFiles.get(name);const url=new URL(link.href,location.href);
        const id='link:'+fingerprint(url.origin+url.pathname);
        attachments.push(file||{id,filename:name,reference:url.href.split('?')[0],_url:url.href});
      }
      if(role==='user')for(const [name,file] of localFiles)if(text.includes(name)&&!attachments.some(x=>x.id===file.id))attachments.push(file);
      if(!text&&!attachments.length){lastScan.empty++;continue;}
      visited.add(node);seats.add(seat);lastScan.recognized++;
      messages.set(id,{id,role,text,attachments});
      visible.push(id);
    }
    // Merge overlapping virtualized windows without treating discovery order as chat order.
    const top=scrollPosition(root());
    for(let n=0;n<visible.length;n++){
      const id=visible[n];if(messageOrder.includes(id))continue;
      const next=visible.slice(n+1).find(x=>messageOrder.includes(x));
      const previous=visible.slice(0,n).reverse().find(x=>messageOrder.includes(x));
      const anchor=previous||[...previousVisible].reverse().find(x=>messageOrder.includes(x));
      const index=next?messageOrder.indexOf(next):previous?messageOrder.indexOf(previous)+1:top===0||top<previousTop?0:anchor?messageOrder.indexOf(anchor)+1:messageOrder.length;
      messageOrder.splice(index,0,id);
    }
    if(visible.length){previousVisible=visible;previousTop=top;}
  }
  async function flush(){
    if(flushing||waiting||(fullScanRequested&&['to-top','reading','incomplete'].includes(scanState.phase)))return;collect();if(!enabled)return;flushing=true;
    const run=epoch,activePair=pair;
    try{
    // Only file links already rendered inside this authorized conversation are eligible.
    for(const message of messages.values())for(const file of message.attachments){
      const source=file._url;if(!source||!activePair.include_files)continue;
      const url=new URL(source);if(url.protocol!=='blob:'&&url.origin!==location.origin)continue;
      if(!attachmentBytes.has(source))attachmentBytes.set(source,(async()=>{
        try{const response=await fetch(source,{credentials:'same-origin',signal:AbortSignal.timeout(10000)});if(!response.ok||Number(response.headers.get('content-length')||0)>50*1024*1024)return null;
          const reader=response.body.getReader(),chunks=[];let size=0;
          while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>50*1024*1024){await reader.cancel();return null;}chunks.push(value);}
          const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return bytesToBase64(bytes);
        }catch{return null;}
      })());
      const data=await attachmentBytes.get(source);if(run!==epoch||!enabled)return;if(data!==null)file.data=data;
    }
    const clean=message=>({...message,attachments:message.attachments.map(({_url,...file})=>file)});
    const changed=messageOrder.map(id=>clean(messages.get(id))).filter(x=>JSON.stringify(x)!==sent.get(x.id));
    const orderSignature=JSON.stringify(messageOrder);
    if(!changed.length&&orderSignature!==sentOrder&&messageOrder.length)changed.push(clean(messages.get(messageOrder[0])));
    for(let n=0;n<changed.length;n+=100){
      if(!enabled||run!==epoch)return;
      const batch=changed.slice(n,n+100),result=await chrome.runtime.sendMessage({type:'capture-snapshot',endpoint:activePair.endpoint,packet:{provider:activePair.provider,conversation_id:activePair.conversation_id,conversation_title:info().title.slice(0,180),messages:batch,message_order:messageOrder}});
      if(run!==epoch)return;
      if(!result?.ok){lastError=result?.error||'无法连接 ShirOS';lastErrorCode=result?.code||'capture.network_unavailable';if(lastError.includes('not_authorized'))stop();return;}
      batch.forEach(x=>sent.set(x.id,JSON.stringify(x)));lastError='';lastErrorCode='';lastSync=new Date().toISOString();
      excludedMessages=result.excluded_messages||0;
      pendingReview=!!result.pending;privacyOmissions=result.privacy_omitted_messages||0;privacyTitleOmitted=!!result.privacy_omitted_title;
    }
    sentOrder=orderSignature;
    if(!messages.size)lastError='尚未识别消息，请确认已打开对话。该客户端的页面结构可能需要适配。';
    }finally{flushing=false;}
  }
  async function scrollAll(){
    if(!enabled||scrolling)return;scrolling=true;scrollStop=false;rootCache=null;scrollAxes=new WeakMap();const run=epoch;
    scanState={phase:'to-top',topReached:false,bottomReached:false,truncated:false,steps:0};
    const styles=new Map();
    function move(pane,position){
      if(!styles.has(pane)){styles.set(pane,{behavior:pane.style.scrollBehavior,snap:pane.style.scrollSnapType});pane.style.scrollBehavior='auto';pane.style.scrollSnapType='none';}
      const reversed=scrollAxis(pane,true),extent=Math.max(0,pane.scrollHeight-pane.clientHeight),value=Math.max(0,Math.min(extent,position))-(reversed?extent:0);
      if(pane.scrollTop===value&&position===0&&extent>0)pane.scrollTop=value+1;
      pane.scrollTop=value;
      pane.dispatchEvent(new (pane.ownerDocument.defaultView||window).Event('scroll'));
    }
    const active=()=>!scrollStop&&enabled&&run===epoch;
    const signature=pane=>pane.scrollHeight+':'+messageOrder.join('|');
    const busy=pane=>queryAll('[aria-busy=true],[role=progressbar]',[pane]).some(n=>!hidden(n))||queryAll('button').some(n=>!hidden(n)&&/^(Stop generating|停止生成|停止回答)$/i.test((n.getAttribute('aria-label')||n.textContent||'').trim()));
    try{
      let stable=0,previous='';
      for(let n=0;n<160&&active();n++){
        const pane=root();move(pane,0);await wait(400);collect();scanState.steps++;
        const now=signature(pane);
        stable=scrollPosition(pane)<=1&&now===previous&&!busy(pane)?stable+1:0;previous=now;
        if(stable>=8){scanState.topReached=true;break;}
      }
      if(!active())return;
      if(!scanState.topReached){scanState.truncated=true;throw Object.assign(new Error('未能到达对话顶部，本次未标记为完整。'),{code:'capture.top_incomplete'});}
      scanState.phase='reading';stable=0;previous='';let stalled=0;
      for(let n=0;n<3000&&active();n++){
        collect();await flush();const pane=root();
        const before=pane.scrollTop;move(pane,scrollPosition(pane)+Math.max(80,pane.clientHeight*.55));
        scanState.attempted=Math.round(pane.scrollTop);scanState.pane=scrollDetails();
        await wait(400);collect();scanState.steps++;
        stalled=pane.scrollTop===before&&scrollPosition(pane)<pane.scrollHeight-pane.clientHeight-3?stalled+1:0;
        if(stalled>=8)throw Object.assign(new Error('对话滚动区域没有移动，本次已停止。请复制识别诊断。'),{code:'capture.scroll_blocked'});
        const now=signature(pane);
        stable=scrollPosition(pane)>=pane.scrollHeight-pane.clientHeight-3&&now===previous&&!busy(pane)?stable+1:0;previous=now;
        if(stable>=8){scanState.bottomReached=true;break;}
      }
      if(!active())return;
      if(!scanState.bottomReached){scanState.truncated=true;throw Object.assign(new Error('对话历史未读完，本次未标记为完整。'),{code:'capture.history_incomplete'});}
      const counts=roleCounts();
      if(counts.user&&!counts.assistant){throw Object.assign(new Error('仅识别到提问，未识别到回答。本次未标记为完整，请复制识别诊断。'),{code:'capture.answers_missing'});}
      scanState.phase='saving';await flush();
      scanState.phase='complete';
    }catch(error){if(run===epoch){scanState.phase='incomplete';lastError=error.message;lastErrorCode=error.code||'capture.page_error';}}
    finally{for(const [pane,style] of styles){pane.style.scrollBehavior=style.behavior;pane.style.scrollSnapType=style.snap;}if(run===epoch)scrolling=false;}
  }
  function stop(){epoch++;enabled=false;waiting=false;scrolling=false;scrollStop=true;clearInterval(timer);timer=null;}
  async function discover(activePair){
    if(!activePair?.all_conversations)throw new Error('请先在 ShirOS 授权此平台的批量采集。');
    discoveryStop=false;
    const links=new Map(),projects=new Map(),directories=new Map(),initial=location.origin;
    const diagnostics={passes:[],panes:0,more_clicks:0,truncated:false,stopped:false,project_filter_changed:false};
    const nav='nav,aside,[role=complementary],[data-testid*="history"],[role=dialog]';
    const navigation='a[href]';
    const directory='a[href]';
    const excluded='[data-message-author-role],[data-testid^="conversation-turn"],[data-turn],textarea,[contenteditable=true],[data-chatgpt-composer],'+MODERN_USER+','+MODERN_BODY+','+MODERN_TURN;
    const positions=new Map(),paneStyles=new Map();
    const directoryLink=a=>{try{const u=new URL(a.href,location.href);return u.origin===initial&&/^\/(?:c\/|g\/g-p-|projects\/?$|history\/?$|chats\/?$)/.test(u.pathname);}catch{return false;}};
    if(!queryAll(navigation).some(a=>!hidden(a)&&directoryLink(a))){
      const open=queryAll('button,[role=button]').find(n=>!hidden(n)&&(n.dataset.testid==='open-sidebar-button'||/(?:open|expand|打开|展开|显示).*(?:sidebar|侧栏|侧边栏)/i.test(n.getAttribute('aria-label')||n.getAttribute('title')||'')));
      if(open){open.click();await wait(350);}
      for(let n=0;n<20&&!discoveryStop&&!queryAll(navigation).some(a=>!hidden(a)&&directoryLink(a));n++)await wait(250);
    }
    function scan(){for(const a of queryAll(directory)){
      if(a.closest(excluded)||hidden(a))continue;let u;try{u=new URL(a.href,location.href);}catch{continue;}if(u.origin!==initial||u.search||u.hash)continue;
      const id=u.pathname.match(/\/c\/([a-z0-9-]+)\/?$/i)?.[1];
      if(id){const entry={url:u.origin+u.pathname,title:(a.getAttribute('title')||a.innerText||'').trim()};if(!links.has(id)||u.pathname.includes('/g/'))links.set(id,entry);}
      else if(/^\/g\/g-p-[^/]+(?:\/project)?\/?$/.test(u.pathname))projects.set(u.pathname.replace(/\/project\/?$/,''),{url:u.origin+u.pathname});
      else if(/^\/(?:projects|history|chats)\/?$/.test(u.pathname)&&u.pathname!==location.pathname)directories.set(u.pathname,{url:u.origin+u.pathname});
    }}
    function panes(){const result=new Set();
      for(const node of [...queryAll(directory),...queryAll(nav)]){
        if(hidden(node)||node.closest(excluded))continue;
        for(let pane=node;pane;pane=parentOf(pane)){
          if(pane===document.body||pane===document.documentElement)break;
          if(/auto|scroll/.test(styleOf(pane).overflowY)&&pane.scrollHeight>pane.clientHeight+2)result.add(pane);
        }
      }
      if(!info().is_conversation&&document.scrollingElement?.scrollHeight>innerHeight+2)result.add(document.scrollingElement);
      for(const pane of result)if(!positions.has(pane)){positions.set(pane,pane.scrollTop);paneStyles.set(pane,{behavior:pane.style.scrollBehavior,snap:pane.style.scrollSnapType});pane.style.scrollBehavior='auto';pane.style.scrollSnapType='none';}
      diagnostics.panes=Math.max(diagnostics.panes,result.size);return [...result];
    }
    async function drain(source){
      let stable=0,complete=false;const clicked=new Set();scan();
      for(const pane of panes()){const reversed=scrollAxis(pane,true);pane.scrollTop=reversed?-(pane.scrollHeight-pane.clientHeight):0;}
      await wait(400);scan();
      for(let n=0;n<240&&!discoveryStop;n++){
        const before=links.size+projects.size+directories.size,list=panes();
        const signature=list.map(p=>p.scrollHeight+':'+p.scrollTop).join('|');
        let more=false;
        const buttons=queryAll('button,[role=button]').filter(b=>!hidden(b)&&!ancestor(b,excluded)&&/^(?:查看更多|查看全部|更多对话|更多项目|加载更多|显示更多|Show more|See all|View all|Load more|More chats|More projects)$/i.test((b.textContent||b.getAttribute('aria-label')||'').trim()));
        for(const button of buttons){if(clicked.has(button))continue;clicked.add(button);button.click();diagnostics.more_clicks++;more=true;}
        for(const pane of list){const extent=Math.max(0,pane.scrollHeight-pane.clientHeight);pane.scrollTop=Math.min(extent,scrollPosition(pane)+Math.max(120,pane.clientHeight*.8))-(scrollAxis(pane,true)?extent:0);}
        await wait(400);scan();const next=panes();
        const busy=queryAll(nav).some(region=>!hidden(region)&&queryAll('[aria-busy=true],[role=progressbar]',[region]).some(node=>!hidden(node)));
        const atBottom=next.every(p=>scrollPosition(p)>=p.scrollHeight-p.clientHeight-3);
        const unchanged=signature===next.map(p=>p.scrollHeight+':'+p.scrollTop).join('|')&&before===links.size+projects.size+directories.size;
        stable=!more&&!busy&&atBottom&&unchanged?stable+1:0;
        if(stable>=8){complete=true;break;}
      }
      if(!complete&&!discoveryStop)diagnostics.truncated=true;
      diagnostics.passes.push({source,conversations:links.size,projects:projects.size,directories:directories.size,settled:complete});
    }
    const recent=()=>queryAll('button,[role=button]').find(n=>/^(最近|Recent|Recents)$/i.test(n.textContent.trim())&&!hidden(n)&&!ancestor(n,excluded));
    async function projectFilter(value){
      const button=recent();if(!button)return false;
      button.click();await wait(250);
      const filter=queryAll('[role=menu] input[type=checkbox],[role=menu] [role=menuitemcheckbox],[role=menu] [role=checkbox],[role=dialog] input[type=checkbox],[data-dropdown-overlay-side] [role=checkbox],[data-radix-popper-content-wrapper] [role=checkbox],[data-radix-popper-content-wrapper] input[type=checkbox]').find(n=>!hidden(n)&&/^(项目|Projects?)$/i.test((n.getAttribute('aria-label')||n.closest('label')?.textContent||n.textContent||'').trim()));
      if(filter){const checked=filter.checked===true||filter.getAttribute('aria-checked')==='true'||filter.getAttribute('data-state')==='checked';if(checked!==value){filter.click();diagnostics.project_filter_changed=true;await wait(600);}}
      if(queryAll('[role=menu],[data-radix-popper-content-wrapper]').some(n=>!hidden(n))){button.click();await wait(200);}return !!filter;
    }
    try{
      await drain('current');
      if(!discoveryStop&&await projectFilter(true))await drain('projects-included');
      if(!discoveryStop&&await projectFilter(false))await drain('projects-excluded');
      // Search is an observed UI directory only; no account API or credentials are read.
      const existingDialog=queryAll('[role=dialog]').some(n=>!hidden(n));
      const search=!existingDialog&&queryAll('button,[role=button]').find(n=>!hidden(n)&&!ancestor(n,excluded)&&/^(搜索聊天|搜索对话|Search chats|Search conversations)$/i.test((n.getAttribute('aria-label')||n.textContent||'').trim()));
      if(!discoveryStop&&search){search.click();await wait(500);try{await drain('search-directory');}finally{const dialog=queryAll('[role=dialog]').find(n=>!hidden(n));const close=dialog&&queryAll('button',[dialog]).find(n=>/^(关闭|Close)$/i.test((n.getAttribute('aria-label')||n.textContent||'').trim()));if(close)close.click();}}
    }finally{for(const [pane,previous] of positions)if(pane.isConnected)pane.scrollTop=previous;for(const [pane,style] of paneStyles){pane.style.scrollBehavior=style.behavior;pane.style.scrollSnapType=style.snap;}}
    diagnostics.stopped=discoveryStop;return {links:[...links.values()],projects:[...projects.values()],directories:[...directories.values()],project_filter_changed:diagnostics.project_filter_changed,diagnostics};
  }
  const listener=(message,_sender,reply)=>{
    if(message.type==='capture-info'){reply({...info(),enabled,scrolling,waiting,count:messages.size,submitted:sent.size,excludedMessages,pendingReview,privacyOmissions,privacyTitleOmitted,lastError,lastErrorCode,lastSync,roles:roleCounts(),scan:scanState});return;}
    if(message.type==='capture-stop'){stop();reply({ok:true});return;}
    if(message.type==='capture-diagnostics'){reply(diagnostics());return;}
    if(message.type==='capture-discover-stop'){discoveryStop=true;reply({ok:true});return;}
    if(message.type==='capture-finish'){(async()=>{while(flushing)await wait(100);await flush();const result={ok:!lastError&&sent.size===messages.size&&(!fullScanRequested||scanState.phase==='complete'),count:messages.size,pendingReview,privacyOmissions,privacyTitleOmitted,error:lastError,code:lastErrorCode};stop();return result;})().then(reply).catch(e=>reply({ok:false,error:e.message,code:e.code}));return true;}
    if(message.type==='capture-discover'){discover(message.pair).then(result=>reply({ok:true,...result})).catch(e=>reply({ok:false,error:e.message}));return true;}
    if(message.type==='capture-scroll'){scrollAll().then(()=>reply({ok:true,count:messages.size,error:lastError}));return true;}
    if(message.type==='capture-start'){
      if(!message.pair||typeof message.pair!=='object'||!message.pair.endpoint||!message.pair.provider||!message.pair.conversation_id){reply({ok:false,error:'配对信息未加载，请先保存 ShirOS 设置页生成的配对信息。',code:'capture.pairing_missing'});return;}
      if(!info().is_conversation){reply({ok:false,error:'首页请使用“批量导入对话”，无需打开单个对话。'});return;}
      const current=info();if(current.conversation_id!==message.pair.conversation_id&&!message.pair.all_conversations){reply({ok:false,error:'当前对话与授权不匹配。'});return;}
      stop();const run=epoch;waiting=true;lastError='';lastErrorCode='';
      (async()=>{const authorized=await chrome.runtime.sendMessage({type:'capture-check',endpoint:message.pair.endpoint,provider:message.pair.provider,conversation_id:current.conversation_id});if(run!==epoch)return {ok:false,error:'已停止同步。'};if(!authorized?.ok)throw new Error(authorized?.error||'授权检查失败。');
        pair={...message.pair,conversation_id:current.conversation_id};startingUrl=current.url;rootCache=null;messages.clear();sent.clear();localFiles.clear();attachmentBytes.clear();messageOrder=[];previousVisible=[];previousTop=Infinity;sentOrder='';excludedMessages=0;pendingReview=false;privacyOmissions=0;privacyTitleOmitted=false;enabled=true;lastSync=null;fullScanRequested=!!message.autoScroll;scanState={phase:'waiting',topReached:false,bottomReached:false,truncated:false,steps:0};
        timer=setInterval(()=>flush().catch(e=>lastError=e.message),2500);
        (async()=>{for(let n=0;n<80&&enabled&&run===epoch;n++){collect();if(messages.size)break;await wait(250);}if(run!==epoch||!enabled)return;waiting=false;await(message.autoScroll?scrollAll():flush());})().catch(e=>{if(run===epoch){waiting=false;lastError=e.message;lastErrorCode=e.code||'capture.page_error';}});
        return {ok:true};})().then(reply).catch(e=>{if(run===epoch){waiting=false;lastError=e.message;lastErrorCode=e.code||'capture.page_error';}reply({ok:false,error:e.message});});return true;
    }
  };chrome.runtime.onMessage.addListener(listener);
  window.shirosCaptureController={version:VERSION,dispose:()=>{stop();discoveryStop=true;document.removeEventListener('change',fileChange,true);chrome.runtime.onMessage.removeListener(listener);messages.clear();sent.clear();attachmentBytes.clear();localFiles.clear();}};
})();
