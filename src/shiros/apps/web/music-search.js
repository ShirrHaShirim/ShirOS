/* One scoped text search, with optional person / organization association filters. */
(() => {
  const ui = window.ShirosI18n.message;
  'use strict';
  const make=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)window.ShirosI18n.text(node, text);return node;};
  function clearButton(input,label){
    const button=make('button','music-search-clear');button.type='button';window.ShirosI18n.attribute(button, 'aria-label', label);
    const icon=make('span',null,'×');icon.setAttribute('aria-hidden','true');button.append(icon);input.parentElement.append(button);
    input.parentElement.classList.add('music-clearable-search');
    const sync=()=>button.hidden=!input.value;input.addEventListener('input',sync);
    button.addEventListener('click',()=>{input.value='';input.focus();input.dispatchEvent(new Event('input',{bubbles:true}));});sync();return sync;
  }
  function attach({input,filter,api,context,invalidate,changed,onError}){
    const box=input.parentElement,wrap=make('div','music-search-wrap');box.before(wrap);wrap.append(box);
    const menu=make('div','music-search-suggestions');menu.id='music-search-suggestions';menu.setAttribute('role','listbox');window.ShirosI18n.attribute(menu, 'aria-label', ui("筛选关联人物或组织"));menu.hidden=true;wrap.append(menu);
    const chip=make('div','music-search-profile');chip.hidden=true;wrap.append(chip);
    window.ShirosI18n.attribute(input, 'aria-label', ui("搜索音乐库"));input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-controls',menu.id);input.setAttribute('aria-expanded','false');input.autocomplete='off';
    const syncClear=clearButton(input,ui("清除音乐搜索"));
    let timer=null,controller=null,generation=0,active=-1,rows=[],composing=false,lastValue=input.value,lastContext='',labelGeneration=0;
    const names=new Map();
    function close(){generation++;controller?.abort();controller=null;menu.hidden=true;rows=[];active=-1;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
    function cancel(){clearTimeout(timer);timer=null;close();}
    function highlight(index){active=index;for(const [i,node] of [...menu.querySelectorAll('[role=option]')].entries())node.setAttribute('aria-selected',String(i===active));if(active>=0){input.setAttribute('aria-activedescendant',menu.querySelectorAll('[role=option]')[active].id);menu.querySelectorAll('[role=option]')[active].scrollIntoView({block:'nearest'});}else input.removeAttribute('aria-activedescendant');}
    async function choose(row){cancel();names.set(row.id,row.title);filter.value=row.id;input.value='';lastValue='';sync();invalidate();await changed();input.focus();}
    async function suggestions(){
      close();const query=input.value.trim();if(!query||!context().allowProfiles||composing)return;
      const current=++generation;controller=new AbortController();
      try{
        const result=await api('relation-options',{kinds:['person','group','organization'],query,limit:8},controller.signal);
        if(current!==generation||document.activeElement!==input||!context().allowProfiles)return;
        rows=result.items;menu.replaceChildren();if(!rows.length)return;
        const hint=make('div','music-search-suggestion-heading',ui("筛选关联人物／组织"));hint.setAttribute('role','presentation');menu.append(hint);
        for(const [index,row] of rows.entries()){
          const option=make('button','music-search-suggestion');option.type='button';option.id=`music-search-option-${index}`;option.setAttribute('role','option');option.setAttribute('aria-selected','false');
          option.append(make('span',null,row.title),make('small',null,({person:ui("人物"),group:ui("团体"),organization:ui("机构")})[row.kind]));
          option.addEventListener('mousedown',event=>event.preventDefault());option.addEventListener('click',()=>choose(row).catch(onError));menu.append(option);
        }
        menu.hidden=false;input.setAttribute('aria-expanded','true');
      }catch(error){if(error.name!=='AbortError'&&current===generation)onError(error);}
    }
    function queue(){lastValue=input.value;cancel();invalidate();syncClear();if(composing)return;timer=setTimeout(()=>{timer=null;changed().catch(onError);suggestions();},180);}
    input.addEventListener('input',queue);
    input.addEventListener('compositionstart',()=>{composing=true;cancel();invalidate();});
    input.addEventListener('compositionend',()=>{composing=false;queue();});
    input.addEventListener('focus',()=>{if(input.value.trim())suggestions();});
    input.addEventListener('keydown',event=>{
      if(event.isComposing||composing)return;
      if((event.key==='ArrowDown'||event.key==='ArrowUp')&&!menu.hidden){event.preventDefault();highlight(active<0?(event.key==='ArrowDown'?0:rows.length-1):(active+(event.key==='ArrowDown'?1:rows.length-1))%rows.length);}
      else if(event.key==='Enter'){event.preventDefault();if(!menu.hidden&&active>=0)choose(rows[active]).catch(onError);else{cancel();changed().catch(onError);}}
      else if(event.key==='Escape'&&!menu.hidden){event.preventDefault();event.stopPropagation();close();}
    });
    document.addEventListener('pointerdown',event=>{if(!wrap.contains(event.target))close();});
    wrap.addEventListener('focusout',()=>{queueMicrotask(()=>{if(!wrap.contains(document.activeElement))close();});});
    async function sync(){
      const state=context();if(state.key!==lastContext||input.value!==lastValue){cancel();lastContext=state.key;lastValue=input.value;}
      syncClear();chip.replaceChildren();const id=filter.value;chip.hidden=!id||!state.allowProfiles;if(chip.hidden)return;
      const seq=++labelGeneration;
      let title=names.get(id);if(!title){try{title=(await api('detail',{id})).item.title;names.set(id,title);}catch(error){onError(error);return;}}
      if(seq!==labelGeneration||filter.value!==id)return;
      chip.append(make('span',null,title));const remove=make('button','music-search-clear');remove.type='button';window.ShirosI18n.attribute(remove, 'aria-label', ui("清除关联人物或组织"));const icon=make('span',null,'×');icon.setAttribute('aria-hidden','true');remove.append(icon);
      remove.addEventListener('click',()=>{filter.value='';sync();invalidate();changed().catch(onError);});chip.append(remove);
    }
    return {sync,cancel};
  }
  window.ShirosMusicSearch={attach};
})();
