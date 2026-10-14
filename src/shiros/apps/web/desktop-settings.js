/* Desktop-only controls use the window bridge, never a browser HTTP endpoint. */
(() => {
  const ui = window.ShirosI18n.message;
  'use strict';
  const el=(tag,text='',cls='')=>{const node=document.createElement(tag);window.ShirosI18n.text(node, text);node.className=cls;return node;};
  const trigger=document.getElementById('desktop-settings-open');
  async function open(){
    const existing=document.querySelector('.desktop-settings-dialog');if(existing){existing.focus();return;}
    const dialog=el('dialog','','desktop-settings-dialog'),header=el('header','','desktop-settings-header'),title=el('h2',ui("设置"));
    title.id='desktop-settings-title';dialog.setAttribute('aria-labelledby',title.id);
    const close=el('button',ui("关闭"),'secondary-button');close.onclick=()=>dialog.close();header.append(title,close);dialog.append(header);
    const content=el('div','','desktop-settings-content'),status=el('p','','desktop-settings-status');status.setAttribute('role','status');
    const appearance=el('section','','desktop-settings-section');appearance.append(el('h3',ui("颜色主题")),el('p',ui("选择后自动保存，重新打开时沿用。"),'desktop-settings-help'));
    const themeStatus=el('p','','desktop-settings-status');themeStatus.setAttribute('role','status');
    document.dispatchEvent(new CustomEvent('shiros-theme-render',{detail:{container:appearance,status:themeStatus}}));appearance.append(themeStatus);content.append(appearance);
    const language=el('section','','desktop-settings-section');language.append(el('h3',ui("界面语言")));
    const select=el('select','','settings-language');window.ShirosI18n.attribute(select, 'aria-label', ui("界面语言"));
    for(const [value,label] of [['zh',ui("简体中文")],['en','English']]){const option=el('option',label);option.value=value;select.append(option);}
    select.value=document.documentElement.lang==='en'?'en':'zh';const syncLocale=()=>{select.value=window.ShirosI18n.language;};document.addEventListener('shiros-locale-changed',syncLocale);dialog.addEventListener('close',()=>document.removeEventListener('shiros-locale-changed',syncLocale),{once:true});select.onchange=()=>document.dispatchEvent(new CustomEvent('shiros-language-select',{detail:{language:select.value}}));language.append(select);content.append(language);
    document.dispatchEvent(new CustomEvent('shiros-capture-settings',{detail:{container:content}}));
    const behavior=el('section','','desktop-settings-section');behavior.append(el('h3',ui("窗口与后台")));
    behavior.hidden=!window.pywebview?.api;
    const row=el('div','','desktop-settings-row'),copy=el('div');copy.append(el('strong',ui("关闭窗口后在后台运行")),el('p',ui("开启：关闭窗口后保留托盘。关闭：退出桌面程序。"),'desktop-settings-help'));
    const toggle=el('button','','desktop-settings-switch');toggle.type='button';toggle.setAttribute('role','switch');window.ShirosI18n.attribute(toggle, 'aria-label', ui("关闭窗口后在后台运行"));toggle.setAttribute('aria-checked','false');toggle.disabled=true;
    const fullRow=el('div','','desktop-settings-row'),fullCopy=el('div');fullCopy.append(el('strong',ui("全屏显示")),el('p',ui("隐藏系统标题栏。F11 可切换全屏。"),'desktop-settings-help'));
    const fullToggle=el('button','','desktop-settings-switch');fullToggle.classList.add('settings-fullscreen');fullToggle.type='button';fullToggle.setAttribute('role','switch');window.ShirosI18n.attribute(fullToggle, 'aria-label', ui("全屏显示"));fullToggle.setAttribute('aria-checked','false');fullToggle.disabled=true;
    function update(value){toggle.setAttribute('aria-checked',String(value.background_mode));toggle.disabled=!value.background_supported;fullToggle.setAttribute('aria-checked',String(value.fullscreen));fullToggle.disabled=false;if(!value.background_supported)window.ShirosI18n.text(status, ui("此桌面版本暂不支持后台模式。"));}
    toggle.onclick=async()=>{const enabled=toggle.getAttribute('aria-checked')!=='true';toggle.disabled=true;try{update(await window.pywebview.api.set_background_mode(enabled));window.ShirosI18n.text(status, enabled?ui("后台模式已开启"):ui("后台模式已关闭"));}catch(error){window.ShirosI18n.text(status, ui("设置未保存，请重试。"));toggle.disabled=false;}};
    fullToggle.onclick=async()=>{fullToggle.disabled=true;try{update(await window.pywebview.api.set_fullscreen(fullToggle.getAttribute('aria-checked')!=='true'));window.ShirosI18n.text(status, ui("显示设置已保存"));}catch(error){window.ShirosI18n.text(status, ui("显示设置未保存，请重试。"));fullToggle.disabled=false;}};
    row.append(copy,toggle);fullRow.append(fullCopy,fullToggle);behavior.append(fullRow,row,el('p',ui("后台模式控制桌面窗口与系统托盘。"),'desktop-settings-note'));content.append(behavior,status);
    const footer=el('footer','','desktop-settings-footer'),quit=el('button',ui("退出桌面程序"),'secondary-button'),done=el('button',ui("完成"),'primary-button');
    quit.hidden=!window.pywebview?.api;
    quit.onclick=async()=>{if(!confirm(ui("退出 ShirOS 桌面程序？")))return;quit.disabled=true;try{await window.pywebview.api.quit_desktop();}catch(error){window.ShirosI18n.text(status, ui("退出未完成，请重试。"));quit.disabled=false;}};done.onclick=()=>dialog.close();footer.append(quit,done);dialog.append(content,footer);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
    if(window.pywebview?.api){try{update(await window.pywebview.api.get_settings());}catch(error){window.ShirosI18n.text(status, ui("桌面设置读取失败，请重新打开。"));}}
  }
  const minimize=document.getElementById('desktop-window-minimize'),maximize=document.getElementById('desktop-window-maximize'),close=document.getElementById('desktop-window-close');
  function enable(){minimize.hidden=maximize.hidden=close.hidden=!window.pywebview?.api;}
  minimize.onclick=()=>window.pywebview.api.minimize_window();maximize.onclick=async()=>{const saved=await window.pywebview.api.maximize_window();const toggle=document.querySelector('.desktop-settings-dialog .settings-fullscreen');if(toggle)toggle.setAttribute('aria-checked',String(saved.fullscreen));};close.onclick=()=>window.pywebview.api.close_window();
  const topbar=document.querySelector('.topbar');
  topbar.addEventListener('mousedown',event=>{if(event.button!==0||event.target.closest('button,input,select,a')||!window.pywebview?.api)return;const x=event.screenX,y=event.screenY;const stop=()=>{window.removeEventListener('mousemove',move);window.removeEventListener('mouseup',stop);};const move=e=>{if(!(e.buttons&1)){stop();return;}if(Math.hypot(e.screenX-x,e.screenY-y)<6)return;stop();window.pywebview.api.drag_window();};window.addEventListener('mousemove',move);window.addEventListener('mouseup',stop);});
  topbar.addEventListener('dblclick',event=>{if(event.target.closest('button,input,select,a')||!window.pywebview?.api)return;window.pywebview.api.maximize_window();});
  document.addEventListener('keydown',async event=>{if(event.key!=='F11'||!window.pywebview?.api||event.repeat)return;event.preventDefault();try{const value=await window.pywebview.api.get_settings();const saved=await window.pywebview.api.set_fullscreen(!value.fullscreen);const toggle=document.querySelector('.desktop-settings-dialog .settings-fullscreen');if(toggle)toggle.setAttribute('aria-checked',String(saved.fullscreen));}catch(error){}});
  window.addEventListener('pywebviewready',enable);enable();
  const edges=['top','right','bottom','left','top-left','top-right','bottom-left','bottom-right'];
  for(const edge of edges){const handle=el('div','','window-resize-handle resize-'+edge);handle.dataset.resize=edge;handle.setAttribute('aria-hidden','true');handle.hidden=true;handle.addEventListener('pointerdown',event=>{if(event.button!==0||!window.pywebview?.api)return;event.preventDefault();event.stopPropagation();window.pywebview.api.resize_window(edge);});document.body.append(handle);}
  async function refreshResize(){if(!window.pywebview?.api)return;try{const settings=await window.pywebview.api.get_settings();document.querySelectorAll('.window-resize-handle').forEach(handle=>handle.hidden=!settings.resizable);}catch(error){}}
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(refreshResize,120);});window.addEventListener('pywebviewready',refreshResize);refreshResize();
  async function syncNativeLanguage(){if(!window.pywebview?.api?.set_interface_language)return;try{await window.pywebview.api.set_interface_language(window.ShirosI18n.language);}catch(error){window.ShirosFeedback.show(ui('桌面语言未同步，请重试。'),document.querySelector('.desktop-settings-dialog')||document.body);}}
  document.addEventListener('shiros-locale-changed',syncNativeLanguage);window.addEventListener('pywebviewready',syncNativeLanguage);syncNativeLanguage();
  trigger.onclick=open;document.addEventListener('shiros-desktop-settings',open);
})();
