(() => {
  'use strict';

  const state = {
    config: {
      apiBase: '',
      configPath: '/api/config',
      healthPath: '/api/health',
      modelsPath: '/api/models',
      chatPath: '/api/chat',
      localModels: [],
      parameters: { temperature: 0.2, topP: 0.9, maxOutputTokens: 512, maxContextTokens: 4096, retrievalLimit: 5, thinking: true },
      systemPrompt: '',
      hardware: null,
      downloadableModels: []
    },
    messages: [],
    context: null
  };

  const els = {
    chat: document.querySelector('#chat'),
    form: document.querySelector('#promptForm'),
    prompt: document.querySelector('#prompt'),
    send: document.querySelector('#sendButton'),
    model: document.querySelector('#modelSelect'),
    library: document.querySelector('#libraryToggle'),
    libraryQuick: document.querySelector('#libraryToggleQuick'),
    thinking: document.querySelector('#thinkingToggle'),
    clear: document.querySelector('#clearButton'),
    status: document.querySelector('#serviceStatus'),
    error: document.querySelector('#errorBox'),
    modelFolder: document.querySelector('#modelFolder'),
    settingsButton: document.querySelector('#settingsButton'),
    settingsPanel: document.querySelector('#settingsPanel'),
    hardwareNotice: document.querySelector('#hardwareNotice'),
    modelDownloadPanel: document.querySelector('#modelDownloadPanel'),
    downloadModelButton: document.querySelector('#downloadModelButton'),
    modelDownloadStatus: document.querySelector('#modelDownloadStatus'),
    engineLabel: document.querySelector('#engineLabel'),
    checkUpdatesButton: document.querySelector('#checkUpdatesButton'),
    applyUpdateButton: document.querySelector('#applyUpdateButton'),
    restartButton: document.querySelector('#restartButton'),
    updateStatus: document.querySelector('#updateStatus'),
    systemPrompt: document.querySelector('#systemPrompt'),
    contextBar: document.querySelector('#contextBar'),
    contextText: document.querySelector('#contextText'),
    performance: document.querySelector('#performanceText'),
    modelQuickLabel: document.querySelector('#modelQuickLabel'),
    parameterInputs: {
      temperature: document.querySelector('#temperature'),
      topP: document.querySelector('#topP'),
      maxOutputTokens: document.querySelector('#maxOutputTokens'),
      maxContextTokens: document.querySelector('#maxContextTokens'),
      retrievalLimit: document.querySelector('#retrievalLimit')
    }
  };

  const storageKey = 'offlineai.session.v1';
  const mountPrefix = String(window.OFFLINEAI_BASE || '').replace(/\/$/, '');
  const text = value => String(value ?? '');
  const escapeHtml = value => text(value).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));

  function endpoint(path) { return `${state.config.apiBase || mountPrefix}${path}`; }
  function showError(message) { els.error.textContent = message; els.error.hidden = !message; }
  function setStatus(label, kind) { els.status.className = `status status-${kind}`; els.status.innerHTML = `<span></span>${escapeHtml(label)}`; }

  function inlineMarkdown(value) {
    let html = value;
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
      const safe = /^(https?:\/\/|mailto:|#)/i.test(url);
      return safe ? `<a href="${url}" target="_blank" rel="noreferrer">${label}</a>` : label;
    });
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
    html = html.replace(/(?<!_)_([^_]+)_(?!_)/g, '<em>$1</em>');
    return html;
  }

  function renderMarkdown(value) {
    const lines = escapeHtml(value).split(/\r?\n/);
    const output = [];
    let inCode = false;
    let code = [];
    let list = null;
    const closeList = () => { if (list) { output.push(`</${list}>`); list = null; } };
    for (const line of lines) {
      if (line.trim().startsWith('```')) {
        if (inCode) { output.push(`<pre><code>${code.join('\n')}</code></pre>`); code = []; inCode = false; }
        else { closeList(); inCode = true; }
        continue;
      }
      if (inCode) { code.push(line); continue; }
      if (!line.trim()) { closeList(); continue; }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) { closeList(); const level = heading[1].length; output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`); continue; }
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
      if (unordered || ordered) {
        const type = unordered ? 'ul' : 'ol';
        if (list !== type) { closeList(); output.push(`<${type}>`); list = type; }
        output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`); continue;
      }
      const quote = line.match(/^\s*&gt;\s?(.+)$/);
      if (quote) { closeList(); output.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`); continue; }
      closeList(); output.push(`<p>${inlineMarkdown(line)}</p>`);
    }
    if (inCode) output.push(`<pre><code>${code.join('\n')}</code></pre>`);
    closeList();
    return output.join('');
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.error || `Service returned HTTP ${response.status}`);
    return body;
  }

  async function loadConfig() {
    try {
      const local = await request('config.json', { headers: {} });
      state.config = { ...state.config, ...local };
    } catch (_) {}
    try {
      const backend = await request(endpoint(state.config.configPath || '/api/config'));
      state.config = { ...state.config, ...backend };
      if (backend.system_prompt) state.config.systemPrompt = backend.system_prompt;
      if (Array.isArray(backend.models)) state.config.localModels = backend.models;
      if (Array.isArray(backend.downloadable_models)) state.config.downloadableModels = backend.downloadable_models;
    } catch (_) {}
  }

  function modelId(model) { return typeof model === 'string' ? model : (model?.id || model?.name || ''); }
  function modelLabel(model) { return typeof model === 'string' ? model : (model?.label || model?.id || model?.name || ''); }

  function normalizeContext(value) {
    if (!value) return null;
    return {
      used: Number(value.used ?? value.estimated_used_tokens ?? 0),
      maximum: Number(value.maximum ?? value.configured_context_limit ?? 0),
      percentage: Number(value.percentage ?? NaN),
      skipped: Boolean(value.library_search_skipped),
      reason: value.skip_reason || ''
    };
  }

  function performanceLabel(value) {
    if (!value) return '';
    const parts = [];
    if (Number.isFinite(Number(value.tokens_per_second)) && Number(value.tokens_per_second) > 0) parts.push(`${Number(value.tokens_per_second).toFixed(1)} output tokens/s`);
    if (Number.isFinite(Number(value.elapsed_seconds)) && Number(value.elapsed_seconds) > 0) parts.push(`${Number(value.elapsed_seconds).toFixed(1)}s`);
    if (Number.isFinite(Number(value.output_tokens))) parts.push(`${Number(value.output_tokens).toLocaleString()} output tokens`);
    if (Number.isFinite(Number(value.prompt_tokens))) parts.push(`${Number(value.prompt_tokens).toLocaleString()} prompt tokens`);
    return parts.length ? `Performance · ${parts.join(' · ')}` : '';
  }

  function updateContextMeter() {
    const max = Number(state.context?.maximum || els.parameterInputs.maxContextTokens.value || 0);
    const fallbackUsed = Math.ceil((state.messages.reduce((total, item) => total + text(item.content).length, 0) + text(els.prompt.value).length) / 4);
    const used = Number(state.context?.used || fallbackUsed);
    const suppliedPercentage = Number(state.context?.percentage);
    const percentage = Number.isFinite(suppliedPercentage) ? Math.min(100, Math.max(0, suppliedPercentage)) : (max ? Math.min(100, used / max * 100) : 0);
    els.contextBar.style.width = `${percentage}%`;
    els.contextBar.className = percentage >= 90 ? 'danger' : percentage >= 75 ? 'warn' : '';
    els.contextText.textContent = `Context: ${used.toLocaleString()} / ${max.toLocaleString()} tokens (${Math.round(percentage)}%)`;
  }

  function render() {
    els.chat.innerHTML = '';
    if (!state.messages.length) {
      els.chat.innerHTML = '<div class="empty">Your conversation will appear here.<br>Library sources will appear only when the question needs them.</div>';
      updateContextMeter();
      return;
    }
    state.messages.forEach(message => {
      const node = document.createElement('article');
      node.className = `message ${message.role}${message.pending ? ' pending' : ''}`;
      const body = message.role === 'assistant' && !message.pending ? renderMarkdown(message.content) : `<p>${escapeHtml(message.content)}</p>`;
      node.innerHTML = `<div class="message-meta">${message.role === 'user' ? 'You' : 'OfflineAI'}</div><div class="message-content">${body}</div>`;
      if (message.role === 'assistant' && !message.pending) {
        if (message.reasoning) {
          const thinking = document.createElement('details');
          thinking.className = 'thinking-block';
          thinking.innerHTML = `<summary>Show thinking</summary><div class="thinking-content">${renderMarkdown(message.reasoning)}</div>`;
          node.appendChild(thinking);
        }
        const retrieval = document.createElement('div');
        retrieval.className = 'message-meta';
        retrieval.textContent = message.searchPerformed ? 'Library search performed' : `Library search skipped${message.retrievalReason ? `: ${message.retrievalReason}` : ''}`;
        node.appendChild(retrieval);
        if (message.performance) {
          const performance = document.createElement('div');
          performance.className = 'message-meta performance-message';
          performance.textContent = performanceLabel(message.performance);
          node.appendChild(performance);
        }
      }
      if (message.sources?.length) {
        const details = document.createElement('details');
        details.className = 'sources';
        details.innerHTML = `<summary>${message.sources.length} source${message.sources.length === 1 ? '' : 's'}</summary>` + message.sources.map(source => {
          const sourcePath = source.relative_path || source.path || '';
          const page = Number(source.pdf_page);
          const pageHash = Number.isFinite(page) && page > 0 ? `#page=${page}` : '';
          const openUrl = `${endpoint('/api/source')}?path=${encodeURIComponent(sourcePath)}${pageHash}`;
          return `<div class="source"><div class="source-title"><a class="source-link" href="${openUrl}" target="_blank" rel="noreferrer">Open PDF</a> ${escapeHtml(source.title || source.source_filename || sourcePath || 'Local document')} <span class="source-page">${source.pdf_page ? `p. ${escapeHtml(source.pdf_page)}` : ''}</span></div><div class="source-path">${escapeHtml(sourcePath)}</div><div class="source-text">${escapeHtml(source.snippet || source.text || '')}</div></div>`;
        }).join('');
        node.appendChild(details);
      }
      els.chat.appendChild(node);
    });
    els.chat.lastElementChild?.scrollIntoView({ block: 'nearest' });
    updateContextMeter();
  }

  function save() { localStorage.setItem(storageKey, JSON.stringify(state.messages.filter(message => !message.pending))); }

  function applyConfigMetadata(meta = {}) {
    if (meta.system_prompt) state.config.systemPrompt = meta.system_prompt;
    if (meta.systemPrompt) state.config.systemPrompt = meta.systemPrompt;
    const hardware = meta.hardware || state.config.hardware || {};
    if (meta.hardware) state.config.hardware = meta.hardware;
    const parameters = { ...(state.config.parameters || {}), ...(hardware.parameters || {}), ...(meta.parameters || {}) };
    Object.entries(els.parameterInputs).forEach(([key, input]) => { if (parameters[key] !== undefined) input.value = parameters[key]; });
    els.systemPrompt.textContent = state.config.systemPrompt || 'Configured by local backend.';
    if (els.modelFolder && state.config.folder) els.modelFolder.textContent = `Models: ${state.config.folder}`;
    if (els.engineLabel && state.config.engine) els.engineLabel.textContent = `Engine: ${state.config.engine}`;
    if (els.hardwareNotice && hardware.id === 'low-resource') {
      els.hardwareNotice.textContent = `${hardware.label}: ${hardware.message} Thinking remains available, but is off by default to save time and memory.`;
      els.hardwareNotice.hidden = false;
    } else if (els.hardwareNotice) {
      els.hardwareNotice.hidden = true;
    }
    if (els.modelQuickLabel) {
      const selected = (state.config.localModels || []).find(model => modelId(model) === els.model.value);
      els.modelQuickLabel.textContent = selected ? modelLabel(selected) : 'Local model';
    }
    if (parameters.thinking !== undefined && els.thinking) els.thinking.checked = Boolean(parameters.thinking);
    updateModelDownloadPanel();
    updateContextMeter();
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes >= 1024 ** 3) return `${(bytes / (1024 ** 3)).toFixed(2)} GB`;
    return `${Math.round(bytes / (1024 ** 2))} MB`;
  }

  function updateModelDownloadPanel(downloadState = null) {
    if (!els.modelDownloadPanel) return;
    const qwen = (state.config.downloadableModels || []).find(model => model.id === 'qwen/qwen3-0.6b');
    const installed = (state.config.localModels || []).some(model => modelId(model) === 'qwen/qwen3-0.6b');
    if (!qwen || installed) {
      els.modelDownloadPanel.hidden = true;
      return;
    }
    els.modelDownloadPanel.hidden = false;
    const current = downloadState || {};
    if (current.status === 'downloading' || current.status === 'starting') {
      els.downloadModelButton.disabled = true;
      els.downloadModelButton.textContent = 'Downloading…';
      els.modelDownloadStatus.textContent = current.message || `${formatBytes(current.bytes_downloaded)} of ${formatBytes(current.total_bytes)} (${current.percent || 0}%)`;
    } else if (current.status === 'complete') {
      els.downloadModelButton.disabled = true;
      els.downloadModelButton.textContent = 'Download complete';
      els.modelDownloadStatus.textContent = current.message || 'Restart OfflineAI to load the new model.';
      if (els.restartButton) els.restartButton.hidden = false;
    } else if (current.status === 'error') {
      els.downloadModelButton.disabled = false;
      els.downloadModelButton.textContent = 'Retry model download';
      els.modelDownloadStatus.textContent = current.message || 'The download failed.';
    } else {
      els.downloadModelButton.disabled = false;
      els.downloadModelButton.textContent = `Download super-light model (${formatBytes(qwen.size_bytes)})`;
      els.modelDownloadStatus.textContent = 'The download is optional. It is recommended for very low-memory laptops.';
    }
  }

  let modelDownloadTimer = null;
  async function pollModelDownload() {
    try {
      const result = await request(endpoint('/api/model/download/status'));
      updateModelDownloadPanel(result);
      if (result.status === 'downloading' || result.status === 'starting') {
        modelDownloadTimer = window.setTimeout(pollModelDownload, 1000);
      }
    } catch (error) {
      if (els.modelDownloadStatus) els.modelDownloadStatus.textContent = `Could not read download status: ${error.message}`;
      if (els.downloadModelButton) els.downloadModelButton.disabled = false;
    }
  }

  async function downloadModel() {
    if (modelDownloadTimer) window.clearTimeout(modelDownloadTimer);
    updateModelDownloadPanel({ status: 'starting', message: 'Preparing model download…' });
    try {
      await request(endpoint('/api/model/download'), { method: 'POST', body: '{}' });
      await pollModelDownload();
    } catch (error) {
      updateModelDownloadPanel({ status: 'error', message: `Model download failed: ${error.message}` });
    }
  }

  function applyModelDefaults() {
    const selected = (state.config.localModels || []).find(model => modelId(model) === els.model.value);
    if (!selected) return;
    const defaults = selected.default_generation || {};
    if (defaults.temperature !== undefined) els.parameterInputs.temperature.value = defaults.temperature;
    if (defaults.top_p !== undefined) els.parameterInputs.topP.value = defaults.top_p;
    if (defaults.max_output_tokens !== undefined) els.parameterInputs.maxOutputTokens.value = defaults.max_output_tokens;
    if (selected.context_window !== undefined) els.parameterInputs.maxContextTokens.value = selected.context_window;
    const hardwareParameters = state.config.hardware?.parameters || {};
    Object.entries(hardwareParameters).forEach(([key, value]) => {
      const input = els.parameterInputs[key];
      if (input && value !== undefined) input.value = value;
    });
    if (hardwareParameters.thinking !== undefined && els.thinking) els.thinking.checked = Boolean(hardwareParameters.thinking);
    if (els.modelQuickLabel) els.modelQuickLabel.textContent = modelLabel(selected);
    updateContextMeter();
  }

  function loadConfiguredModels() {
    const models = state.config.localModels || [];
    els.model.innerHTML = '';
    if (!models.length) {
      els.model.add(new Option('No configured local models', ''));
      els.model.disabled = true;
      return;
    }
    els.model.disabled = false;
    models.forEach(model => { const id = modelId(model); if (id) els.model.add(new Option(modelLabel(model), id)); });
    const recommended = state.config.hardware?.recommended_model;
    if (recommended && [...els.model.options].some(option => option.value === recommended)) els.model.value = recommended;
    applyModelDefaults();
  }

  async function checkHealth() {
    try { await request(endpoint(state.config.healthPath)); setStatus('Service ready', 'good'); }
    catch (_) { setStatus('Service unavailable', 'bad'); }
  }

  async function loadModels() {
    loadConfiguredModels();
    try {
      const body = await request(endpoint(state.config.modelsPath));
      const models = body.models || [];
      const allowed = new Set((state.config.localModels || []).map(modelId));
      const available = models.filter(model => allowed.has(modelId(model)));
      if (available.length) {
        els.model.innerHTML = '';
        available.forEach(model => els.model.add(new Option(modelLabel(model), modelId(model))));
        applyModelDefaults();
      }
    } catch (_) {}
  }

  function currentParameters() {
    return Object.fromEntries(Object.entries(els.parameterInputs).map(([key, input]) => [key, Number(input.value)]));
  }

  async function submit(event) {
    event.preventDefault();
    const content = els.prompt.value.trim();
    if (!content || els.send.disabled) return;
    showError('');
    state.messages.push({ role: 'user', content });
    els.prompt.value = '';
    const pending = { role: 'assistant', content: 'Thinking…', pending: true };
    state.messages.push(pending);
    els.send.disabled = true;
    render();
    try {
      const body = await request(endpoint(state.config.chatPath), {
        method: 'POST',
        body: JSON.stringify({
          message: content,
          model: els.model.value || undefined,
          thinking: els.thinking.checked,
          searchLibrary: els.libraryQuick.checked,
          parameters: currentParameters(),
          history: state.messages.slice(0, -1).filter(item => !item.pending).map(({ role, content: itemContent }) => ({ role, content: itemContent }))
        })
      });
      pending.content = body.message || body.answer || body.response || body.text || 'The service returned no answer.';
      pending.sources = body.sources || body.documents || [];
      pending.reasoning = body.reasoning || body.thinking || '';
      pending.performance = body.performance || body.metrics || null;
      pending.searchPerformed = body.searchPerformed ?? body.retrieval?.performed ?? pending.sources.length > 0;
      pending.retrievalReason = body.retrieval?.reason || body.context?.skip_reason || '';
      state.context = normalizeContext(body.context || body.context_accounting || body.contextUsage || body.usage?.context);
      applyConfigMetadata(body.config || body.metadata || {});
      if (pending.performance && els.performance) els.performance.textContent = performanceLabel(pending.performance);
      else if (els.performance) els.performance.textContent = pending.reasoning ? 'Thinking was enabled for this answer.' : 'Answer complete.';
      delete pending.pending;
      setStatus('Service ready', 'good');
    } catch (error) {
      state.messages.pop();
      showError(`Could not reach the local assistant. ${error.message} Check that the integrated backend is running.`);
      setStatus('Service error', 'bad');
    } finally {
      els.send.disabled = false;
      save();
      render();
    }
  }

  async function checkUpdates() {
    els.updateStatus.textContent = 'Checking…';
    els.applyUpdateButton.hidden = true;
    try {
      const result = await request(endpoint('/api/update/check'));
      els.updateStatus.textContent = result.message || (result.updateAvailable ? `Version ${result.latestVersion} is available.` : 'No update available.');
      els.applyUpdateButton.hidden = !result.updateAvailable;
    } catch (error) { els.updateStatus.textContent = `Update check failed: ${error.message}`; }
  }

  async function applyUpdate() {
    els.updateStatus.textContent = 'Downloading and installing…';
    els.applyUpdateButton.disabled = true;
    try {
      const result = await request(endpoint('/api/update/apply'), { method: 'POST', body: '{}' });
      els.updateStatus.textContent = result.message || 'Update installed. Restart OfflineAI.';
      els.applyUpdateButton.hidden = true;
      if (result.restartRequired && els.restartButton) els.restartButton.hidden = false;
    } catch (error) { els.updateStatus.textContent = `Update failed: ${error.message}`; }
    finally { els.applyUpdateButton.disabled = false; }
  }

  async function restartOfflineAI() {
    if (!els.restartButton) return;
    els.restartButton.disabled = true;
    els.updateStatus.textContent = 'Restarting OfflineAI…';
    try {
      await request(endpoint('/api/restart'), { method: 'POST', body: '{}' });
      window.setTimeout(() => window.location.reload(), 4000);
    } catch (error) {
      els.restartButton.disabled = false;
      els.updateStatus.textContent = `Restart failed: ${error.message}. Close and start OfflineAI again.`;
    }
  }

  els.form.addEventListener('submit', submit);
  els.prompt.addEventListener('input', updateContextMeter);
  els.prompt.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); els.form.requestSubmit(); } });
  els.model.addEventListener('change', applyModelDefaults);
  els.libraryQuick.addEventListener('change', () => { els.library.checked = els.libraryQuick.checked; });
  els.library.addEventListener('change', () => { els.libraryQuick.checked = els.library.checked; });
  els.clear.addEventListener('click', () => { state.messages = []; state.context = null; save(); showError(''); render(); });
  els.settingsButton.addEventListener('click', () => { const open = els.settingsPanel.hidden; els.settingsPanel.hidden = !open; els.settingsButton.setAttribute('aria-expanded', String(open)); });
  els.checkUpdatesButton.addEventListener('click', checkUpdates);
  els.applyUpdateButton.addEventListener('click', applyUpdate);
  els.restartButton?.addEventListener('click', restartOfflineAI);
  els.downloadModelButton?.addEventListener('click', downloadModel);

  try { state.messages = JSON.parse(localStorage.getItem(storageKey) || '[]'); } catch (_) { state.messages = []; }
  (async () => { render(); await loadConfig(); applyConfigMetadata(); await Promise.all([checkHealth(), loadModels()]); })();
})();
