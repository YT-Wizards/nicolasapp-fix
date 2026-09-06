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
  $('#settingsStatus').innerHTML = [
    ['GeminiGen', status.geminigen], ['Algrow', status.algrow], ['Vercel AI', status.gateway]
  ].map(([label, ok]) => `<span class="${ok ? 'ok' : ''}">${ok ? '✓' : '×'} ${label}</span>`).join('');
  const ready = status.geminigen && status.algrow && status.gateway;
  $('#connectionPill').textContent = ready ? 'Claves guardadas' : 'Configurar APIs';
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
    await window.vyt.createJob({
      title, source: selectedVideo.path, branding: $('#brandingToggle').checked, testMode,
      productSale: productEnabled ? {
        enabled: true,
        cardDataUrl
      } : { enabled: false }
    });
    $('#createButton').disabled = false; $('#testButton').disabled = false;
  } catch (error) {
    $('#createButton').disabled = false; $('#testButton').disabled = false;
    showError($('#formError'), error.message);
    if (/Ajustes|conecta/i.test(error.message)) $('#settingsDialog').showModal();
  }
}

function renderJobs() {
  const active = appState.jobs.filter((j) => !['completed', 'failed', 'cancelled'].includes(j.status));
  $('#capacityLabel').textContent = `${active.filter((j) => j.status === 'running').length} de 2 activas`;
  $('#jobsEmpty').style.display = active.length ? 'none' : 'flex';
  $('#jobsList').innerHTML = active.map((job) => `
    <article class="job-card">
      <div class="job-title-row"><strong>${escapeHtml(job.title)}</strong><span class="job-percent">${Math.round(job.progress || 0)}%</span></div>
      <div class="progress-track"><i style="width:${Math.max(0, Math.min(100, job.progress || 0))}%"></i></div>
      <div class="job-detail"><span>${escapeHtml(job.phase || 'En cola')}</span><span>${job.status === 'queued' ? 'esperando turno' : formatEta(job.etaSeconds)}</span></div>
      <div class="job-cost"><span>${escapeHtml(job.detail || (job.testMode ? 'Prueba de 90 s' : 'Vídeo completo'))}</span><b>${Number(job.spentUsd || 0).toFixed(2)} $ / ${Number(job.maxCostUsd || (job.testMode ? 1.5 : 7)).toFixed(2)} $</b></div>
      <div class="job-metrics"><span>${escapeHtml(statusLabel(job.status))}</span><span>${costMetric(job.costLedger, 'avoided_duplicate')} recuperados</span><span>${costMetric(job.costLedger, 'charged')} cobrados</span></div>
      ${job.warning ? `<div class="job-warning">${escapeHtml(job.warning)}</div>` : ''}
      <div class="job-actions"><button data-cancel="${job.id}">Cancelar</button></div>
    </article>`).join('');
  $$('[data-cancel]').forEach((button) => button.onclick = () => window.vyt.cancelJob(button.dataset.cancel));
}

function statusLabel(status) {
  return ({ queued: 'En cola', running: 'En producción', paused: 'En pausa', waiting_for_provider: 'Esperando provider', waiting_for_download: 'Esperando descarga', rendering: 'Montando', recoverable: 'Reanudable' })[status] || status || 'En cola';
}

function costMetric(ledger, kind) {
  const value = Number(ledger?.[kind] || 0);
  return `${value.toFixed(2)} $`;
}

function renderHistory() {
  if (!appState.history.length) {
    $('#historyList').innerHTML = '<div class="empty-state"><strong>Aún no hay vídeos terminados</strong><p>Los resultados aparecerán aquí, sin escenas ni archivos temporales.</p></div>';
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
      <button data-open="${escapeHtml(item.outputPath)}" ${item.outputPath ? '' : 'disabled'}>${item.status === 'completed' ? 'Abrir vídeo' : 'Sin archivo'}</button>
    </article>`;
  }).join('');
  $$('[data-open]').forEach((button) => button.onclick = () => window.vyt.openOutput(button.dataset.open));
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
  $('#channelsSummary').innerHTML = `<strong>${channelsState.channels.length}</strong><span>canales</span>`;
  $('#channelsAssigned').textContent = `${channelsState.channels.filter((item) => item.proxy).length} con proxy asignado`;
  $('#toggleChannelEmails').textContent = showChannelEmails ? 'Ocultar correos' : 'Mostrar correos';
  $('#channelsEmpty').style.display = items.length ? 'none' : 'block';
  $('#channelsEmpty').textContent = channelsLoaded ? 'No hay canales que coincidan.' : 'Cargando tus canales…';
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
      gatewayKey: $('#gatewayKey').value
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
      gatewayKey: $('#gatewayKey').value
    });
    const result = await window.vyt.testSettings();
    showError($('#settingsError'), result.errors.length ? result.errors.join(' · ') : 'Conexiones correctas: Claude y Gemini están disponibles.');
    await refreshSettings();
  } catch (error) { showError($('#settingsError'), error.message); }
};

window.vyt.onState(render);
window.vyt.getState().then(render);
refreshSettings();
