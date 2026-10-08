/* Local appearance preference; no user-data transmission. */
(() => {
  'use strict';
  const themes=[['ivory','羊皮纸','暖白与酒红'],['forest','苔庭','鼠尾草与墨绿'],['lapis','青金','雾蓝与深海'],['plum','暮紫','淡紫与梅色'],['graphite','石墨','深灰与银白']];
  let active='ivory';try{const saved=localStorage.getItem('shiros-theme');if(themes.some(t=>t[0]===saved))active=saved;}catch(e){}
  document.documentElement.dataset.theme=active;
  document.addEventListener('DOMContentLoaded',()=>{
    const trigger=document.getElementById('theme-open');
    trigger.addEventListener('click',()=>{
      const dialog=document.createElement('dialog');dialog.className='theme-dialog';
      const title=document.createElement('h2');title.textContent='选择一种色彩';dialog.append(title);
      const sub=document.createElement('p');sub.textContent='让收藏拥有自己的气质';dialog.append(sub);
      const choices=document.createElement('div');choices.className='theme-choices';choices.setAttribute('role','radiogroup');choices.setAttribute('aria-label','颜色主题');dialog.append(choices);
      for(const [id,name,description] of themes){
        const button=document.createElement('button');button.type='button';button.dataset.palette=id;button.setAttribute('role','radio');button.setAttribute('aria-checked',String(active===id));
        const swatch=document.createElement('span');swatch.className='theme-swatch';const label=document.createElement('strong');label.textContent=name;const desc=document.createElement('small');desc.textContent=description;button.append(swatch,label,desc);
        button.addEventListener('click',()=>{active=id;document.documentElement.dataset.theme=id;try{localStorage.setItem('shiros-theme',id);}catch(e){}for(const b of choices.children)b.setAttribute('aria-checked',String(b===button));});choices.append(button);
      }
      const done=document.createElement('button');done.type='button';done.className='primary-button';done.textContent='完成';done.addEventListener('click',()=>dialog.close());dialog.append(done);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
    });
  });
})();
