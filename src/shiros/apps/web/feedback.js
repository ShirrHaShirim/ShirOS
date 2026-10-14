/* Transient feedback anchored to the page or modal that owns the operation. */
(() => {
  const ui = window.ShirosI18n.message;
  const active=new Map();
  function clear(scope){for(const [anchor,entry] of active)if(!scope||scope===anchor||scope.contains(anchor))entry.remove();}
  function show(message,anchor,severity='error'){
    if(!anchor?.isConnected||!message)return;
    clear(anchor);
    const bounds=anchor.getBoundingClientRect();if(!bounds.width||!bounds.height)return;
    const toast=document.createElement('div');toast.className='shiros-toast';toast.dataset.severity=severity;
    toast.setAttribute('role',severity==='error'?'alert':'status');toast.setAttribute('aria-atomic','true');
    const text=document.createElement('span');window.ShirosI18n.text(text, message);
    const close=document.createElement('button');close.type='button';window.ShirosI18n.text(close, '×');window.ShirosI18n.attribute(close, 'aria-label', ui("关闭提示"));toast.append(text,close);
    const modal=anchor.closest('dialog');(modal||document.body).append(toast);
    // Keep feedback above a modal's scrolling content without changing its scroll position.
    if(typeof toast.showPopover==='function'){toast.setAttribute('popover','manual');toast.showPopover();}
    let timer,removalTimer,remaining=severity==='error'?4500:2800,started=0,leaving=false,paused=true;
    const previousFocus=document.activeElement;
    function remove(){clearTimeout(timer);clearTimeout(removalTimer);observer.disconnect();resize.disconnect();window.removeEventListener('resize',position);window.removeEventListener('scroll',position,true);toast.remove();if(active.get(anchor)?.toast===toast)active.delete(anchor);}
    function dismiss(){if(leaving)return;leaving=true;clearTimeout(timer);toast.classList.add('is-leaving');removalTimer=setTimeout(remove,matchMedia('(prefers-reduced-motion: reduce)').matches?0:260);}
    function position(){
      const rect=anchor.getBoundingClientRect();if(!anchor.isConnected||!rect.width||!rect.height||modal&&!modal.open){remove();return;}
      toast.style.width=Math.max(1,Math.min(360,rect.width-24,innerWidth-24))+'px';
      toast.style.left=Math.max(12,Math.min(innerWidth-toast.offsetWidth-12,rect.right-toast.offsetWidth-12))+'px';
      toast.style.top=Math.max(12,Math.min(innerHeight-toast.offsetHeight-12,rect.bottom-toast.offsetHeight-12))+'px';
    }
    function resume(){if(leaving||toast.matches(':hover')||toast.contains(document.activeElement))return;clearTimeout(timer);paused=false;started=performance.now();timer=setTimeout(dismiss,remaining);}
    function pause(){if(leaving||paused)return;paused=true;clearTimeout(timer);remaining=Math.max(0,remaining-(performance.now()-started));}
    toast.addEventListener('mouseenter',pause);toast.addEventListener('mouseleave',resume);toast.addEventListener('focusin',pause);toast.addEventListener('focusout',()=>queueMicrotask(resume));
    close.onclick=()=>{dismiss();if(toast.contains(document.activeElement)&&previousFocus?.isConnected)previousFocus.focus({preventScroll:true});};
    const observer=new MutationObserver(()=>{if(!anchor.isConnected||modal&&!modal.open)remove();});observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});
    const resize=new ResizeObserver(position);resize.observe(anchor);window.addEventListener('resize',position);window.addEventListener('scroll',position,true);
    active.set(anchor,{toast,remove});position();requestAnimationFrame(()=>{if(toast.isConnected)toast.classList.add('is-visible');});resume();
  }
  window.ShirosFeedback={show,clear};
})();
