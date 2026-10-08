(() => {
  "use strict";
  const tokenFromHash = new URLSearchParams(location.hash.slice(1)).get(
    "token",
  );
  let token = tokenFromHash || sessionStorage.getItem("shiros-token") || "";
  if (tokenFromHash) {
    sessionStorage.setItem("shiros-token", tokenFromHash);
    history.replaceState(null, "", location.pathname + location.search);
  }
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [
    ...root.querySelectorAll(selector),
  ];
  const dict = {
    zh: {
      workspace: "个人工作区",
      navMemory: "记忆",
      musicTitle: "音乐库",
      navReview: "待审",
      navImport: "导入",
      localOnly: "仅本机",
      localWorkspace: "本机工作区",
      lockedTitle: "会话未开启",
      lockedCopy: "请通过 shiros open 打开本机会话。",
      personalKnowledge: "个人知识",
      memoryTitle: "记忆",
      memorySub: "已确认并可供检索的内容",
      addMemory: "添加记忆",
      searchPlaceholder: "搜索记忆…",
      selectMemory: "选择一条记忆",
      selectMemorySub: "查看内容、来源与记录时间",
      humanReview: "人工确认",
      reviewTitle: "待审队列",
      reviewSub: "确认前不会写入长期记忆",
      selectCandidate: "选择待审内容",
      selectCandidateSub: "审阅来源并决定是否保留",
      bringKnowledge: "导入内容",
      importTitle: "新增记忆",
      importSub: "内容先预览，再决定是否送审",
      sourceContent: "来源内容",
      titleLabel: "标题",
      titlePlaceholder: "例如：写作偏好",
      contentLabel: "文本内容",
      loadExample: "载入示例",
      contentPlaceholder: "输入或粘贴你希望记住的内容…",
      chooseFile: "选择文件",
      preview: "预览",
      sendToReview: "送交审核",
      previewTitle: "预览",
      notPreviewed: "尚未预览",
      previewHelp: "标题与文本准备好后，点击预览检查处理结果。",
      privacyNote: "原文不落盘；确认送审后仅保存安全文本。",
      confirmAction: "确认操作",
      cancel: "取消",
      confirm: "确认",
      created: "记录时间",
      updated: "更新时间",
      source: "来源",
      evidenceLevel: "证据等级",
      status: "状态",
      provenance: "来源记录",
      memoryContent: "记忆内容",
      candidateContent: "审核内容",
      editedContent: "可编辑文本",
      approve: "批准",
      reject: "拒绝",
      saveEdit: "保存修改",
      revoke: "撤销",
      hide: "隐藏",
      unhide: "取消隐藏",
      noMemories: "还没有记忆",
      noMemoriesSub: "导入内容并通过审核后，记忆会显示在这里。",
      startImport: "开始导入",
      noCandidates: "队列为空",
      noCandidatesSub: "新导入的内容会先出现在待审队列。",
      noResults: "没有匹配结果",
      noResultsSub: "试试其他关键词。",
      emptyTitle: "请填写标题",
      emptyText: "请填写文本内容",
      previewFirst: "请先预览这份内容",
      previewSuccess: "预览已就绪",
      notEligible: "安全文本未获准保存",
      safeTextLabel: "安全文本（送审内容）",
      stageSuccess: "已送交审核",
      approvedSuccess: "记忆已批准",
      rejectedSuccess: "已拒绝该候选内容",
      savedSuccess: "修改已保存",
      revokedSuccess: "已撤销",
      hiddenSuccess: "显示状态已更新",
      approveTitle: "批准这条内容？",
      approveCopy: "批准后，这条内容会成为可检索记忆。",
      rejectTitle: "拒绝这条内容？",
      rejectCopy: "拒绝后候选内容将退出待审队列。",
      revokeTitle: "撤销这条记录？",
      revokeCopy: "此操作会撤销选定来源或记忆。",
      confirmApprove: "批准",
      confirmReject: "拒绝",
      confirmRevoke: "撤销",
      permission: "会话已失效，请重新运行 shiros open。",
      loadFailed: "暂时无法读取数据，请重试。",
      actionFailed: "操作未完成，请重试。",
      previewFailed: "预览失败，请检查内容后重试。",
      kindText: "文本",
      kindMarkdown: "Markdown",
      kindJson: "JSON",
      kindCsv: "CSV",
      chars: "字符",
      privacySafe: "隐私检查",
      allowed: "可送审",
      blocked: "需检查",
      hideLabel: "已隐藏",
      visibleLabel: "可见",
      searchCount: "条记录",
      fileInvalid: "请选择 .txt、.md、.json 或 .csv 文件。",
      fileTooLarge: "文件内容超过 50000 字符限制。",
      revokeSource: "来源",
      revokeMemory: "记忆",
      revisionConflict: "候选内容已变化，请重新选择并检查后批准。",
      unsaved: "有未保存的修改",
    },
    en: {
      workspace: "WORKSPACE",
      navMemory: "Memory",
      musicTitle: "音乐库",
      navReview: "Review",
      navImport: "Import",
      localOnly: "Local only",
      localWorkspace: "LOCAL WORKSPACE",
      lockedTitle: "Session not open",
      lockedCopy: "Open this local session with shiros open.",
      personalKnowledge: "PERSONAL KNOWLEDGE",
      memoryTitle: "Memory",
      memorySub: "Confirmed content available for retrieval",
      addMemory: "Add memory",
      searchPlaceholder: "Search memories…",
      selectMemory: "Select a memory",
      selectMemorySub: "View content, provenance and timestamps",
      humanReview: "HUMAN REVIEW",
      reviewTitle: "Review queue",
      reviewSub: "Nothing enters long-term memory before approval",
      selectCandidate: "Select an item to review",
      selectCandidateSub: "Inspect its source and decide whether to keep it",
      bringKnowledge: "BRING IN KNOWLEDGE",
      importTitle: "Add memory",
      importSub: "Preview the content before submitting it for review",
      sourceContent: "Source content",
      titleLabel: "Title",
      titlePlaceholder: "e.g. Writing preferences",
      contentLabel: "Text content",
      loadExample: "Load example",
      contentPlaceholder: "Type or paste something you want remembered…",
      chooseFile: "Choose file",
      preview: "Preview",
      sendToReview: "Send to review",
      previewTitle: "Preview",
      notPreviewed: "Not previewed",
      previewHelp: "Enter a title and text, then preview the result.",
      privacyNote:
        "Imported source text is handled in this page. A review item is saved only when you explicitly submit it.",
      confirmAction: "CONFIRM ACTION",
      cancel: "Cancel",
      confirm: "Confirm",
      created: "Created",
      updated: "Updated",
      source: "Source",
      evidenceLevel: "Evidence level",
      status: "Status",
      provenance: "Provenance",
      memoryContent: "Memory content",
      candidateContent: "Review content",
      editedContent: "Editable text",
      approve: "Approve",
      reject: "Reject",
      saveEdit: "Save changes",
      revoke: "Revoke",
      hide: "Hide",
      unhide: "Unhide",
      noMemories: "No memories yet",
      noMemoriesSub: "Imported content appears here after review and approval.",
      startImport: "Start import",
      noCandidates: "Queue is clear",
      noCandidatesSub: "New imports appear here for review.",
      noResults: "No matches found",
      noResultsSub: "Try another search term.",
      emptyTitle: "Enter a title",
      emptyText: "Enter some text content",
      previewFirst: "Preview this content first",
      previewSuccess: "Preview is ready",
      stageSuccess: "Sent to review",
      approvedSuccess: "Memory approved",
      rejectedSuccess: "Candidate rejected",
      savedSuccess: "Changes saved",
      revokedSuccess: "Record revoked",
      hiddenSuccess: "Visibility updated",
      approveTitle: "Approve this item?",
      approveCopy:
        "This will make the content available as a searchable memory.",
      rejectTitle: "Reject this item?",
      rejectCopy: "The candidate will leave the review queue.",
      revokeTitle: "Revoke this record?",
      revokeCopy: "This will revoke the selected source or memory.",
      confirmApprove: "Approve",
      confirmReject: "Reject",
      confirmRevoke: "Revoke",
      permission: "Session expired. Run shiros open again.",
      loadFailed: "Could not load data. Please retry.",
      actionFailed: "Action did not complete. Please retry.",
      previewFailed: "Preview failed. Check the content and try again.",
      kindText: "Text",
      kindMarkdown: "Markdown",
      kindJson: "JSON",
      kindCsv: "CSV",
      chars: "characters",
      privacySafe: "Privacy check",
      allowed: "Ready to submit",
      blocked: "Needs review",
      hideLabel: "Hidden",
      visibleLabel: "Visible",
      searchCount: "items",
      fileInvalid: "Choose a .txt, .md, .json or .csv file.",
      fileTooLarge: "File exceeds the 50000 character limit.",
      revokeSource: "Source",
      revokeMemory: "Memory",
      revisionConflict: "This candidate changed. Reopen and review it before approving.",
      unsaved: "Unsaved changes",
    },
  };
  Object.assign(dict.zh, {
    libraryTitle: "全部记忆", groupTags: "标签分组", byDate: "添加日期",
    recentMemories: "最近添加", expandMemories: "展开全部", collapseMemories: "收起",
    recentCount: "最近", totalCount: "共",
    manageTags: "管理标签", tags: "标签", saveTags: "保存标签", noTags: "暂无标签",
    editTag: "编辑标签", newTag: "＋ 新建标签", tagName: "标签名称", parentTag: "上级标签",
    rootTag: "顶层", allMemories: "全部记忆", chooseTag: "选择一个标签",
    tagInvalid: "标签名称重复、层级无效或操作未完成。", tagsSaved: "标签已保存",
    privacyNote: "原文不落盘；提交后仅保存安全文本。",
    safeTextLabel: "安全文本",
    reviewSub: "敏感或无法确定安全的内容等待审核", importSub: "预览并提交内容",
    sendToReview: "提交记忆", allowed: "可提交", noCandidatesSub: "暂无需要人工审核的内容。",
    noMemoriesSub: "已通过审核的记忆会显示在这里。",
    notEligible: "安全文本未获准保存",
    searchCount: "条记录",
    findTags: "搜索标签", selectedTags: "已选标签", noTagMatch: "未找到标签",
    clearTags: "清空选择", includesChildren: "含下级标签", autoApproved: "已自动存入记忆库",
    removeTagSelection: "取消标签",
    proposedTitle: "记忆标题", proposedSources: "结构化来源记录",
    targetMemory: "目标记忆", baseRevision: "原修订版本",
    invalidSources: "来源记录须为有效的 JSON 对象数组。",
    sourceRecords: "来源记录", originalProvenance: "原始来源（历史记录）",
    sourceKind: "来源类型", sourceLocator: "来源位置", observedAt: "来源时间",
  });
  Object.assign(dict.en, {
    libraryTitle: "All memories", groupTags: "Tag groups", byDate: "Date added",
    recentMemories: "Recently added", expandMemories: "Expand all", collapseMemories: "Collapse",
    recentCount: "Recent", totalCount: "Total",
    manageTags: "Manage tags", tags: "Tags", saveTags: "Save tags", noTags: "No tags yet",
    editTag: "Edit tag", newTag: "+ New tag", tagName: "Tag name", parentTag: "Parent tag",
    rootTag: "Root", allMemories: "All memories", chooseTag: "Select a tag",
    tagInvalid: "Duplicate name, invalid hierarchy, or action did not complete.", tagsSaved: "Tags saved",
    privacyNote:
      "Source text is not written to disk. After submission, only safe text is saved.",
    safeTextLabel: "Safe text to submit",
    reviewSub: "Sensitive or uncertain content awaiting review", importSub: "Preview and submit content",
    sendToReview: "Submit memory", allowed: "Ready to submit", noCandidatesSub: "No content needs manual review.",
    noMemoriesSub: "Approved memories appear here.",
    notEligible: "Safe text is not eligible for storage",
    searchCount: "items",
    findTags: "Search tags", selectedTags: "Selected tags", noTagMatch: "No matching tags",
    clearTags: "Clear selection", includesChildren: "Including nested tags", autoApproved: "Saved to memory automatically",
    removeTagSelection: "Remove tag selection",
    proposedTitle: "Memory title", proposedSources: "Structured source records",
    targetMemory: "Target memory", baseRevision: "Base revision",
    invalidSources: "Source records must be a valid JSON array of objects.",
    sourceRecords: "Source records", originalProvenance: "Original source (history)",
    sourceKind: "Source kind", sourceLocator: "Source location", observedAt: "Source date",
  });
  let lang = localStorage.getItem("shiros-locale") === "en" ? "en" : "zh";
  let currentView = "memory",
    memories = [],
    candidates = [],
    selectedMemory = null,
    selectedCandidate = null,
    previewId = null,
    toastTimer,
    searchTimer,
    searchRevision = 0;
  let tags = [], libraryItems = [], libraryMode = "tags", libraryTag = null,
    libraryOffset = 0, libraryTotal = 0, libraryRevision = 0, detailRevision = 0;
  const libraryLimit = 50;
  const recentLimit = 20;
  let memoryTotal = 0;
  const expandedTags = new Set(), pickerExpansion = new Map(), tagDrafts = new Map();
  const tr = (key) => dict[lang][key] || key;
  const text = (node, value) => {
    node.textContent = value == null ? "" : String(value);
  };
  function applyLanguage() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    $$("[data-i18n]").forEach((el) => text(el, tr(el.dataset.i18n)));
    $$("[data-i18n-placeholder]").forEach((el) =>
      el.setAttribute("placeholder", tr(el.dataset.i18nPlaceholder)),
    );
    $("#language-toggle").textContent = lang === "zh" ? "EN" : "中文";
    $("#view-title").textContent = tr(
      currentView === "music" ? "musicTitle" : currentView === "memory"
        ? "memoryTitle"
        : currentView === "review"
          ? "reviewTitle"
          : currentView === "library" ? "libraryTitle" : "importTitle",
    );
  }
  function notice(message, info = false) {
    const el = $("#notice");
    text(el, message);
    el.classList.toggle("info", info);
    el.classList.remove("hidden");
  }
  function clearNotice() {
    $("#notice").classList.add("hidden");
  }
  function toast(message) {
    const el = $("#toast");
    text(el, message);
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 2800);
  }
  const memoryId = (item) => item.memory_id ?? item.id ?? "";
  const candidateId = (item) => item.id ?? item.candidate_id ?? "";
  const titleOf = (item) =>
    item.title ||
    item.label ||
    (lang === "zh" ? "未命名内容" : "Untitled item");
  const contentOf = (item) =>
    item.text ?? item.content ?? item.normalized_text ?? item.body ?? "";
  function date(value) {
    if (!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.valueOf())
      ? String(value)
      : new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en", {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(d);
  }
  function kindName(value) {
    const names = {
      text: "kindText",
      markdown: "kindMarkdown",
      md: "kindMarkdown",
      json: "kindJson",
      csv: "kindCsv",
    };
    return tr(names[String(value || "text").toLowerCase()] || "kindText");
  }
  async function api(action, payload = {}) {
    if (!token)
      throw Object.assign(new Error("permission.denied"), {
        code: "permission.denied",
      });
    const response = await fetch(`/ui-api/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Shiros-Token": token },
      body: JSON.stringify(payload),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {}
    if (!response.ok) {
      const code =
        data.error ||
        (response.status === 403 ? "permission.denied" : "request.failed");
      throw Object.assign(new Error(code), { code, status: response.status });
    }
    return data;
  }
  function errorMessage(error, fallback = "actionFailed") {
    if (error.code === "candidate.revision_conflict") return tr("revisionConflict");
    if (error.code === "permission.denied") {
      sessionStorage.removeItem("shiros-token");
      token = "";
      $("#locked-screen").classList.remove("hidden");
      return tr("permission");
    }
    return tr(fallback);
  }
  if (!token) $("#locked-screen").classList.remove("hidden");
  function renderStats(stats = {}) {
    if (stats.memories != null) memoryTotal = stats.memories;
    text($("#memory-count"), memoryTotal);
    text($("#candidate-count"), stats.candidates ?? candidates.length);
    text($("#queue-total"), stats.candidates ?? candidates.length);
  }
  async function loadSession() {
    try {
      const data = await api("session");
      if (data.locale && ["zh", "en"].includes(data.locale)) lang = data.locale;
      if (data.scope) text($("#scope-label"), data.scope);
      renderStats(data.stats);
      applyLanguage();
    } catch (e) {
      notice(errorMessage(e, "loadFailed"));
    }
  }
  function card(item, selected, type) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `list-item${selected ? " selected" : ""}`;
    button.setAttribute("aria-pressed", String(selected));
    const title = document.createElement("span");
    title.className = "item-title";
    text(title, titleOf(item));
    const snippet = document.createElement("span");
    snippet.className = "item-snippet";
    text(snippet, contentOf(item));
    const meta = document.createElement("span");
    meta.className = "item-meta";
    const tag = document.createElement("span");
    tag.className = "item-kind";
    text(tag, kindName(item.kind || item.source_kind));
    meta.append(tag);
    const when = document.createElement("span");
    text(when, date(item.added_at || item.created_at || item.updated_at));
    meta.append(when);
    button.append(title, snippet, meta);
    button.addEventListener("click", () =>
      type === "memory" || type === "library"
        ? selectMemory(memoryId(item), type)
        : selectCandidate(candidateId(item)),
    );
    return button;
  }
  function emptyList(container, heading, sub, action) {
    const wrap = document.createElement("div");
    wrap.className = "empty-list";
    const h = document.createElement("strong");
    text(h, heading);
    const p = document.createElement("span");
    text(p, sub);
    wrap.append(h, p);
    if (action) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "secondary-button empty-cta";
      text(b, action.label);
      b.addEventListener("click", action.run);
      wrap.append(b);
    }
    container.replaceChildren(wrap);
  }
  function renderMemoryList() {
    const list = $("#memory-list"),
      filtered = memories;
    text($("#memory-result-meta"), $("#memory-search").value.trim()
      ? `${filtered.length} ${tr("searchCount")}`
      : `${tr("recentCount")} ${filtered.length} / ${tr("totalCount")} ${memoryTotal}`);
    if (!filtered.length) {
      const searching = $("#memory-search").value.trim().length > 0;
      emptyList(
        list,
        searching ? tr("noResults") : tr("noMemories"),
        searching ? tr("noResultsSub") : tr("noMemoriesSub"),
        searching
          ? null
          : { label: tr("startImport"), run: () => showView("import") },
      );
      return;
    }
    list.replaceChildren(
      ...filtered.map((item) =>
        card(
          item,
          String(memoryId(item)) === String(selectedMemory?.id),
          "memory",
        ),
      ),
    );
  }
  function createMeta(label, value) {
    const cell = document.createElement("div");
    cell.className = "meta-cell";
    const l = document.createElement("span");
    l.className = "meta-label";
    text(l, label);
    const v = document.createElement("span");
    v.className = "meta-value";
    text(v, value);
    cell.append(l, v);
    return cell;
  }
  function detailHeader(item, actions = []) {
    const header = document.createElement("div");
    header.className = "detail-header";
    const h = document.createElement("h2");
    text(h, titleOf(item));
    const buttons = document.createElement("div");
    buttons.className = "detail-actions";
    actions.forEach(({ label, cls, run }) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = cls || "secondary-button";
      text(b, label);
      b.addEventListener("click", run);
      buttons.append(b);
    });
    header.append(h, buttons);
    return header;
  }
  function contentBlock(value, label) {
    const block = document.createElement("section");
    const h = document.createElement("h3");
    text(h, label);
    const p = document.createElement("div");
    p.className = "prose";
    text(p, value || "—");
    block.className = "source-block";
    block.append(h, p);
    return block;
  }
  async function selectMemory(id, context = "memory") {
    const revision = ++detailRevision;
    const item = (context === "library" ? libraryItems : memories).find((m) => String(memoryId(m)) === String(id));
    if (!item) return;
    selectedMemory = { ...item, id };
    if (context === "library") renderLibraryList(); else renderMemoryList();
    const panel = $(`#${context}-detail`);
    panel.setAttribute("aria-busy", "true");
    try {
      const data = await api("memory", { id });
      if (revision !== detailRevision) return;
      const memory = data.memory || data;
      selectedMemory = { ...memory, id: memoryId(memory) || id };
      const revoke = () =>
        confirmAction(
          "revokeTitle",
          "revokeCopy",
          "confirmRevoke",
          async () => {
            await api("revoke", { id: memoryId(memory) || id, kind: "memory" });
            toast(tr("revokedSuccess"));
            selectedMemory = null;
            panel.replaceChildren();
            if (context === "library") await loadLibrary(); else await refreshMemory();
          },
        );
      panel.replaceChildren(
        detailHeader(memory, [
          { label: tr("revoke"), cls: "danger-button", run: revoke },
        ]),
      );
      panel.append(contentBlock(contentOf(memory), tr("memoryContent")));
      document.dispatchEvent(new CustomEvent("shiros-memory-selected", {detail: {id, panel}}));
      await loadTags();
      if (revision !== detailRevision) return;
      const assigned = memory.tags || (await api("memory-tags", { id })).items || [];
      if (revision !== detailRevision) return;
      panel.append(tagPicker(id, assigned, context));
      const meta = document.createElement("div");
      meta.className = "meta-grid";
      meta.append(
        createMeta(tr("created"), date(memory.created_at)),
        createMeta(lang === "zh" ? "版本" : "Revision", memory.revision),
        createMeta(tr("source"), data.source?.title || memory.provenance?.source_id || "—"),
        createMeta(
          tr("status"),
          memory.hidden ? tr("hideLabel") : tr("visibleLabel"),
        ),
      );
      panel.append(meta);
      if (Array.isArray(memory.source_records) && memory.source_records.length) {
        const records = document.createElement("section");
        records.className = "source-records";
        const heading = document.createElement("h3");
        text(heading, tr("sourceRecords"));
        records.append(heading);
        memory.source_records.forEach((record) => {
          const entry = contentBlock(record.text, record.title || record.kind || tr("source"));
          const details = document.createElement("div");
          details.className = "meta-grid";
          details.append(
            createMeta(tr("sourceKind"), record.kind),
            createMeta(tr("sourceLocator"), record.locator || "—"),
            createMeta(tr("observedAt"), date(record.observed_at)),
            createMeta("ID", record.id),
          );
          entry.append(details);
          records.append(entry);
        });
        panel.append(records);
      }
      const source = data.source || memory.source;
      const provenance = memory.provenance || {};
      if (source && typeof source === "object") {
        const sourceMeta = document.createElement("div");
        sourceMeta.className = "meta-grid";
        sourceMeta.append(
          createMeta(
            tr("source"),
            provenance.source_id || source.id || "—",
          ),
          createMeta(
            tr("created"),
            date(provenance.observed_at || source.created_at),
          ),
          createMeta(lang === "zh" ? "证据等级" : "Evidence level",
            provenance.level === "explicit_statement" ? (lang === "zh" ? "明确陈述 · 未验证" : "Explicit statement · Unverified") : provenance.level),
          createMeta(lang === "zh" ? "记录者" : "Recorded by", provenance.created_by),
        );
        panel.append(sourceMeta);
        if (source.safe_text || source.text)
          panel.append(
            contentBlock(source.safe_text || source.text, tr("originalProvenance")),
          );
      } else if (
        memory.provenance ||
        memory.source_text ||
        memory.original_text
      )
        panel.append(
          contentBlock(
            memory.provenance || memory.source_text || memory.original_text,
            tr("provenance"),
          ),
        );
    } catch (e) {
      notice(errorMessage(e, "loadFailed"));
    } finally {
      panel.removeAttribute("aria-busy");
    }
  }
  async function refreshMemory(query = $("#memory-search").value.trim()) {
    const revision = ++searchRevision;
    const data = query
      ? await api("memories", { query, mode: "keyword", limit: recentLimit })
      : await api("library", { offset: 0, limit: recentLimit });
    if (revision !== searchRevision) return;
    memories = Array.isArray(data.items) ? data.items : [];
    if (!query) renderStats({ memories: data.total || 0 });
    renderMemoryList();
    if (selectedMemory && currentView === "memory") {
      const id = memoryId(selectedMemory);
      if (memories.some((m) => String(memoryId(m)) === String(id)))
        await selectMemory(id);
      else {
        $("#memory-detail").replaceChildren();
        selectedMemory = null;
      }
    }
  }
  async function loadQueue() {
    const data = await api("queue");
    candidates = Array.isArray(data.items) ? data.items : [];
    renderStats({ candidates: candidates.length });
    const list = $("#review-list");
    if (!candidates.length) {
      emptyList(list, tr("noCandidates"), tr("noCandidatesSub"));
      $("#review-detail").replaceChildren();
      selectedCandidate = null;
      return;
    }
    list.replaceChildren(
      ...candidates.map((item) =>
        card(
          item,
          String(candidateId(item)) === String(selectedCandidate?.id),
          "candidate",
        ),
      ),
    );
    if (
      selectedCandidate &&
      !candidates.some(
        (i) => String(candidateId(i)) === String(selectedCandidate.id),
      )
    )
      selectedCandidate = null;
    if (!selectedCandidate) $("#review-detail").replaceChildren();
  }
  async function selectCandidate(id) {
    const item = candidates.find((c) => String(candidateId(c)) === String(id));
    if (!item) return;
    selectedCandidate = { ...item, id };
    await loadQueue();
    const panel = $("#review-detail");
    panel.setAttribute("aria-busy", "true");
    try {
      const data = await api("inspect", { id });
      const candidate = data.candidate || item;
      selectedCandidate = { ...candidate, id: candidateId(candidate) || id };
      const edit = document.createElement("textarea");
      edit.className = "review-edit";
      edit.id = "candidate-edit";
      edit.value = contentOf(candidate);
      edit.setAttribute("aria-label", tr("editedContent"));
      const label = document.createElement("label");
      label.className = "edit-label";
      label.htmlFor = "candidate-edit";
      text(label, tr("editedContent"));
      const targetEdit = Boolean(candidate.target_memory_id);
      const titleInput = document.createElement("input");
      titleInput.type = "text";
      titleInput.className = "text-input";
      titleInput.id = "candidate-title";
      titleInput.value = candidate.proposed_title ?? data.source?.title ?? "";
      const originalTitle = titleInput.value;
      const titleLabel = document.createElement("label");
      titleLabel.className = "edit-label";
      titleLabel.htmlFor = titleInput.id;
      text(titleLabel, tr("proposedTitle"));
      const sourcesInput = document.createElement("textarea");
      sourcesInput.className = "review-edit source-records-edit";
      sourcesInput.id = "candidate-sources";
      sourcesInput.value = JSON.stringify(candidate.proposed_sources || [], null, 2);
      const originalSources = sourcesInput.value;
      const sourcesLabel = document.createElement("label");
      sourcesLabel.className = "edit-label";
      sourcesLabel.htmlFor = sourcesInput.id;
      text(sourcesLabel, tr("proposedSources"));
      const sourcesError = document.createElement("p");
      sourcesError.className = "form-error";
      sourcesError.setAttribute("role", "status");
      function parseSources() {
        const value = JSON.parse(sourcesInput.value);
        if (!Array.isArray(value) || value.some((record) => !record || typeof record !== "object" || Array.isArray(record)))
          throw new Error(tr("invalidSources"));
        return value;
      }
      const actions = [
        {
          label: tr("reject"),
          cls: "danger-button",
          run: () =>
            confirmAction(
              "rejectTitle",
              "rejectCopy",
              "confirmReject",
              async () => {
                await api("reject", { id });
                toast(tr("rejectedSuccess"));
                selectedCandidate = null;
                await loadQueue();
              },
            ),
        },
        {
          label: tr("approve"),
          cls: "primary-button",
          run: () =>
            confirmAction(
              "approveTitle",
              "approveCopy",
              "confirmApprove",
              async () => {
                await api("approve", { id, revision: candidate.revision });
                toast(tr("approvedSuccess"));
                selectedCandidate = null;
                await Promise.all([refreshMemory(), loadQueue()]);
              },
            ),
        },
      ];
      panel.replaceChildren(detailHeader(candidate, actions));
      const metadata = document.createElement("p");
      metadata.className = "edit-label";
      text(metadata, `${tr("source")}: ${data.source?.origin || "unknown"} | ${tr("evidenceLevel")}: ${candidate.suggested_fact_level || "unknown"}`);
      panel.append(metadata);
      if (targetEdit) {
        const target = document.createElement("div");
        target.className = "meta-grid";
        target.append(
          createMeta(tr("targetMemory"), candidate.target_memory_id),
          createMeta(tr("baseRevision"), candidate.expected_memory_revision),
        );
        panel.append(target, titleLabel, titleInput);
      }
      panel.append(label, edit);
      if (targetEdit) panel.append(sourcesLabel, sourcesInput, sourcesError);
      const commands = document.createElement("div");
      commands.className = "review-commands";
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "danger-button spacer";
      text(revoke, tr("revoke"));
      revoke.addEventListener("click", () =>
        confirmAction(
          "revokeTitle",
          "revokeCopy",
          "confirmRevoke",
          async () => {
            await api("revoke", {
              id: candidate.source_id,
              kind: "intake_source",
            });
            toast(tr("revokedSuccess"));
            selectedCandidate = null;
            await loadQueue();
          },
        ),
      );
      const save = document.createElement("button");
      save.type = "button";
      save.className = "secondary-button";
      text(save, tr("saveEdit"));
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          const payload = { id, text: edit.value };
          if (targetEdit) {
            payload.title = titleInput.value;
            payload.sources = parseSources();
          }
          const updated = await api("edit", payload);
          selectedCandidate = updated.candidate || updated;
          toast(tr("savedSuccess"));
          await loadQueue();
          await selectCandidate(id);
        } catch (e) {
          notice(errorMessage(e));
        } finally {
          updateDirtyState();
        }
      });
      function updateDirtyState() {
        const dirty = edit.value !== contentOf(candidate) || (targetEdit &&
          (titleInput.value !== originalTitle || sourcesInput.value !== originalSources));
        let valid = true;
        if (targetEdit) {
          try { parseSources(); } catch { valid = false; }
          text(sourcesError, valid ? "" : tr("invalidSources"));
          sourcesInput.setAttribute("aria-invalid", String(!valid));
        }
        panel.querySelector(".primary-button").disabled = dirty || !valid;
        save.disabled = !dirty || !valid;
        text(label, dirty ? tr("unsaved") : tr("editedContent"));
      }
      [edit, titleInput, sourcesInput].forEach((field) => field.addEventListener("input", updateDirtyState));
      updateDirtyState();
      commands.append(revoke, save);
      panel.append(commands);
      if (data.source || data.history) {
        const meta = document.createElement("div");
        meta.className = "meta-grid";
        const source = data.source || {};
        meta.append(
          createMeta(
            tr("source"),
            source.source_id || source.id || source.title || source.kind || "—",
          ),
          createMeta(
            tr("created"),
            date(source.observed_at || candidate.created_at),
          ),
          createMeta(tr("status"), source.created_by || source.level || "—"),
        );
        panel.append(meta);
        if (source.safe_text || source.text || source.original_text)
          panel.append(
            contentBlock(
              source.safe_text || source.text || source.original_text,
              tr("provenance"),
            ),
          );
      }
    } catch (e) {
      notice(errorMessage(e, "loadFailed"));
    } finally {
      panel.removeAttribute("aria-busy");
    }
  }
  function showView(name) {
    currentView = name;
    clearNotice();
    $$(".view").forEach((v) =>
      v.classList.toggle("hidden", v.id !== `${name}-view`),
    );
    $$(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === name ||
        (name === "library" && b.dataset.view === "memory")),
    );
    $("#view-title").textContent = tr(
      name === "memory"
        ? "memoryTitle"
        : name === "review"
          ? "reviewTitle"
          : name === "library" ? "libraryTitle" : "importTitle",
    );
    $(".sidebar").classList.remove("open");
    if (name === "music") {
      $("#view-title").textContent = "音乐库";
      document.dispatchEvent(new Event("shiros-music-open"));
    }
    if (name === "files") {
      $("#view-title").textContent = "文件库";
      document.dispatchEvent(new Event("shiros-files-open"));
    }
    if (name === "memory")
      refreshMemory().catch((e) => notice(errorMessage(e, "loadFailed")));
    if (name === "review")
      loadQueue().catch((e) => notice(errorMessage(e, "loadFailed")));
    if (name === "library")
      loadTags().then(loadLibrary).then(() => {
        if (currentView === "library" && selectedMemory && libraryItems.some((item) =>
          String(memoryId(item)) === String(selectedMemory.id)))
          return selectMemory(selectedMemory.id, "library");
      }).catch((e) => notice(errorMessage(e, "loadFailed")));
  }
  function tagPath(tag) {
    const names = [], seen = new Set();
    while (tag && !seen.has(tag.id)) {
      names.unshift(tag.name);
      seen.add(tag.id);
      tag = tags.find((t) => t.id === tag.parent_id);
    }
    return names.join(" / ");
  }
  async function loadTags() {
    const data = await api("tags");
    tags = data.items || [];
    renderTagTree();
  }
  function renderTagTree() {
    const tree = $("#tag-tree");
    const scroll = tree.scrollTop;
    const query = $("#tag-search").value.trim().toLocaleLowerCase();
    const matching = matchingTags(query);
    tree.classList.toggle("hidden", libraryMode !== "tags");
    $("#tag-filter").classList.toggle("hidden", libraryMode !== "tags");
    tree.replaceChildren();
    const seen = new Set();
    function append(parent, target) {
      tags.filter((t) => (t.parent_id || null) === parent).forEach((tag) => {
        if (seen.has(tag.id)) return;
        seen.add(tag.id);
        if (query && !matching.has(tag.id)) return;
        const row = document.createElement("div");
        row.className = "tag-row";
        const select = document.createElement("button");
        select.type = "button";
        select.className = `tag-select${libraryTag === tag.id ? " selected" : ""}`;
        select.setAttribute("aria-pressed", String(libraryTag === tag.id));
        text(select, tag.name);
        select.title = tagPath(tag);
        select.addEventListener("click", () => {
          libraryTag = tag.id;
          libraryOffset = 0;
          loadLibrary().catch((e) => notice(errorMessage(e)));
        });
        const children = document.createElement("div");
        children.className = "tag-children";
        const hasChildren = tags.some((t) => t.parent_id === tag.id);
        const expanded = Boolean(query) || expandedTags.has(tag.id);
        children.classList.toggle("hidden", !expanded);
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "tag-toggle";
        text(toggle, hasChildren ? expanded ? "▾" : "▸" : "·");
        toggle.disabled = !hasChildren;
        toggle.setAttribute("aria-label", tag.name);
        if (hasChildren) toggle.setAttribute("aria-expanded", String(expanded));
        toggle.addEventListener("click", () => {
          const collapsed = children.classList.toggle("hidden");
          if (collapsed) expandedTags.delete(tag.id); else expandedTags.add(tag.id);
          text(toggle, collapsed ? "▸" : "▾");
          toggle.setAttribute("aria-expanded", String(!collapsed));
        });
        row.append(toggle, select);
        target.append(row, children);
        append(tag.id, children);
      });
    }
    append(null, tree);
    if (!tags.length) emptyList(tree, tr("noTags"), "", {label: tr("newTag"), run: openTagDialog});
    else if (!tree.childElementCount) emptyList(tree, tr("noTagMatch"), "");
    tree.scrollTop = scroll;
  }
  function matchingTags(query) {
    const matching = new Set();
    for (const tag of tags) {
      if (!tagPath(tag).toLocaleLowerCase().includes(query)) continue;
      let current = tag;
      const seen = new Set();
      while (current && !seen.has(current.id)) {
        seen.add(current.id);
        matching.add(current.id);
        current = tags.find((t) => t.id === current.parent_id);
      }
    }
    return matching;
  }
  $("#memory-untagged").addEventListener("click", () => {libraryTag="__none__";libraryOffset=0;loadLibrary().catch((e)=>notice(errorMessage(e)));});
  $("#tag-search").addEventListener("input", renderTagTree);
  function tagPicker(id, assigned, context) {
    const section = document.createElement("section");
    section.className = "tag-editor source-block";
    const heading = document.createElement("h3");
    text(heading, tr("tags"));
    const choices = document.createElement("div");
    choices.className = "tag-choices";
    const selected = tagDrafts.get(id) || new Set(assigned.map((tag) => typeof tag === "string" ? tag : tag.id));
    for (const tagId of selected) if (!tags.some((tag) => tag.id === tagId)) selected.delete(tagId);
    const expanded = pickerExpansion.get(id) || new Set();
    if (!pickerExpansion.has(id)) {
      for (const tagId of selected) {
        let tag = tags.find((t) => t.id === tagId);
        const seen = new Set();
        while (tag?.parent_id && !seen.has(tag.id)) {
          seen.add(tag.id); expanded.add(tag.parent_id);
          tag = tags.find((t) => t.id === tag.parent_id);
        }
      }
      pickerExpansion.set(id, expanded);
    }
    const search = document.createElement("input");
    search.type = "search";
    search.className = "text-input tag-picker-search";
    search.placeholder = tr("findTags");
    search.setAttribute("aria-label", tr("findTags"));
    const summary = document.createElement("div");
    summary.className = "tag-selection-summary";
    const picked = document.createElement("div");
    picked.className = "tag-picked";
    function updateSummary() {
      text(summary, `${tr("selectedTags")} · ${selected.size} / 30`);
      picked.replaceChildren();
      for (const tagId of selected) {
        const tag = tags.find((t) => t.id === tagId);
        const chip = document.createElement("button");
        chip.type = "button"; chip.className = "tag-chip";
        text(chip, `${tagPath(tag)} ×`);
        chip.title = `${tr("removeTagSelection")} ${tagPath(tag)}`;
        chip.addEventListener("click", () => { selected.delete(tagId); tagDrafts.set(id, selected); renderChoices(); });
        picked.append(chip);
      }
    }
    function renderChoices() {
      const scroll = choices.scrollTop;
      choices.replaceChildren();
      const query = search.value.trim().toLocaleLowerCase();
      const matching = matchingTags(query), seen = new Set();
      function append(parent, target) {
        tags.filter((t) => (t.parent_id || null) === parent).forEach((tag) => {
          if (seen.has(tag.id) || (query && !matching.has(tag.id))) return;
          seen.add(tag.id);
          const row = document.createElement("div"); row.className = "tag-row";
          const children = document.createElement("div"); children.className = "tag-children";
          const hasChildren = tags.some((t) => t.parent_id === tag.id);
          const open = Boolean(query) || expanded.has(tag.id);
          children.classList.toggle("hidden", !open);
          const toggle = document.createElement("button");
          toggle.type = "button"; toggle.className = "tag-toggle"; toggle.disabled = !hasChildren;
          text(toggle, hasChildren ? open ? "▾" : "▸" : "·");
          toggle.setAttribute("aria-label", tag.name);
          if (hasChildren) toggle.setAttribute("aria-expanded", String(open));
          toggle.addEventListener("click", () => {
            const collapsed = children.classList.toggle("hidden");
            if (collapsed) expanded.delete(tag.id); else expanded.add(tag.id);
            text(toggle, collapsed ? "▸" : "▾"); toggle.setAttribute("aria-expanded", String(!collapsed));
          });
          const label = document.createElement("label"); label.title = tagPath(tag);
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox"; checkbox.value = tag.id; checkbox.checked = selected.has(tag.id);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked && selected.size >= 30) { checkbox.checked = false; return; }
            if (checkbox.checked) selected.add(tag.id); else selected.delete(tag.id);
            tagDrafts.set(id, selected); updateSummary();
          });
          const caption = document.createElement("span"); text(caption, tag.name);
          label.append(checkbox, caption); row.append(toggle, label); target.append(row, children);
          append(tag.id, children);
        });
      }
      append(null, choices);
      if (!choices.childElementCount) text(choices, tr(tags.length ? "noTagMatch" : "noTags"));
      choices.scrollTop = scroll; updateSummary();
    }
    search.addEventListener("input", renderChoices);
    renderChoices();
    const actions = document.createElement("div");
    actions.className = "tag-actions";
    const noTagsButton=document.createElement('button');noTagsButton.type='button';noTagsButton.className='secondary-button';text(noTagsButton,'无标签');noTagsButton.addEventListener('click',()=>{selected.clear();tagDrafts.set(id,selected);renderChoices();});actions.append(noTagsButton);
    const save = document.createElement("button");
    save.className = "secondary-button";
    save.type = "button";
    text(save, tr("saveTags"));
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        await api("set-memory-tags", { id, tag_ids: [...selected] });
        tagDrafts.delete(id);
        toast(tr("tagsSaved"));
        if (context === "library") await loadLibrary();
      } catch (e) { notice(errorMessage(e)); }
      finally { save.disabled = false; }
    });
    const manage = document.createElement("button");
    manage.className = "text-button";
    manage.type = "button";
    text(manage, tr("manageTags"));
    manage.addEventListener("click", openTagDialog);
    const clear = document.createElement("button");
    clear.type = "button"; clear.className = "text-button"; text(clear, tr("clearTags"));
    clear.addEventListener("click", () => { selected.clear(); tagDrafts.set(id, selected); renderChoices(); });
    actions.append(save, manage, clear);
    section.append(heading, search, summary, picked, choices, actions);
    return section;
  }
  function renderLibraryList() {
    const list = $("#library-list");
    list.replaceChildren();
    if (!libraryItems.length) {
      emptyList(list, libraryMode === "tags" && !libraryTag ? tr("chooseTag") : tr("noMemories"), "");
      return;
    }
    let lastDate = "";
    for (const item of libraryItems) {
      if (libraryMode === "date") {
        const d = new Date(item.added_at || item.created_at);
        const key = Number.isNaN(d.valueOf()) ? "—" : d.toLocaleDateString(lang === "zh" ? "zh-CN" : "en");
        if (key !== lastDate) {
          const heading = document.createElement("h3");
          heading.className = "date-divider";
          text(heading, key);
          list.append(heading);
          lastDate = key;
        }
      }
      list.append(card(item, String(memoryId(item)) === String(selectedMemory?.id), "library"));
    }
  }
  async function loadLibrary() {
    const revision = ++libraryRevision;
    const selectedTag = tags.find((tag) => tag.id === libraryTag);
    text($("#library-location"), libraryMode === "tags" ? libraryTag === "__none__" ? "无标签" : selectedTag ? `${tagPath(selectedTag)} · ${tr("includesChildren")}` : tr("chooseTag") : tr("allMemories"));
    renderTagTree();
    const data = libraryMode === "tags" && !libraryTag ? {items: [], total: 0} :
      await api("library", { tag_id: libraryMode === "tags" && libraryTag !== "__none__" ? libraryTag : null, untagged: libraryMode === "tags" && libraryTag === "__none__", offset: libraryOffset, limit: libraryLimit });
    if (revision !== libraryRevision) return;
    libraryItems = data.items || [];
    libraryTotal = data.total || 0;
    if (libraryMode === "date") renderStats({ memories: libraryTotal });
    renderLibraryList();
    text($("#library-result-meta"), `${libraryTotal} ${tr("searchCount")}`);
    text($("#library-page"), `${libraryTotal ? libraryOffset + 1 : 0}–${Math.min(libraryOffset + libraryItems.length, libraryTotal)} / ${libraryTotal}`);
    $("#library-prev").disabled = libraryOffset === 0;
    $("#library-next").disabled = libraryOffset + libraryLimit >= libraryTotal;
    if (!libraryItems.some((item) => String(memoryId(item)) === String(selectedMemory?.id)))
      $("#library-detail").replaceChildren();
  }
  function populateTagForm() {
    const id = $("#tag-existing").value;
    const tag = tags.find((t) => t.id === id);
    $("#tag-name").value = tag?.name || "";
    const excluded = new Set(id ? [id] : []);
    for (let i = 0; i < tags.length; i++)
      tags.forEach((t) => { if (excluded.has(t.parent_id)) excluded.add(t.id); });
    $("#tag-parent").replaceChildren(new Option(tr("rootTag"), ""),
      ...tags.filter((t) => !excluded.has(t.id)).map((t) => new Option(tagPath(t), t.id)));
    $("#tag-parent").value = tag?.parent_id || "";
    text($("#tag-error"), "");
  }
  async function openTagDialog() {
    try {
      await loadTags();
      $("#tag-existing").replaceChildren(new Option(tr("newTag"), ""), ...tags.map((tag) => new Option(tagPath(tag), tag.id)));
      populateTagForm();
      $("#tag-dialog").showModal();
    } catch (e) { notice(errorMessage(e)); }
  }
  $("#manage-tags").addEventListener("click", openTagDialog);
  $("#tag-dialog-close").addEventListener("click", () => $("#tag-dialog").close());
  $("#tag-existing").addEventListener("change", populateTagForm);
  $("#tag-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = $("#tag-save");
    save.disabled = true;
    try {
      const id = $("#tag-existing").value;
      await api(id ? "tag-update" : "tag-create", { ...(id ? {id} : {}), name: $("#tag-name").value.trim(), parent_id: $("#tag-parent").value || null });
      await loadTags();
      $("#tag-dialog").close();
      toast(tr("tagsSaved"));
      if (selectedMemory) await selectMemory(selectedMemory.id, currentView === "library" ? "library" : "memory");
    } catch (e) { text($("#tag-error"), errorMessage(e, "tagInvalid")); }
    finally { save.disabled = false; }
  });
  $$("[data-library-mode]").forEach((button) => button.addEventListener("click", () => {
    libraryMode = button.dataset.libraryMode;
    libraryOffset = 0;
    $$("[data-library-mode]").forEach((b) => {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    });
    loadLibrary().catch((e) => notice(errorMessage(e)));
  }));
  for (const [selector, delta] of [["#library-prev", -1], ["#library-next", 1]])
    $(selector).addEventListener("click", () => {
      libraryOffset = Math.max(0, libraryOffset + delta * libraryLimit);
      loadLibrary().catch((e) => notice(errorMessage(e)));
    });
  function confirmAction(titleKey, copyKey, confirmKey, action) {
    const dialog = $("#confirm-dialog");
    text($("#dialog-title"), tr(titleKey));
    text($("#dialog-copy"), tr(copyKey));
    const confirm = $("#dialog-confirm");
    text(confirm, tr(confirmKey));
    dialog.showModal();
    const handler = async (event) => {
      if (event.submitter?.value !== "confirm") return;
      event.preventDefault();
      confirm.disabled = true;
      try {
        await action();
        await loadSession();
        dialog.close();
      } catch (e) {
        notice(errorMessage(e));
      } finally {
        confirm.disabled = false;
      }
    };
    dialog.addEventListener("submit", handler);
    dialog.addEventListener(
      "close",
      () => dialog.removeEventListener("submit", handler),
      { once: true },
    );
  }
  function countChars() {
    text($("#char-count"), `${$("#import-text").value.length} / 50000`);
    $("#stage-button").disabled = !previewId;
  }
  function importKind(file) {
    const ext = file.name.split(".").pop().toLowerCase();
    return (
      { txt: "text", md: "markdown", json: "json", csv: "csv" }[ext] || "text"
    );
  }
  function renderPreview(data) {
    const body = $("#preview-body"),
      h = document.createElement("h3"),
      label = document.createElement("span"),
      pre = document.createElement("pre"),
      safe =
        data.privacy?.safe_text ?? data.safe_text ?? data.normalized_text ?? "";
    text(h, data.title || $("#import-title").value.trim());
    text(label, tr("safeTextLabel"));
    label.className = "meta-label";
    text(pre, safe);
    body.replaceChildren(h, label, pre);
    const stats = document.createElement("div");
    stats.className = "preview-stats";
    const a = document.createElement("span");
    text(a, `${safe.length} ${tr("chars")}`);
    const b = document.createElement("span");
    text(
      b,
      `${tr("privacySafe")}: ${data.privacy?.persistence_allowed === false ? tr("blocked") : tr("allowed")}`,
    );
    stats.append(a, b);
    body.append(stats);
    const allowed = data.privacy?.persistence_allowed !== false;
    previewId = allowed ? data.id : null;
    $("#stage-button").disabled = !allowed || !previewId;
    text(
      $("#preview-state"),
      allowed ? tr("previewSuccess") : tr("notEligible"),
    );
  }
  async function previewImport() {
    const title = $("#import-title").value.trim(),
      content = $("#import-text").value;
    if (!title) {
      toast(tr("emptyTitle"));
      $("#import-title").focus();
      return;
    }
    if (!content.trim()) {
      toast(tr("emptyText"));
      $("#import-text").focus();
      return;
    }
    const button = $("#preview-button");
    button.disabled = true;
    try {
      const file = $("#import-file").files[0];
      const data = await api("preview", {
        title,
        text: content,
        kind: file ? importKind(file) : "text",
      });
      previewId = data.id;
      renderPreview(data);
      countChars();
    } catch (e) {
      notice(errorMessage(e, "previewFailed"));
    } finally {
      button.disabled = false;
    }
  }
  async function stageImport() {
    if (!previewId) {
      toast(tr("previewFirst"));
      return;
    }
    const button = $("#stage-button");
    button.disabled = true;
    try {
      const result = await api("stage", { id: previewId });
      const approved = result.status === "approved";
      toast(tr(approved ? "autoApproved" : "stageSuccess"));
      previewId = null;
      $("#import-title").value = "";
      $("#import-text").value = "";
      $("#file-name").textContent = "";
      $("#import-file").value = "";
      $("#preview-body").replaceChildren();
      const p = document.createElement("p");
      p.className = "muted";
      text(p, tr("previewHelp"));
      $("#preview-body").append(p);
      text($("#preview-state"), tr("notPreviewed"));
      countChars();
      await loadQueue();
      showView(approved ? "memory" : "review");
    } catch (e) {
      notice(errorMessage(e));
      button.disabled = false;
    }
  }
  $("#language-toggle").addEventListener("click", () => {
    lang = lang === "zh" ? "en" : "zh";
    localStorage.setItem("shiros-locale", lang);
    applyLanguage();
    renderMemoryList();
    if (selectedMemory) selectMemory(memoryId(selectedMemory), currentView === "library" ? "library" : "memory");
    if (currentView === "review") loadQueue();
    if (currentView === "library") loadLibrary().catch((e) => notice(errorMessage(e)));
  });
  $$(".nav-item").forEach((button) =>
    button.addEventListener("click", () => showView(button.dataset.view)),
  );
  $("#go-import").addEventListener("click", () => showView("import"));
  $("#expand-memory").addEventListener("click", () => {
    libraryMode = "date";
    libraryTag = null;
    libraryOffset = 0;
    $$("[data-library-mode]").forEach((button) => {
      const active = button.dataset.libraryMode === libraryMode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    showView("library");
  });
  $("#collapse-memory").addEventListener("click", () => {
    $("#memory-search").value = "";
    showView("memory");
  });
  $("#mobile-nav-toggle").addEventListener("click", () =>
    $(".sidebar").classList.toggle("open"),
  );
  $("#memory-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(
      () => refreshMemory().catch((e) => notice(errorMessage(e, "loadFailed"))),
      220,
    );
  });
  $("#load-example").addEventListener("click", () => {
    if (lang === "zh") {
      $("#import-title").value = "写作偏好";
      $("#import-text").value =
        "我偏好清晰、直接的表达。技术说明先给结论，再补充必要背景。列表保持精简，避免重复的信息。";
    } else {
      $("#import-title").value = "Writing preferences";
      $("#import-text").value =
        "I prefer clear, direct writing. Technical explanations should lead with the conclusion, then add the context needed to act. Keep lists concise and avoid repeating information.";
    }
    previewId = null;
    $("#preview-state").textContent = tr("notPreviewed");
    text($("#file-name"), "");
    $("#import-file").value = "";
    countChars();
  });
  $("#import-text").addEventListener("input", () => {
    previewId = null;
    countChars();
  });
  $("#import-title").addEventListener("input", () => {
    previewId = null;
    countChars();
  });
  $("#preview-button").addEventListener("click", previewImport);
  $("#stage-button").addEventListener("click", stageImport);
  $("#import-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (!/\.(txt|md|json|csv)$/i.test(file.name)) {
      toast(tr("fileInvalid"));
      event.target.value = "";
      return;
    }
    if (file.size > 65536) {
      toast(tr("fileTooLarge"));
      event.target.value = "";
      return;
    }
    try {
      const bytes = await file.arrayBuffer();
      const content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      if (content.length > 50000) {
        toast(tr("fileTooLarge"));
        event.target.value = "";
        return;
      }
      $("#import-text").value = content;
      const title = file.name.replace(/\.[^.]+$/, "").slice(0, 200);
      $("#import-title").value = title;
      text($("#file-name"), file.name);
      previewId = null;
      countChars();
    } catch (_) {
      toast(tr("fileInvalid"));
    }
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "/" &&
      !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) &&
      currentView === "memory"
    ) {
      event.preventDefault();
      $("#memory-search").focus();
    }
    if (event.key === "Escape") $(".sidebar").classList.remove("open");
  });
  applyLanguage();
  countChars();
  loadSession().then(() =>
    refreshMemory().catch((e) => notice(errorMessage(e, "loadFailed"))),
  );
})();
