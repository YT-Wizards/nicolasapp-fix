const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let selectedVideo = null;
let selectedProductQr = null;
let appState = { jobs: [], history: [] };
let channelsState = { channels: [], proxies: [], labels: [] };
let channelsLoaded = false;
let showChannelEmails = false;
let editingChannelId = null;
let selectedChannelLabels = new Set();
let currentLanguage = 'es';

const translations = {
  es: { create: 'Crear', history: 'Historial', channels: 'Canales', settings: 'Ajustes', approvedStyle: 'Ver estilo aprobado', configure: 'Configurar APIs', saved: 'Claves guardadas', production: 'Producción', progress: 'Aquí verás el progreso real', progressHelp: 'Fase actual, porcentaje, tiempo restante y coste consumido.', results: 'RESULTADOS', simpleHistory: 'Historial sencillo', historyHelp: 'Solo se conserva el vídeo final y su coste.', personal: 'GESTOR PERSONAL', channelHelp: 'Accesos, estado y proxy de tus canales, separados de la producción de vídeo.', search: 'Buscar canal o correo…', showEmails: 'Mostrar correos', hideEmails: 'Ocultar correos', import: 'Importar', newChannel: '+ Nuevo canal', settingsEyebrow: 'CONEXIONES SEGURAS', settingsCopy: 'Las claves quedan cifradas con el llavero de este Mac. Nunca aparecen en el vídeo ni en el historial.', check: 'Comprobar', saveConnections: 'Guardar conexiones', noVideos: 'Aún no hay vídeos terminados', noVideosHelp: 'Los resultados aparecerán aquí, sin escenas ni archivos temporales.', openVideo: 'Abrir vídeo', noFile: 'Sin archivo', resume: 'Continuar', resumeTest: 'Continuar prueba', resumeVideo: 'Continuar vídeo', recovered: 'recuperados', charged: 'cobrados', operations: 'operaciones', channelsCount: 'canales', assigned: 'con proxy asignado', loading: 'Cargando tus canales…', noMatches: 'No hay canales que coincidan.' },
  en: { create: 'Create', history: 'History', channels: 'Channels', settings: 'Settings', approvedStyle: 'View approved style', configure: 'Configure APIs', saved: 'Keys saved', production: 'Production', progress: 'Real progress will appear here', progressHelp: 'Current phase, percentage, time remaining and spend.', results: 'RESULTS', simpleHistory: 'Simple history', historyHelp: 'Only the final video and its cost are kept.', personal: 'PERSONAL MANAGER', channelHelp: 'Access, status and proxy for your channels, separate from video production.', search: 'Search channel or email…', showEmails: 'Show emails', hideEmails: 'Hide emails', import: 'Import', newChannel: '+ New channel', settingsEyebrow: 'SECURE CONNECTIONS', settingsCopy: 'Keys are encrypted with this Mac’s Keychain. They never appear in the video or history.', check: 'Check', saveConnections: 'Save connections', noVideos: 'No finished videos yet', noVideosHelp: 'Results will appear here without scenes or temporary files.', openVideo: 'Open video', noFile: 'No file', resume: 'Resume', resumeTest: 'Resume test', resumeVideo: 'Resume video', recovered: 'recovered', charged: 'charged', operations: 'operations', channelsCount: 'channels', assigned: 'with proxy assigned', loading: 'Loading your channels…', noMatches: 'No matching channels.' }
};
function t(key) { return translations[currentLanguage]?.[key] || translations.es[key] || key; }
function applyLanguage(language) {
  currentLanguage = language === 'en' ? 'en' : 'es';
  document.documentElement.lang = currentLanguage;
  const set = (selector, key) => { const element = $(selector); if (element) element.textContent = t(key); };
  set('[data-view="create"]', 'create'); set('[data-view="history"]', 'history'); set('[data-view="channels"]', 'channels'); $('#settingsButton')?.setAttribute('aria-label', t('settings')); set('#styleButton', 'approvedStyle');
  const english = currentLanguage === 'en';
  const heroTitle = $('.hero-row h1'); if (heroTitle) heroTitle.innerHTML = english ? 'A finished video.<br><em>Without editing.</em>' : 'Un vídeo terminado.<br><em>Sin editar nada.</em>';
  const heroEyebrow = $('.hero-row .eyebrow'); if (heroEyebrow) heroEyebrow.textContent = english ? 'HEYGEN → FULL VIDEO' : 'HEYGEN → VÍDEO COMPLETO';
  const lede = $('.lede'); if (lede) lede.textContent = english ? 'Add the HeyGen video. VYT analyses the narration, creates the B-roll and delivers the final edit to Downloads.' : 'Añade el vídeo de HeyGen. VYT analiza la narración, crea el B-roll y entrega el montaje final en Descargas.';
  const titleLabel = document.querySelector('label[for="titleInput"]'); if (titleLabel) titleLabel.textContent = english ? 'Video title' : 'Título del vídeo'; const titleInput = $('#titleInput'); if (titleInput) titleInput.placeholder = english ? 'E.g. Why Walt Hayes Changed Everything' : 'Ej. Why Walt Hayes Changed Everything';
  const fileTitle = $('#fileTitle'); if (fileTitle && !selectedVideo) fileTitle.textContent = english ? 'Add HeyGen video' : 'Añadir vídeo de HeyGen'; const fileMeta = $('#fileMeta'); if (fileMeta && !selectedVideo) fileMeta.textContent = english ? 'MP4 or MOV · 8–35 min · landscape · final audio included' : 'MP4 o MOV · 8–35 min · horizontal · audio final incluido';
  const browse = $('#browseButton'); if (browse) browse.textContent = english ? 'Select' : 'Seleccionar';
  const toggles = $$('.toggle-row'); if (toggles[0]) { toggles[0].querySelector('strong').textContent = english ? 'Allow real brands' : 'Permitir marcas reales'; toggles[0].querySelector('small').textContent = english ? 'Only when the narration explicitly mentions a brand or product.' : 'Solo si la narración menciona expresamente una marca o producto.'; } if (toggles[1]) { toggles[1].querySelector('strong').textContent = english ? 'Add sales QR' : 'Añadir QR de venta'; toggles[1].querySelector('small').textContent = english ? 'A brief Bertha-style card, only during the product call to action.' : 'Tarjeta breve como Bertha, solo durante la llamada a la acción del producto.'; }
  const mix = document.querySelector('.mix-head span:first-child'); if (mix) mix.textContent = english ? 'Adaptive FaceTuber mix' : 'Mezcla FaceTuber adaptativa';
  const limit = document.querySelector('.limit-note'); if (limit) limit.textContent = english ? 'Full videos of 8–35 min · limit: $7.00 per video · 90-second test: $1.50 · no job limit · 1080p output' : 'Vídeos completos de 8–35 min · límite: 7,00 $ por vídeo · prueba de 90 s: 1,50 $ · sin límite de jobs · salida 1080p';
  set('.panel-title span:first-child', 'production'); set('#jobsEmpty strong', 'progress'); set('#jobsEmpty p', 'progressHelp');
  set('#historyView .eyebrow', 'results'); set('#historyView h2', 'simpleHistory'); set('#historyView .history-head p', 'historyHelp'); set('#channelsView .eyebrow', 'personal'); set('#channelsView h2', 'channels'); set('#channelsView .channels-head p', 'channelHelp');
  const search = $('#channelSearch'); if (search) search.placeholder = t('search'); set('#toggleChannelEmails', showChannelEmails ? 'hideEmails' : 'showEmails'); set('#importChannelsButton', 'import'); set('#newChannelButton', 'newChannel');
  set('#settingsDialog .eyebrow', 'settingsEyebrow'); set('#settingsDialog h3', 'settings'); set('#settingsDialog .modal-copy', 'settingsCopy'); set('#testConnections', 'check'); set('#saveSettings', 'saveConnections');
  renderJobs(); renderHistory(); if (channelsLoaded) { renderChannelFilters(); renderChannels(); }
}

const approvedStyle = `SUBJECT: [The exact factual subject, action, place and era described by this 4–6 second narration beat]. Show only what is supported by the narration. Recurring subjects keep the same factual written description, but every image and clip is created independently so a previous composition is never reused. Generic examples may vary.

Photorealistic paused frame extracted from an ordinary factual YouTube documentary recorded around 2018–2022. Authentic real-life footage, completely candid and uncinematic. Consumer smartphone or ordinary camcorder, 24–35 mm equivalent, eye-level human operator, deep focus, natural available light, automatic exposure and white balance, muted natural colours, matte surfaces, genuine wear and small real-world imperfections.

The frame must look informative rather than beautiful: an incidental B-roll moment someone simply paused in a normal 1080p YouTube video. Prefer a concrete task in progress, a useful object being handled, or a factual detail that directly explains the current sentence. Moderate H.264/H.265 compression, slight background macroblocking, reduced microcontrast, mild digital noise, tiny motion softness and subtle smartphone sharpening halos. Preserve useful detail; the image must not look dirty, damaged, vintage or intentionally degraded.

No presenter looking into camera unless the narration specifically requires an interview. No invented text, labels, logos or brand packaging unless “real branding” is enabled and the narration explicitly names that brand. No captions, no title cards, no graphic design.

NEGATIVE: cinematic, movie still, commercial photography, editorial lighting, studio lighting, dramatic lighting, volumetric light, golden hour, teal and orange, HDR, glossy surfaces, silky fur, hyper-detailed individual hairs, perfect skin, waxy texture, plastic texture, CGI, render, Unreal Engine, Octane, digital painting, concept art, shallow depth of field, creamy bokeh, perfect symmetry, polished composition, vibrant colours, excessive contrast, oversharpening, fake film grain, scratches, dust overlay, dirty lens, VHS, sepia, vintage filter, heavy noise, extreme zoom, Dutch angle, camera shake, duplicated subjects, distorted anatomy, watermark, subtitle, text, poster, collage.`;

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'Calculando tiempo…';
  const minutes = Math.ceil(seconds / 60);
  return minutes < 2 ? 'menos de 2 min' : `aprox. ${minutes} min`;
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}
function showError(target, message) {
  target.textContent = message || '';
  target.classList.toggle('visible', Boolean(message));
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('visible'), 2200);
}

async function refreshSettings() {
  const status = await window.vyt.getSettingsStatus();
  applyLanguage(status.language);
  $('#languageSelect').value = status.language;
  $('#settingsStatus').innerHTML = [
    ['GeminiGen', status.geminigen], ['Algrow', status.algrow], ['Vercel AI', status.gateway]
  ].map(([label, ok]) => `<span class="${ok ? 'ok' : ''}">${ok ? '✓' : '×'} ${label}</span>`).join('');
  const ready = status.geminigen && status.algrow && status.gateway;
  $('#connectionPill').textContent = ready ? t('saved') : t('configure');
  $('#connectionPill').classList.toggle('connected', ready);
  return ready;
}

async function selectVideo() {
  const info = await window.vyt.chooseVideo();
  if (!info) return;
  selectedVideo = info;
  $('#fileTitle').textContent = info.name;
  $('#fileMeta').textContent = `${formatDuration(info.duration)} · ${info.width}×${info.height} · audio final incluido`;
  if (!$('#titleInput').value.trim()) $('#titleInput').value = info.name.replace(/\.[^.]+$/, '');
  showError($('#formError'), '');
}

async function selectProductQr() {
  const info = await window.vyt.chooseProductQr();
  if (!info) return;
  selectedProductQr = info;
  $('#productQrName').textContent = info.name;
  $('#productQrPreview').textContent = '';
  $('#productQrPreview').style.backgroundImage = `url("${info.dataUrl}")`;
  showError($('#formError'), '');
}

function loadCanvasImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('No se pudo leer la imagen del QR.'));
    image.src = dataUrl;
  });
}

function roundedRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
}

function fitText(context, text, maxWidth, startSize, minimumSize = 28) {
  let size = startSize;
  while (size > minimumSize) {
    context.font = `700 ${size}px Georgia, serif`;
    if (context.measureText(text).width <= maxWidth) break;
    size -= 2;
  }
  return size;
}

async function submit(testMode) {
  try {
    showError($('#formError'), '');
    if (!selectedVideo) throw new Error('Añade primero el vídeo completo de HeyGen.');
    const title = $('#titleInput').value.trim();
    if (!title) throw new Error('Escribe el título que tendrá el archivo final.');
    $('#createButton').disabled = true; $('#testButton').disabled = true;
    const productEnabled = $('#productQrToggle').checked;
    if (productEnabled && !selectedProductQr) throw new Error('Selecciona la tarjeta completa del QR.');
    const cardDataUrl = productEnabled ? selectedProductQr.dataUrl : '';
    showError($('#formError'), currentLanguage === 'en'
      ? (testMode ? 'Checking connections and preparing the 90-second test…' : 'Checking connections and preparing the full video…')
      : (testMode ? 'Comprobando conexiones y preparando la prueba de 90 s…' : 'Comprobando conexiones y preparando el vídeo completo…'));
    await window.vyt.createJob({
      title, source: selectedVideo.path, branding: $('#brandingToggle').checked, testMode,
      productSale: productEnabled ? {
        enabled: true,
        cardDataUrl
      } : { enabled: false }
    });
    $('#createButton').disabled = false; $('#testButton').disabled = false;
    showError($('#formError'), '');
    showToast(currentLanguage === 'en' ? 'Test queued' : 'Prueba puesta en cola');
  } catch (error) {
    $('#createButton').disabled = false; $('#testButton').disabled = false;
    showError($('#formError'), error.message);
    if (/Ajustes|conecta/i.test(error.message)) $('#settingsDialog').showModal();
  }
}

function renderJobs() {
  const active = appState.jobs.filter((j) => !['completed', 'failed', 'cancelled'].includes(j.status));
  $('#capacityLabel').textContent = currentLanguage === 'en' ? `${active.filter((j) => j.status === 'running').length} active` : `${active.filter((j) => j.status === 'running').length} activas`;
  $('#jobsEmpty').style.display = active.length ? 'none' : 'flex';
  $('#jobsList').innerHTML = active.map((job) => `
    <article class="job-card">
      <div class="job-title-row"><strong>${escapeHtml(job.title)}</strong><span class="job-percent">${Math.round(job.progress || 0)}%</span></div>
      <div class="progress-track"><i style="width:${Math.max(0, Math.min(100, job.progress || 0))}%"></i></div>
      <div class="job-detail"><span>${escapeHtml(job.phase || statusLabel('queued'))}</span><span>${job.status === 'queued' ? (currentLanguage === 'en' ? 'waiting for turn' : 'esperando turno') : formatEta(job.etaSeconds)}</span></div>
      <div class="job-cost"><span>${escapeHtml(job.detail || (job.testMode ? (currentLanguage === 'en' ? '90-second test' : 'Prueba de 90 s') : (currentLanguage === 'en' ? 'Full video' : 'Vídeo completo')))}</span><b>${Number(job.spentUsd || 0).toFixed(2)} $ / ${Number(job.maxCostUsd || (job.testMode ? 1.5 : 7)).toFixed(2)} $</b></div>
      <div class="job-metrics"><span>${escapeHtml(statusLabel(job.status))}</span><span>${costMetric(job.costLedger, 'avoided_duplicate')} ${t('recovered') || 'recuperados'}</span><span>${costMetric(job.costLedger, 'charged')} ${t('charged') || 'cobrados'}</span><span>${operationMetric(job.providerOperations)} ${t('operations') || 'operaciones'}</span></div>
      ${job.warning ? `<div class="job-warning">${escapeHtml(job.warning)}</div>` : ''}
      <div class="job-actions"><button data-cancel="${job.id}">Cancelar</button></div>
    </article>`).join('');
  $$('[data-cancel]').forEach((button) => button.onclick = () => window.vyt.cancelJob(button.dataset.cancel));
}

function statusLabel(status) {
  const labels = { queued: currentLanguage === 'en' ? 'Queued' : 'En cola', running: currentLanguage === 'en' ? 'In production' : 'En producción', paused: currentLanguage === 'en' ? 'Paused' : 'En pausa', waiting_for_provider: currentLanguage === 'en' ? 'Waiting for provider' : 'Esperando provider', waiting_for_download: currentLanguage === 'en' ? 'Waiting for download' : 'Esperando descarga', rendering: currentLanguage === 'en' ? 'Rendering' : 'Montando', recoverable: currentLanguage === 'en' ? 'Resumable' : 'Reanudable' };
  return labels[status] || status || labels.queued;
}

function costMetric(ledger, kind) {
  const value = Number(ledger?.[kind] || 0);
  return `${value.toFixed(2)} $`;
}

function operationMetric(summary) {
  const waiting = ['submitted', 'polling', 'download_pending']
    .reduce((total, status) => total + Number(summary?.[status] || 0), 0);
  return `${waiting} en recovery`;
}

function renderHistory() {
  if (!appState.history.length) {
    $('#historyList').innerHTML = `<div class="empty-state"><strong>${t('noVideos')}</strong><p>${t('noVideosHelp')}</p></div>`;
    return;
  }
  $('#historyList').innerHTML = appState.history.map((item) => {
    const counts = item.generatedCounts || {};
    const breakdown = item.costBreakdown || {};
    const production = item.status === 'completed' && Object.keys(counts).length
      ? ` · ${counts.video || 0} clips · ${counts.image || 0} imágenes · ${counts.avatar || 0} avatar`
      : '';
    const costDetail = Object.keys(breakdown).length
      ? `Vídeo ${Number(breakdown.video || 0).toFixed(2)} $ · imágenes ${Number(breakdown.image || 0).toFixed(2)} $ · análisis/revisión ${Number((breakdown.analysis || 0) + (breakdown.review || 0)).toFixed(2)} $`
      : '';
    return `
    <article class="history-item">
      <div><strong>${escapeHtml(item.title)}</strong><small>${item.status === 'completed' ? `${formatDuration(item.duration)} · ${new Date(item.completedAt).toLocaleString('es-ES')}${production}` : escapeHtml(item.error || 'No terminado')}</small>${item.warning ? `<small>${escapeHtml(item.warning)}</small>` : ''}${costDetail ? `<small>${costDetail}</small>` : ''}</div>
      <div class="history-cost">${Number(item.costUsd || 0).toFixed(2)} $</div>
      <button data-open="${escapeHtml(item.outputPath)}" ${item.outputPath ? '' : 'disabled'}>${item.status === 'completed' ? t('openVideo') : t('noFile')}</button>
      ${item.resumable ? `<button data-resume="${escapeHtml(item.id)}" class="primary">${item.testMode ? t('resumeTest') : t('resumeVideo')}</button>` : ''}
    </article>`;
  }).join('');
  $$('[data-open]').forEach((button) => button.onclick = () => window.vyt.openOutput(button.dataset.open));
  $$('[data-resume]').forEach((button) => button.onclick = async () => {
    try {
      button.disabled = true;
      await window.vyt.resumeJob(button.dataset.resume);
      showToast(currentLanguage === 'en' ? 'Resume queued from checkpoint' : 'Continuación puesta en cola desde el checkpoint');
      $('[data-view="create"]')?.click();
    } catch (error) {
      button.disabled = false;
      showToast(error.message);
    }
  });
}

function render(state) { appState = state; renderJobs(); renderHistory(); }

function channelStatusLabel(status) {
  return ({ aged: 'Aged', active: 'Activo', under_review: 'En revisión', monetized: 'Monetizado' })[status] || status;
}

function renderChannelFilters() {
  const filter = $('#channelProxyFilter');
  const current = filter.value;
  filter.innerHTML = '<option value="">Todos los proxies</option><option value="none">Sin proxy</option>' +
    channelsState.proxies.map((proxy) => `<option value="${proxy.id}">${escapeHtml(proxy.label || proxy.host || `Proxy ${proxy.id}`)}</option>`).join('');
  filter.value = [...filter.options].some((option) => option.value === current) ? current : '';
  $('#channelProxy').innerHTML = '<option value="">Ninguno</option>' + channelsState.proxies.map((proxy) =>
    `<option value="${proxy.id}">${escapeHtml(proxy.label || proxy.host || `Proxy ${proxy.id}`)}${proxy.port ? ` · ${escapeHtml(proxy.port)}` : ''}</option>`
  ).join('');
}

function visibleChannels() {
  const query = $('#channelSearch').value.trim().toLocaleLowerCase('es');
  const proxy = $('#channelProxyFilter').value;
  return channelsState.channels.filter((channel) => {
    const matchesText = !query || [channel.channel_name, channel.gmail, channel.notes]
      .some((value) => String(value || '').toLocaleLowerCase('es').includes(query));
    const matchesProxy = !proxy || (proxy === 'none' ? !channel.proxy_id : String(channel.proxy_id) === proxy);
    return matchesText && matchesProxy;
  });
}

function renderChannels() {
  const items = visibleChannels();
  $('#channelsSummary').innerHTML = `<strong>${channelsState.channels.length}</strong><span>${t('channelsCount')}</span>`;
  $('#channelsAssigned').textContent = `${channelsState.channels.filter((item) => item.proxy).length} ${t('assigned')}`;
  $('#toggleChannelEmails').textContent = showChannelEmails ? 'Ocultar correos' : 'Mostrar correos';
  $('#channelsEmpty').style.display = items.length ? 'none' : 'block';
  $('#channelsEmpty').textContent = channelsLoaded ? t('noMatches') : t('loading');
  $('#channelsBody').innerHTML = items.map((channel) => {
    const identity = channel.channel_name || channel.gmail || 'Sin nombre';
    return `<tr>
      <td><div class="channel-identity"><span class="channel-initial">${escapeHtml(identity[0]?.toUpperCase() || '?')}</span><div><strong>${escapeHtml(channel.channel_name || 'Sin nombre')}</strong><span class="channel-email ${showChannelEmails ? '' : 'concealed'}">${escapeHtml(channel.gmail || 'Sin correo')}</span></div></div></td>
      <td>${channel.proxy ? `<div class="channel-proxy"><b>${escapeHtml(channel.proxy.port || '—')}</b><span>${escapeHtml(channel.proxy.host || channel.proxy.label || '')}</span></div>` : '<span class="channel-muted">Sin asignar</span>'}</td>
      <td><span class="channel-status ${escapeHtml(channel.status)}">${escapeHtml(channelStatusLabel(channel.status))}</span></td>
      <td><div class="channel-actions"><button data-channel-copy="${channel.id}">Copiar acceso</button><button class="danger" data-channel-delete="${channel.id}">Eliminar</button><button class="primary" data-channel-edit="${channel.id}">Editar</button></div></td>
    </tr>`;
  }).join('');
  $$('[data-channel-copy]').forEach((button) => button.onclick = async () => {
    try { await window.vyt.copyChannelAccess(Number(button.dataset.channelCopy)); showToast('Acceso copiado'); }
    catch (error) { showError($('#channelsError'), error.message); }
  });
  $$('[data-channel-delete]').forEach((button) => button.onclick = () => deleteSavedChannel(Number(button.dataset.channelDelete), button));
  $$('[data-channel-edit]').forEach((button) => button.onclick = () => openChannelEditor(Number(button.dataset.channelEdit)));
}

async function deleteSavedChannel(channelId, button) {
  const channel = channelsState.channels.find((item) => item.id === channelId);
  const identity = channel?.channel_name || channel?.gmail || 'este canal';
  const confirmed = window.confirm(`¿Eliminar “${identity}”?\n\nSe borrarán también sus datos de acceso guardados. Esta acción no se puede deshacer.`);
  if (!confirmed) return;
  try {
    showError($('#channelsError'), '');
    button.disabled = true;
    await window.vyt.deleteChannel(channelId);
    await loadChannels();
    showToast('Canal eliminado');
  } catch (error) {
    showError($('#channelsError'), error.message);
    button.disabled = false;
  }
}

async function loadChannels() {
  try {
    showError($('#channelsError'), '');
    $('#channelsConnection').textContent = 'Conectando…';
    const result = await window.vyt.channelsList();
    channelsState = result;
    channelsLoaded = true;
    $('#channelsConnection').textContent = 'Base conectada';
    $('#channelsConnection').classList.add('connected');
    renderChannelFilters();
    renderChannels();
  } catch (error) {
    channelsLoaded = true;
    $('#channelsConnection').textContent = 'No disponible';
    $('#channelsConnection').classList.remove('connected');
    $('#channelsEmpty').textContent = 'No se pudieron cargar los canales.';
    showError($('#channelsError'), error.message);
  }
}

function setChannelField(selector, value) { $(selector).value = value ?? ''; }

function renderEditableLabels() {
  $('#channelLabels').innerHTML = channelsState.labels.length ? channelsState.labels.map((label) =>
    `<button type="button" class="channel-label ${selectedChannelLabels.has(label.id) ? 'selected' : ''}" data-channel-label="${label.id}" style="--label-color:${escapeHtml(label.color || '#8a7355')}">${escapeHtml(label.name)}</button>`
  ).join('') : '<span class="channel-muted">Sin etiquetas creadas</span>';
  $$('[data-channel-label]').forEach((button) => button.onclick = () => {
    const id = Number(button.dataset.channelLabel);
    if (selectedChannelLabels.has(id)) selectedChannelLabels.delete(id); else selectedChannelLabels.add(id);
    renderEditableLabels();
  });
}

async function openChannelEditor(channelId = null) {
  try {
    showError($('#channelFormError'), '');
    editingChannelId = channelId;
    let channel = {
      gmail: '', password: '', recovery_email: '', phone: '', twofa_secret: '', channel_url: '',
      channel_name: '', year: '', source: '', status: 'aged', proxy_id: '', notes: '', labels: []
    };
    if (channelId) {
      const found = channelsState.channels.find((item) => item.id === channelId);
      if (!found) throw new Error('Canal no encontrado.');
      const secrets = await window.vyt.channelSecrets(channelId);
      channel = { ...channel, ...found, ...secrets };
    }
    $('#channelDialogTitle').textContent = channelId ? 'Editar canal' : 'Nuevo canal';
    setChannelField('#channelGmail', channel.gmail);
    setChannelField('#channelPassword', channel.password);
    setChannelField('#channelRecovery', channel.recovery_email);
    setChannelField('#channelPhone', channel.phone);
    setChannelField('#channelTwofa', channel.twofa_secret);
    setChannelField('#channelName', channel.channel_name);
    setChannelField('#channelUrl', channel.channel_url);
    setChannelField('#channelYear', channel.year);
    setChannelField('#channelStatus', channel.status || 'aged');
    setChannelField('#channelSource', channel.source);
    setChannelField('#channelProxy', channel.proxy_id);
    setChannelField('#channelNotes', channel.notes);
    selectedChannelLabels = new Set((channel.labels || []).map((label) => label.id));
    renderEditableLabels();
    $('#channelDialog').showModal();
  } catch (error) { showError($('#channelsError'), error.message); }
}

async function saveCurrentChannel() {
  try {
    showError($('#channelFormError'), '');
    $('#saveChannelButton').disabled = true;
    await window.vyt.saveChannel({
      id: editingChannelId,
      gmail: $('#channelGmail').value,
      password: $('#channelPassword').value,
      recovery_email: $('#channelRecovery').value,
      phone: $('#channelPhone').value,
      twofa_secret: $('#channelTwofa').value,
      channel_url: $('#channelUrl').value,
      channel_name: $('#channelName').value,
      year: $('#channelYear').value,
      source: $('#channelSource').value,
      status: $('#channelStatus').value,
      proxy_id: $('#channelProxy').value,
      notes: $('#channelNotes').value,
      label_ids: [...selectedChannelLabels]
    });
    $('#channelDialog').close();
    await loadChannels();
    showToast('Canal guardado');
  } catch (error) { showError($('#channelFormError'), error.message); }
  finally { $('#saveChannelButton').disabled = false; }
}

function parseChannelImport(text) {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const values = line.split('|').map((value) => value.trim());
    return {
      gmail: values[0] || '', password: values[1] || '', recovery_email: values[2] || '',
      twofa_secret: values[3] || '', status: 'aged'
    };
  }).filter((item) => item.gmail);
}

async function importChannels() {
  const rows = parseChannelImport($('#channelsImportText').value);
  if (!rows.length) { showError($('#channelsImportError'), 'No encuentro ninguna cuenta válida. Usa el separador |.'); return; }
  try {
    showError($('#channelsImportError'), '');
    $('#runChannelsImport').disabled = true;
    for (const row of rows) await window.vyt.saveChannel(row);
    $('#importChannelsDialog').close();
    $('#channelsImportText').value = '';
    await loadChannels();
    showToast(`${rows.length} ${rows.length === 1 ? 'canal importado' : 'canales importados'}`);
  } catch (error) { showError($('#channelsImportError'), error.message); }
  finally { $('#runChannelsImport').disabled = false; }
}

$$('.nav-button').forEach((button) => button.onclick = () => {
  $$('.nav-button').forEach((b) => b.classList.toggle('active', b === button));
  $$('.view').forEach((view) => view.classList.remove('active'));
  $(`#${button.dataset.view}View`).classList.add('active');
  if (button.dataset.view === 'channels' && !channelsLoaded) loadChannels();
});
$('#browseButton').onclick = selectVideo;
$('#selectProductQr').onclick = selectProductQr;
$('#productQrToggle').onchange = () => { $('#productQrPanel').hidden = !$('#productQrToggle').checked; };
$('#dropZone').onclick = (event) => { if (event.target.id !== 'browseButton') selectVideo(); };
$('#createButton').onclick = () => submit(false);
$('#testButton').onclick = () => submit(true);
$('#settingsButton').onclick = async () => { await refreshSettings(); $('#settingsDialog').showModal(); };
$('#languageSelect').onchange = async () => {
  try {
    applyLanguage($('#languageSelect').value);
    await window.vyt.saveSettings({ language: $('#languageSelect').value });
    showToast(currentLanguage === 'en' ? 'Language changed to English' : 'Idioma cambiado a español');
  } catch (error) { showError($('#settingsError'), error.message); }
};
$('#styleButton').onclick = () => { $('#stylePrompt').textContent = approvedStyle; $('#styleDialog').showModal(); };
$('#channelSearch').oninput = renderChannels;
$('#channelProxyFilter').onchange = renderChannels;
$('#toggleChannelEmails').onclick = () => { showChannelEmails = !showChannelEmails; renderChannels(); };
$('#newChannelButton').onclick = () => openChannelEditor();
$('#saveChannelButton').onclick = saveCurrentChannel;
$('#importChannelsButton').onclick = () => { showError($('#channelsImportError'), ''); $('#importChannelsDialog').showModal(); };
$('#runChannelsImport').onclick = importChannels;
$('#channelDialog').addEventListener('close', () => {
  editingChannelId = null;
  selectedChannelLabels.clear();
  setChannelField('#channelPassword', '');
  setChannelField('#channelTwofa', '');
});
$('#saveSettings').onclick = async () => {
  try {
    showError($('#settingsError'), '');
    await window.vyt.saveSettings({
      geminigenKey: $('#geminigenKey').value,
      algrowKey: $('#algrowKey').value,
      gatewayKey: $('#gatewayKey').value,
      language: $('#languageSelect').value
    });
    $('#geminigenKey').value = ''; $('#algrowKey').value = ''; $('#gatewayKey').value = '';
    await refreshSettings();
  } catch (error) { showError($('#settingsError'), error.message); }
};
$('#testConnections').onclick = async () => {
  try {
    showError($('#settingsError'), 'Comprobando conexiones…');
    await window.vyt.saveSettings({
      geminigenKey: $('#geminigenKey').value,
      algrowKey: $('#algrowKey').value,
      gatewayKey: $('#gatewayKey').value,
      language: $('#languageSelect').value
    });
    const result = await window.vyt.testSettings();
    const connectionMessages = [...(result.errors || []), ...(result.warnings || [])];
    showError($('#settingsError'), connectionMessages.length ? connectionMessages.join(' · ') : 'Conexiones correctas: Claude y Gemini están disponibles.');
    await refreshSettings();
  } catch (error) { showError($('#settingsError'), error.message); }
};

window.vyt.onState((state) => { if (state.language) applyLanguage(state.language); render(state); });
window.vyt.getState().then((state) => { if (state.language) applyLanguage(state.language); render(state); });
refreshSettings();
