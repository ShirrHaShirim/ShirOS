/* Shared classification editor. Each library retains its own API and permissions. */
(() => {
  const ui = window.ShirosI18n.message;
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)window.ShirosI18n.text(n, text);if(cls)n.className=cls;return n;};
  const button=(label,fn,cls='secondary-button')=>{const b=el('button',label,cls);b.type='button';b.onclick=fn;return b;};
  window.ShirosTagManager={open:async config=>{
    let nodes=await config.list(),section=config.sections[0].id,current=null,parentId=null,expandedParents=new Set(),busy=false;
    const d=el('dialog',undefined,'music-dialog shared-tag-dialog'),form=el('form');d.append(form);document.body.append(d);d.onclose=()=>{window.ShirosFeedback.clear(d);d.remove();};
    const heading=el('div',undefined,'shared-tag-heading');heading.append(el('h2',ui("标签管理")),button(ui("关闭"),()=>d.close()));form.append(heading);
    const tabs=el('div',undefined,'taxonomy-tabs'),layout=el('div',undefined,'taxonomy-manager'),left=el('aside'),right=el('section');form.append(tabs,layout);layout.append(left,right);
    const search=el('input');search.type='search';window.ShirosI18n.attribute(search, 'placeholder', ui("搜索标签"));window.ShirosI18n.attribute(search, 'aria-label', ui("搜索分类"));const list=el('div',undefined,'taxonomy-manager-tree');left.append(button(ui("＋ 新建分类"),()=>select(null),'taxonomy-add'),search,list);
    const title=el('h3'),path=el('p','','taxonomy-path'),label=el('label',ui("名称"),'music-field'),name=el('input');name.required=true;name.maxLength=64;window.ShirosI18n.attribute(name, 'aria-label', ui("名称"));label.append(name);right.append(title,path,label,el('h4',ui("上级分类")));
    const parentCaption=el('p','','parent-caption'),parents=el('div',undefined,'parent-tree');parents.setAttribute('role','tree');window.ShirosI18n.attribute(parents, 'aria-label', ui("上级分类"));right.append(parentCaption,parents);
    const status=el('p','','taxonomy-manager-status');status.setAttribute('role','status');const footer=el('div',undefined,'shared-tag-footer'),remove=button(ui("删除标签"),()=>confirmDelete(),'danger-button'),save=el('button',ui("保存分类"),'primary-button');save.type='submit';footer.append(remove,save);form.append(status,footer);
    const confirmBox=el('div',undefined,'tag-delete-confirm');confirmBox.hidden=true;confirmBox.append(el('p',ui("删除此标签？记录和操作历史会保留。有下级分类时，请先移动或删除下级。")),button(ui("取消"),()=>confirmBox.hidden=true),button(ui("确认删除"),()=>deleteCurrent(),'danger-button'));form.insertBefore(confirmBox,footer);
    const displayLabel=n=>config.displayLabel?config.displayLabel(n):n.name;
    const inSection=n=>!n.namespace||n.namespace===section;
    function nodePath(node,translated=false){const names=[],seen=new Set();while(node&&!seen.has(node.id)){seen.add(node.id);names.unshift(translated?String(displayLabel(node)):node.name);node=nodes.find(n=>n.id===node.parent_id);}return names.join(' / ');}
    function excluded(id){let n=nodes.find(n=>n.id===id),seen=new Set();while(n&&!seen.has(n.id)){if(n.id===current?.id)return true;seen.add(n.id);n=nodes.find(p=>p.id===n.parent_id);}return false;}
    function renderParents(){
      parents.replaceChildren();const root=button(ui("根目录"),()=>{parentId=null;renderParents();},'parent-tree-row');root.setAttribute('role','treeitem');root.setAttribute('aria-selected',String(parentId===null));parents.append(root);
      function append(parent,host,depth,seen=new Set()){
        for(const n of nodes.filter(n=>inSection(n)&&(n.parent_id||null)===parent&&!excluded(n.id))){
          if(seen.has(n.id))continue;seen.add(n.id);
          const row=el('div',undefined,'parent-tree-branch'),nested=el('div'),hasChildren=nodes.some(x=>inSection(x)&&x.parent_id===n.id&&!excluded(x.id));
          row.style.paddingLeft=`${depth*16}px`;nested.setAttribute('role','group');nested.hidden=!expandedParents.has(n.id);
          const toggle=button(hasChildren?(nested.hidden?'▸':'▾'):'',()=>{if(expandedParents.has(n.id))expandedParents.delete(n.id);else expandedParents.add(n.id);nested.hidden=!expandedParents.has(n.id);window.ShirosI18n.text(toggle, nested.hidden?'▸':'▾');toggle.setAttribute('aria-expanded',String(!nested.hidden));},'parent-tree-toggle');toggle.disabled=!hasChildren;window.ShirosI18n.attribute(toggle, 'aria-label', ui("展开或收起 {0}", n.name));if(hasChildren)toggle.setAttribute('aria-expanded',String(!nested.hidden));
          const b=button(displayLabel(n),()=>{parentId=n.id;renderParents();},'parent-tree-row');b.setAttribute('role','treeitem');b.setAttribute('aria-level',String(depth+1));b.setAttribute('aria-selected',String(parentId===n.id));row.append(toggle,b);host.append(row);if(hasChildren){host.append(nested);append(n.id,nested,depth+1,seen);}
        }
      }append(null,parents,0);window.ShirosI18n.text(parentCaption, parentId?nodePath(nodes.find(n=>n.id===parentId)):ui("根目录"));
    }
    function renderList(){list.replaceChildren();const query=search.value.trim().toLocaleLowerCase();for(const n of nodes.filter(n=>inSection(n)&&[nodePath(n),nodePath(n,true)].join(' ').toLocaleLowerCase().includes(query)).sort((a,b)=>nodePath(a).localeCompare(nodePath(b),'zh-CN'))){const b=button('',()=>select(n),'taxonomy-manager-node');b.classList.toggle('selected',current?.id===n.id);b.append(el('strong',displayLabel(n)),el('small',n.parent_id?nodePath(nodes.find(x=>x.id===n.parent_id)):ui("根目录")));list.append(b);}if(!list.childElementCount)list.append(el('p',ui("没有匹配的标签"),'taxonomy-help'));}
    function select(n){if(busy)return;current=n;parentId=n?.parent_id||null;let ancestor=nodes.find(x=>x.id===parentId);const seen=new Set();while(ancestor&&!seen.has(ancestor.id)){seen.add(ancestor.id);expandedParents.add(ancestor.id);ancestor=nodes.find(x=>x.id===ancestor.parent_id);}name.value=n?.name||'';window.ShirosI18n.text(title, n?ui("编辑标签"):ui("新建标签"));window.ShirosI18n.text(path, n?nodePath(n):config.sections.find(s=>s.id===section).name);remove.hidden=!n;confirmBox.hidden=true;window.ShirosI18n.text(status, '');renderList();renderParents();}
    function setBusy(value){busy=value;save.disabled=remove.disabled=value;for(const b of confirmBox.querySelectorAll('button'))b.disabled=value;}
    async function refresh(){nodes=await config.list();await config.changed();}
    function confirmDelete(){if(current&&!busy)confirmBox.hidden=false;}
    async function deleteCurrent(){if(!current||busy)return;window.ShirosFeedback.clear(d);setBusy(true);try{await config.remove(current);await refresh();setBusy(false);select(null);window.ShirosI18n.text(status, ui("已删除"));}catch(e){window.ShirosI18n.text(status, '');window.ShirosFeedback.show(e.message,d);}finally{setBusy(false);}}
    for(const s of config.sections){const b=button(s.name,()=>{if(busy)return;section=s.id;for(const t of tabs.children)t.classList.toggle('active',t===b);select(null);},'taxonomy-tab');b.classList.toggle('active',s.id===section);tabs.append(b);}tabs.hidden=config.sections.length===1;
    search.oninput=renderList;form.onsubmit=async event=>{event.preventDefault();if(busy)return;window.ShirosFeedback.clear(d);setBusy(true);try{const node=await config.save({current,section,name:name.value.trim(),parent_id:parentId});await refresh();setBusy(false);select(nodes.find(n=>n.id===node.id)||null);window.ShirosI18n.text(status, ui("已保存"));}catch(e){window.ShirosI18n.text(status, '');window.ShirosFeedback.show(e.message,d);}finally{setBusy(false);}};
    select(null);d.showModal();return d;
  }};
})();
