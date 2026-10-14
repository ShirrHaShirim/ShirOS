/* All retrieved text is rendered as text, never as markup. */
(() => {
  const ui = window.ShirosI18n.message;
  'use strict';
  const $ = s => document.querySelector(s);
  const el = (tag, value = '', cls = '') => { const node = document.createElement(tag); window.ShirosI18n.text(node, value); node.className = cls; return node; };
  const labels = {queued:ui("等待整理"), claimed:ui("正在整理"), awaiting_review:ui("等待审核"), published:ui("已发布"), cancelled:ui("已取消")};
  const sections = {long_term:ui("长期信息"), current:ui("当前状态"), changes:ui("变化记录"), exceptions:ui("例外与边界"), unfinished:ui("未完成事项")};
  const reasons = {manual:ui("手动整理"), daily:ui("日常维护"), weekly:ui("每周回顾")};
  let domains = [], layers = [], jobs = [], offset = 0;
  const pageSize = 20;
  const headers = () => ({'Content-Type':'application/json','X-Shiros-Token':sessionStorage.getItem('shiros-token') || ''});
  async function api(action, payload = {}) {
    const response = await window.ShirosMotion.request('/ui-api/knowledge/' + action, {method:'POST', headers:headers(), body:JSON.stringify(payload)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || ui("暂时无法获取数据")); return data;
  }
  async function run(fn) { try { await fn(); } catch (error) { window.ShirosI18n.text($('#knowledge-message'), ui("操作未完成：{0}", error.message)); } }
  function selection(target, items, empty) {
    const selected = target.value; target.replaceChildren();
    if (!items.length) { const option = el('option', empty); option.value = ''; target.append(option); }
    for (const item of items) { const option = el('option', item.name); option.value = item.id; target.append(option); }
    if (items.some(x => x.id === selected)) target.value = selected;
    target.disabled = !items.length;
  }
  const nameOf = head => head?.layer === 'L0' ? ui("核心原则") : domains.find(d => d.id === head?.domain_key)?.name || ui("领域总结");
  const date = value => value && !Number.isNaN(Date.parse(value)) ? window.ShirosI18n.date(value, {timeZone:'Asia/Shanghai',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
  function cardHeading(title, category, status, stateClass = '') {
    const heading = el('div', '', 'knowledge-card-heading'), info = el('div', '', 'knowledge-card-title');
    info.append(el('p', category, 'eyebrow'), el('h3', title));
    heading.append(info, el('span', status, 'knowledge-badge ' + stateClass));
    return heading;
  }
  function empty(title, copy) { const node = el('div', '', 'knowledge-empty'); node.append(el('span', '◇', 'knowledge-empty-mark'), el('h3', title), el('p', copy)); return node; }
  function body(node, value) {
    for (const [key, title] of Object.entries(sections)) {
      if (value?.[key] == null) continue;
      const section = el('section', '', 'knowledge-prose');
      section.append(el('h4', title), el('p', typeof value[key] === 'string' ? value[key] : JSON.stringify(value[key], null, 2))); node.append(section);
    }
  }
  function technical(title, value) {
    const detail = el('details', '', 'knowledge-technical'); detail.append(el('summary', title), el('pre', JSON.stringify(value, null, 2))); return detail;
  }
  function renderLayers() {
    const list = $('#knowledge-layers'); list.replaceChildren();
    for (const item of layers) {
      const card = el('article', '', 'knowledge-card knowledge-layer-card');
      const heading = cardHeading(nameOf(item), item.layer === 'L0' ? ui("核心原则") : ui("领域总结"), item.stale ? (item.revision ? ui("需要更新") : ui("尚未建立")) : ui("依赖有效"), item.stale ? 'is-pending' : 'is-current');
      card.append(heading, el('p', item.revision ? ui("修订 {0} · {1}", item.revision, item.stale ? ui("下层依赖已变化，请重新整理") : ui("与当前证据保持一致")) : ui("尚无正式综述。建立维护任务后提交草稿。"), 'knowledge-caption knowledge-card-meta'));
      const versions = item.versions || [];
      if (versions[0]?.body) { const current = el('details', '', 'knowledge-history'); current.append(el('summary', ui("阅读当前综述"))); body(current, versions[0].body); card.append(current); }
      if (versions.length) {
        const history = el('details', '', 'knowledge-history'); history.append(el('summary', ui("历史修订 · {0}", versions.length)));
        for (const version of versions) {
          const detail = el('details', '', 'knowledge-version'); detail.append(el('summary', ui("修订 {0} · {1}", version.revision, date(version.created_at))));
          if (version.body) body(detail, version.body); else detail.append(el('p', ui("依赖已过期或不可见，正文暂不展示。"), 'knowledge-caption'));
          history.append(detail);
        } card.append(history);
      }
      list.append(card);
    }
    if (!layers.length) list.append(empty(ui("暂无综述"), ui("启用领域后建立维护任务。")));
    window.ShirosI18n.text($('#knowledge-synthesis-count'), layers.length);
  }
  async function inspect(job) {
    const data = await api('evidence', {id:job.id});
    const dialog = el('dialog', '', 'knowledge-review'), head = el('header', '', 'knowledge-review-heading');
    const heading = el('div'), title = el('h2', nameOf(layers.find(x => x.id === job.head_id)));
    title.id = 'knowledge-review-title'; dialog.setAttribute('aria-labelledby', title.id);
    heading.append(el('p', 'REVIEW / ' + (data.layer || 'KNOWLEDGE'), 'eyebrow'), title);
    const close = el('button', ui("关闭"), 'secondary-button'); close.onclick = () => dialog.close(); head.append(heading, close); dialog.append(head);
    const content = el('div', '', 'knowledge-review-content'), feedback = el('p', '', 'knowledge-notice'); feedback.setAttribute('role', 'status');
    content.append(el('span', labels[job.state] || job.state, 'knowledge-badge'));
    if (data.stale) content.append(empty(ui("依赖已变化"), ui("当前候选不可审核。请重新建立维护任务。")));
    else {
      const draft = el('section', '', 'knowledge-draft'); draft.append(el('h3', ui("综述草稿")));
      if (data.draft) body(draft, data.draft); else draft.append(empty(ui("草稿尚未提交"), ui("等待授权客户端提交草稿。")));
      content.append(draft);
      const evidence = el('section', '', 'knowledge-evidence'); evidence.append(el('h3', ui("依据与来源 · {0}", data.total || 0)));
      const list = el('div'), more = el('button', ui("加载更多依据"), 'secondary-button'); let loaded = 0;
      function appendEvidence(packet) {
        for (const item of packet.items || []) {
          const detail = el('details', '', 'knowledge-evidence-item');
          detail.append(el('summary', item.title || (item.layer ? nameOf(item) : item.name) || ui("来源记录")));
          if (item.text) detail.append(el('p', item.text, 'knowledge-evidence-text'));
          if (item.versions?.[0]?.body) body(detail, item.versions[0].body);
          detail.append(technical(ui("来源详情"), item)); list.append(detail);
        }
        loaded += (packet.items || []).length; more.hidden = loaded >= (packet.total || 0);
      }
      appendEvidence(data);
      more.onclick = async () => { more.disabled = true; try { const packet = await api('evidence', {id:job.id, offset:loaded}); if (packet.stale) { dialog.close(); await inspect(job); } else appendEvidence(packet); } catch (error) { window.ShirosI18n.text(feedback, ui("依据加载失败：{0}", error.message)); } finally { more.disabled = false; } };
      evidence.append(list, more); content.append(evidence, technical(ui("整理进度与依赖详情"), {progress:data.progress, dependencies:data.dependencies}));
    }
    content.append(feedback); dialog.append(content);
    const footer = el('footer', '', 'knowledge-review-footer'); footer.append(el('p', ui("发布前请核对来源、变化、例外与未完成事项。")));
    if (job.state === 'awaiting_review' && !data.stale) {
      const approve = el('button', ui("审核并发布"), 'primary-button');
      approve.onclick = async () => {
        if (!confirm(data.layer === 'L0' ? ui("确认更新核心原则原则？这不会自动修改详细记录。") : ui("确认这份综述准确保留变化、例外和未完成事项，并发布新修订？"))) return;
        approve.disabled = true;
        try { await api('publish', {id:job.id,layer:data.layer,expected_revision:data.base_revision,confirm:data.layer === 'L0'}); dialog.close(); await refresh(); }
        catch (error) { window.ShirosI18n.text(feedback, ui("发布未完成：{0}", error.message)); } finally { approve.disabled = false; }
      }; footer.append(approve);
    }
    dialog.append(footer); dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal();
  }
  function renderJobs() {
    const container = $('#knowledge-jobs'); container.replaceChildren(); const filter = $('#knowledge-job-filter').value;
    const visible = jobs.filter(job => filter === 'all' || (filter === 'active' ? !['published','cancelled'].includes(job.state) : job.state === filter));
    for (const job of visible) {
      const card = el('article', '', 'knowledge-card knowledge-job');
      const top = cardHeading(nameOf(layers.find(x => x.id === job.head_id)), reasons[job.reason] || job.reason, labels[job.state] || job.state, 'state-' + job.state);
      card.append(top, el('p', ui("基础修订 {0} · {1}", job.base_revision, date(job.created_at)), 'knowledge-caption knowledge-card-meta'));
      const actions = el('div', '', 'knowledge-job-actions'), read = el('button', job.state === 'awaiting_review' ? ui("阅读并审核 →") : ui("查看详情 →"), 'secondary-button'); read.onclick = () => run(() => inspect(job)); actions.append(read);
      if (!['published','cancelled'].includes(job.state)) { const cancel = el('button', ui("取消"), 'knowledge-text-button'); cancel.onclick = () => run(async () => { if (confirm(ui("取消此维护任务？已保存的记录和审计仍保留。"))) { await api('cancel', {id:job.id}); await refresh(); } }); actions.append(cancel); }
      card.append(actions); container.append(card);
    }
    if (!visible.length) container.append(empty(filter === 'all' ? ui("暂无维护任务") : ui("本页没有这类任务"), filter === 'all' ? ui("选择领域并建立维护任务。") : ui("切换筛选或翻页查看。")));
    window.ShirosI18n.text($('#knowledge-jobs-count'), visible.length);
    $('#knowledge-jobs-prev').disabled = offset === 0; $('#knowledge-jobs-next').disabled = jobs.length < pageSize;
    $('#knowledge-job-pages').hidden = offset === 0 && jobs.length < pageSize; window.ShirosI18n.text($('#knowledge-jobs-page'), ui("第 {0} 页", offset / pageSize + 1));
  }
  function renderStats() {
    const stats = $('#knowledge-stats'); stats.replaceChildren();
    for (const [value, label] of [[domains.length,ui("已启用领域")], [layers.filter(x => x.revision && !x.stale).length,ui("有效综述")], [layers.filter(x => x.stale).length,ui("待建立 / 更新")], [jobs.filter(x => x.state === 'awaiting_review').length,ui("本页待审核")]]) {
      const node = el('div', '', 'knowledge-stat'); node.append(el('strong', String(value)), el('span', label)); stats.append(node);
    }
  }
  async function refresh() {
    const button = $('#knowledge-refresh'); button.disabled = true; $('#knowledge-view').setAttribute('aria-busy', 'true');
    try {
      const [result, taskResult, response] = await Promise.all([api('domains'), api('jobs', {offset,limit:pageSize}), fetch('/ui-api/tags', {method:'POST',headers:headers(),body:'{}'})]);
      const tags = await response.json(); if (!response.ok) throw new Error(tags.error || ui("标签读取失败"));
      domains = result.items; layers = result.layers; jobs = taskResult.items;
      selection($('#knowledge-domain'), domains, ui("请先启用一个领域")); selection($('#knowledge-domain-root'), (tags.items || []).filter(t => !t.parent_id), ui("请先建立顶层标签"));
      $('#knowledge-enqueue').disabled = !domains.length; $('#knowledge-domain-enable').disabled = !$('#knowledge-domain-root').value;
      renderLayers(); renderJobs(); renderStats();
    } finally { button.disabled = false; $('#knowledge-view').setAttribute('aria-busy', 'false'); }
  }
  $('#knowledge-refresh').onclick = () => run(refresh);
  const tabs=[$('#knowledge-tab-synthesis'),$('#knowledge-tab-jobs')];
  function selectTab(tab){for(const button of tabs){const active=button===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;$('#'+button.getAttribute('aria-controls')).hidden=!active;}}
  for(const tab of tabs){tab.onclick=()=>selectTab(tab);tab.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?tabs[0]:event.key==='End'?tabs[1]:tabs[1-tabs.indexOf(tab)];selectTab(next);next.focus();};}
  $('#knowledge-job-filter').onchange = renderJobs;
  $('#knowledge-jobs-prev').onclick = () => run(async () => { offset = Math.max(0, offset - pageSize); await refresh(); });
  $('#knowledge-jobs-next').onclick = () => run(async () => { offset += pageSize; await refresh(); });
  $('#knowledge-domain-enable').onclick = () => run(async () => { const id = $('#knowledge-domain-root').value; if (!id) throw new Error(ui("请先建立顶层标签")); if (!confirm(ui("将此顶层标签启用为受治理的维护领域？音乐档案只可关联一个领域。"))) return; await api('domain-enable', {domain_id:id,music:$('#knowledge-domain-music').checked}); window.ShirosI18n.text($('#knowledge-message'), ui("领域已启用，可以开始整理。")); await refresh(); });
  $('#knowledge-enqueue').onclick = () => run(async () => { const id = $('#knowledge-domain').value; if (!id) throw new Error(ui("请先启用一个领域")); const result = await api('enqueue', {domain_id:id,request_id:crypto.randomUUID()}); window.ShirosI18n.text($('#knowledge-message'), result.state === 'unchanged' ? ui("没有变化，无需新修订。") : ui("维护任务已建立，等待授权客户端整理与提交草稿。")); offset = 0; await refresh(); });
  $('#knowledge-global').onclick = () => run(async () => { const result = await api('enqueue', {layer:'L0',request_id:crypto.randomUUID()}); window.ShirosI18n.text($('#knowledge-message'), result.state === 'unchanged' ? ui("核心原则依赖没有变化。") : ui("核心原则候选任务已建立，整理后需人工审核。")); offset = 0; await refresh(); });
  document.addEventListener('shiros-knowledge-open', () => run(refresh));
})();
