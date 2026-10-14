/* Local appearance preference; no user-data transmission. */
(() => {
  const ui = window.ShirosI18n.message;
  'use strict';
  const themes=[['white',ui("白色"),ui("纯白 / 蓝色")],['ivory',ui("暖白"),ui("暖白 / 酒红")],['forest',ui("绿色"),ui("浅绿 / 深绿")],['lapis',ui("蓝色"),ui("浅蓝 / 深蓝")],['plum',ui("紫色"),ui("浅紫 / 深紫")],['graphite',ui("深色"),ui("深灰 / 金色")]];
  let active='ivory';try{const saved=localStorage.getItem('shiros-theme');if(themes.some(t=>t[0]===saved))active=saved;}catch(e){}
  const apply=id=>{active=id;document.documentElement.dataset.theme=id;try{localStorage.setItem('shiros-theme',id);}catch(e){}};
  document.documentElement.dataset.theme=active;
  let changed=0,writes=Promise.resolve();
  async function preference(value={}){
    const response=await fetch('/ui-api/preferences/theme',{method:'POST',headers:{'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token')||''},body:JSON.stringify(value)});
    if(!response.ok)throw new Error(ui("主题偏好未保存，请重试。"));
    return response.json();
  }

  document.addEventListener('DOMContentLoaded',()=>{
    const trigger=document.getElementById('desktop-settings-open');
    preference().then(saved=>{if(changed)return;if(saved.saved)apply(saved.theme);else if(active!=='ivory')writes=preference({theme:active});}).catch(()=>{window.ShirosI18n.attribute(trigger, 'title', ui("主题偏好读取失败，当前只使用本机缓存"));});
    function renderChoices(container, sub){
      const choices=document.createElement('div');choices.className='theme-choices';choices.setAttribute('role','radiogroup');window.ShirosI18n.attribute(choices, 'aria-label', ui("颜色主题"));container.append(choices);
      for(const [id,name,description] of themes){
        const button=document.createElement('button');button.type='button';button.dataset.palette=id;button.setAttribute('role','radio');button.setAttribute('aria-checked',String(active===id));
        const swatch=document.createElement('span');swatch.className='theme-swatch';const label=document.createElement('strong');window.ShirosI18n.text(label, name);const desc=document.createElement('small');window.ShirosI18n.text(desc, description);button.append(swatch,label,desc);
        button.addEventListener('click',()=>{changed++;apply(id);window.ShirosI18n.text(sub, ui("正在保存主题…"));writes=writes.catch(()=>{}).then(()=>preference({theme:id}));writes.then(()=>{if(active===id)window.ShirosI18n.text(sub, ui("主题已保存"));},error=>{window.ShirosI18n.text(sub, error.message);});for(const b of document.querySelectorAll('.theme-choices [data-palette]'))b.setAttribute('aria-checked',String(b.dataset.palette===id));});choices.append(button);
      }
    }
    document.addEventListener('shiros-theme-render',event=>renderChoices(event.detail.container,event.detail.status));

  });
})();
