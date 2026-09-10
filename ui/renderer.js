const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let selectedVideo = null;
let selectedProductQr = null;
let selectedExternalAudio = null;
let selectedExternalClipsFolder = null;
let externalPlan = null;
let appState = { jobs: [], history: [] };
let channelsState = { channels: [], proxies: [], labels: [] };
let channelsLoaded = false;
let showChannelEmails = false;
let editingChannelId = null;
let selectedChannelLabels = new Set();
let currentLanguage = 'es';

const translations = {
  es: {
    create: 'Crear', history: 'Historial', channels: 'Canales', settings: 'Ajustes', approvedStyle: 'Ver estilo aprobado', configure: 'Configurar APIs', saved: 'Claves guardadas', production: 'Producción', progress: 'Aquí verás el progreso real', progressHelp: 'Fase actual, porcentaje, tiempo restante y coste consumido.', results: 'RESULTADOS', simpleHistory: 'Historial sencillo', historyHelp: 'Solo se conserva el vídeo final y su coste.', personal: 'GESTOR PERSONAL', channelHelp: 'Accesos, estado y proxy de tus canales, separados de la producción de vídeo.', search: 'Buscar canal o correo…', showEmails: 'Mostrar correos', hideEmails: 'Ocultar correos', import: 'Importar', newChannel: '+ Nuevo canal', settingsEyebrow: 'CONEXIONES SEGURAS', settingsCopy: 'Las claves quedan cifradas con el llavero de este Mac. Nunca aparecen en el vídeo ni en el historial.', check: 'Comprobar', saveConnections: 'Guardar conexiones', noVideos: 'Aún no hay vídeos terminados', noVideosHelp: 'Los resultados aparecerán aquí, sin escenas ni archivos temporales.', openVideo: 'Abrir vídeo', noFile: 'Sin archivo', resume: 'Continuar', resumeTest: 'Continuar prueba', resumeVideo: 'Continuar vídeo', recovered: 'recuperados', charged: 'cobrados', operations: 'operaciones', channelsCount: 'canales', assigned: 'con proxy asignado', loading: 'Cargando tus canales…', noMatches: 'No hay canales que coincidan.',
    automaticTab: 'Vídeo automático', externalTab: 'Clips Veo externos', externalTitle: 'Guion → prompts → clips → vídeo', externalCopy: 'No usa Veo ni créditos dentro de VYT. Tú generas los clips y VYT los sincroniza con la locución.', externalSteps: ['1. Guion y audio', '2. Prompts', '3. Clips', '4. Montaje'], videoTitle: 'Título del vídeo', videoTitlePlaceholder: 'Ej. Why Walt Hayes Changed Everything', addHeygen: 'Añadir vídeo de HeyGen', heygenMeta: 'MP4 o MOV · 8–35 min · horizontal · audio final incluido', select: 'Seleccionar', allowBrands: 'Permitir marcas reales', allowBrandsHelp: 'Solo si la narración menciona expresamente una marca o producto.', salesQr: 'Añadir QR de venta', salesQrHelp: 'Tarjeta breve como Bertha, solo durante la llamada a la acción del producto.', selectCard: 'Seleccionar tarjeta completa', selectCardHelp: 'PNG, JPG o WebP · debe incluir QR, nombre, precio y web', choose: 'Elegir', qrHelp: 'VYT muestra esta imagen durante el CTA real y una vez más en una aparición posterior del avatar.', mix: 'Mezcla FaceTuber adaptativa', mixHelp: 'cortes por frase · ritmo adaptativo en vídeos largos', clips: 'Clips', images: 'Imágenes', avatar: 'Avatar', split: 'Dividida', test: 'Prueba de 90 s', createFull: 'Crear vídeo completo', limit: 'Vídeos completos de 8–35 min · límite: 7,00 $ por vídeo · prueba de 90 s: 1,50 $ · sin límite de jobs · salida 1080p',
    externalTitlePlaceholder: 'Ej. Why no signal changes everything', pasteScript: '1. Pega el guion final', scriptPlaceholder: 'Pega aquí exactamente el guion que se usó para la locución…', promptStyle: 'Estilo de los prompts', styleDocumentary: 'B-roll documental realista', styleYoutube: 'YouTube informativo sencillo', styleNatural: 'Cámara natural, casa y oficina', addAudio: 'Añadir locución final', audioMeta: 'MP3, M4A, WAV o vídeo con la pista final', createPrompts: 'Crear prompts numerados', selectClips: '2. Seleccionar carpeta de clips', clipsMeta: 'Nombres: 001.mp4, 002.mp4, 003.mp4…', checkClips: 'Comprobar clips', assembleVideo: 'Montar vídeo',
    statusQueued: 'En cola', statusRunning: 'En producción', statusPaused: 'En pausa', statusProvider: 'Esperando proveedor', statusDownload: 'Esperando descarga', statusRendering: 'Montando', statusRecoverable: 'Reanudable', waiting: 'esperando turno', calculating: 'Calculando tiempo…', underTwoMinutes: 'menos de 2 min', aboutMinutes: 'aprox. {minutes} min', externalCost: 'Sin coste de APIs VYT', externalEdit: 'Montaje de clips externos', cancel: 'Cancelar', completed: 'Terminado', unfinished: 'No terminado', video: 'Vídeo', analysisReview: 'análisis/revisión', recoveryOperations: 'en recuperación',
    channelsConnection: 'Base de Canales', allProxies: 'Todos los proxies', noProxy: 'Sin proxy', channel: 'Canal', proxy: 'Proxy', status: 'Estado', copyAccess: 'Copiar acceso', delete: 'Eliminar', edit: 'Editar', unnamed: 'Sin nombre', noEmail: 'Sin correo', unassigned: 'Sin asignar', copied: 'Acceso copiado', deleteChannel: '¿Eliminar “{name}”?\n\nSe borrarán también sus datos de acceso guardados. Esta acción no se puede deshacer.', deleted: 'Canal eliminado', connecting: 'Conectando…', connected: 'Base conectada', unavailable: 'No disponible', channelsUnavailable: 'No se pudieron cargar los canales.', noLabels: 'Sin etiquetas creadas', channelNotFound: 'Canal no encontrado.', editChannel: 'Editar canal', channelNew: 'Nuevo canal', channelSaved: 'Canal guardado', importInvalid: 'No encuentro ninguna cuenta válida. Usa el separador |.', channelImported: 'canal importado', channelsImported: 'canales importados',
    channelDialog: 'CANAL', gmail: 'Gmail', password: 'Contraseña', recoveryEmail: 'Email de recuperación', phone: 'Teléfono', twofa: 'Clave 2FA', channelName: 'Nombre del canal', year: 'Año', source: 'Origen', labels: 'Etiquetas', notes: 'Notas', saveChannel: 'Guardar canal', importEyebrow: 'IMPORTAR', importTitle: 'Añadir varios canales', importCopy: 'Una cuenta por línea: correo | contraseña | recuperación | 2FA', importPlaceholder: 'correo@gmail.com | contraseña | recuperacion@gmail.com | CLAVE2FA',
    styleEyebrow: 'BLOQUEADO PARA PRODUCCIÓN', styleTitle: 'Estilo aprobado', styleName: 'Documental real de YouTube', styleCopy: 'Mate, cotidiano, ligeramente imperfecto. Nunca cinematográfico, brillante, sucio ni artificial.', leaveBlank: 'Dejar vacío para conservar la guardada', gatewayHelp: 'Claude dirige las escenas · Gemini revisa la calidad', language: 'Idioma / Language', languageChanged: 'Idioma cambiado a español', connectionsChecking: 'Comprobando conexiones…', connectionsGood: 'Conexiones correctas: Claude y Gemini están disponibles.',
    addHeygenFirst: 'Añade primero el vídeo completo de HeyGen.', enterTitle: 'Escribe el título que tendrá el archivo final.', chooseQr: 'Selecciona la tarjeta completa del QR.', checkingTest: 'Comprobando conexiones y preparando la prueba de 90 s…', checkingFull: 'Comprobando conexiones y preparando el vídeo completo…', testQueued: 'Prueba puesta en cola', pasteScriptError: 'Pega el guion final antes de continuar.', chooseAudioError: 'Selecciona la locución final antes de continuar.', planning: 'Transcribiendo el audio y creando los prompts…', promptsReady: '{count} prompts listos.', promptsReadyHelp: 'Cada uno tiene un número y el tiempo exacto de montaje.', downloadPrompts: 'Descargar prompts (.txt)', audioReady: 'audio final listo', folderReady: 'Carpeta seleccionada · ahora comprueba los clips', createPromptsFirst: 'Crea primero los prompts.', chooseFolder: 'Selecciona la carpeta de clips.', checkingClips: 'Comprobando nombres y duraciones…', allReady: 'Todo listo.', allReadyHelp: '{count} clips están presentes y tienen la duración necesaria.', cannotAssemble: 'Aún no se puede montar.', missing: 'Faltan: {numbers}', tooShort: 'Demasiado cortos: {clips}', enterFinalTitle: 'Escribe un título para el vídeo final.', completeSteps: 'Completa los pasos anteriores antes de montar.', queueAssembly: 'Añadiendo el montaje a Producción…', assemblyQueued: 'Montaje puesto en cola', resumeActive: 'Este vídeo ya está en producción', resumeQueued: 'Continuación puesta en cola desde el checkpoint'
  },
  en: {
    create: 'Create', history: 'History', channels: 'Channels', settings: 'Settings', approvedStyle: 'View approved style', configure: 'Configure APIs', saved: 'Keys saved', production: 'Production', progress: 'Real progress will appear here', progressHelp: 'Current phase, percentage, time remaining and spend.', results: 'RESULTS', simpleHistory: 'Simple history', historyHelp: 'Only the final video and its cost are kept.', personal: 'PERSONAL MANAGER', channelHelp: 'Access, status and proxy for your channels, separate from video production.', search: 'Search channel or email…', showEmails: 'Show emails', hideEmails: 'Hide emails', import: 'Import', newChannel: '+ New channel', settingsEyebrow: 'SECURE CONNECTIONS', settingsCopy: 'Keys are encrypted with this Mac’s Keychain. They never appear in the video or history.', check: 'Check', saveConnections: 'Save connections', noVideos: 'No finished videos yet', noVideosHelp: 'Results will appear here without scenes or temporary files.', openVideo: 'Open video', noFile: 'No file', resume: 'Resume', resumeTest: 'Resume test', resumeVideo: 'Resume video', recovered: 'recovered', charged: 'charged', operations: 'operations', channelsCount: 'channels', assigned: 'with proxy assigned', loading: 'Loading your channels…', noMatches: 'No matching channels.',
    automaticTab: 'Automatic video', externalTab: 'External Veo clips', externalTitle: 'Script → prompts → clips → video', externalCopy: 'It does not use Veo or credits inside VYT. You generate the clips and VYT syncs them to the voiceover.', externalSteps: ['1. Script and audio', '2. Prompts', '3. Clips', '4. Assembly'], videoTitle: 'Video title', videoTitlePlaceholder: 'E.g. Why Walt Hayes Changed Everything', addHeygen: 'Add HeyGen video', heygenMeta: 'MP4 or MOV · 8–35 min · landscape · final audio included', select: 'Select', allowBrands: 'Allow real brands', allowBrandsHelp: 'Only when the narration explicitly mentions a brand or product.', salesQr: 'Add sales QR', salesQrHelp: 'A brief Bertha-style card, only during the product call to action.', selectCard: 'Select complete card', selectCardHelp: 'PNG, JPG or WebP · must include QR, name, price and website', choose: 'Choose', qrHelp: 'VYT shows this image during the real CTA and once more during a later presenter appearance.', mix: 'Adaptive FaceTuber mix', mixHelp: 'phrase cuts · adaptive pace in long videos', clips: 'Clips', images: 'Images', avatar: 'Avatar', split: 'Split', test: '90-second test', createFull: 'Create full video', limit: 'Full videos of 8–35 min · $7.00 limit per video · 90-second test: $1.50 · no job limit · 1080p output',
    externalTitlePlaceholder: 'E.g. Why no signal changes everything', pasteScript: '1. Paste the final script', scriptPlaceholder: 'Paste the exact script used for the voiceover here…', promptStyle: 'Prompt style', styleDocumentary: 'Realistic documentary B-roll', styleYoutube: 'Simple informative YouTube', styleNatural: 'Natural home and office camera', addAudio: 'Add final voiceover', audioMeta: 'MP3, M4A, WAV or video with the final track', createPrompts: 'Create numbered prompts', selectClips: '2. Select clips folder', clipsMeta: 'Names: 001.mp4, 002.mp4, 003.mp4…', checkClips: 'Check clips', assembleVideo: 'Assemble video',
    statusQueued: 'Queued', statusRunning: 'In production', statusPaused: 'Paused', statusProvider: 'Waiting for provider', statusDownload: 'Waiting for download', statusRendering: 'Rendering', statusRecoverable: 'Resumable', waiting: 'waiting for turn', calculating: 'Calculating time…', underTwoMinutes: 'under 2 min', aboutMinutes: 'about {minutes} min', externalCost: 'No VYT API cost', externalEdit: 'External clips edit', cancel: 'Cancel', completed: 'Completed', unfinished: 'Not finished', video: 'Video', analysisReview: 'analysis/review', recoveryOperations: 'in recovery',
    channelsConnection: 'Channels database', allProxies: 'All proxies', noProxy: 'No proxy', channel: 'Channel', proxy: 'Proxy', status: 'Status', copyAccess: 'Copy access', delete: 'Delete', edit: 'Edit', unnamed: 'Unnamed', noEmail: 'No email', unassigned: 'Unassigned', copied: 'Access copied', deleteChannel: 'Delete “{name}”?\n\nIts saved access data will also be deleted. This cannot be undone.', deleted: 'Channel deleted', connecting: 'Connecting…', connected: 'Database connected', unavailable: 'Unavailable', channelsUnavailable: 'Channels could not be loaded.', noLabels: 'No labels created', channelNotFound: 'Channel not found.', editChannel: 'Edit channel', channelNew: 'New channel', channelSaved: 'Channel saved', importInvalid: 'No valid accounts found. Use the | separator.', channelImported: 'channel imported', channelsImported: 'channels imported',
    channelDialog: 'CHANNEL', gmail: 'Gmail', password: 'Password', recoveryEmail: 'Recovery email', phone: 'Phone', twofa: '2FA key', channelName: 'Channel name', year: 'Year', source: 'Source', labels: 'Labels', notes: 'Notes', saveChannel: 'Save channel', importEyebrow: 'IMPORT', importTitle: 'Add several channels', importCopy: 'One account per line: email | password | recovery | 2FA', importPlaceholder: 'email@gmail.com | password | recovery@gmail.com | 2FAKEY',
    styleEyebrow: 'LOCKED FOR PRODUCTION', styleTitle: 'Approved style', styleName: 'Realistic YouTube documentary', styleCopy: 'Matte, ordinary, slightly imperfect. Never cinematic, glossy, dirty or artificial.', leaveBlank: 'Leave blank to keep the saved key', gatewayHelp: 'Claude directs scenes · Gemini checks quality', language: 'Idioma / Language', languageChanged: 'Language changed to English', connectionsChecking: 'Checking connections…', connectionsGood: 'Connections ready: Claude and Gemini are available.',
    addHeygenFirst: 'Add the finished HeyGen video first.', enterTitle: 'Enter a title for the final file.', chooseQr: 'Select the complete QR card.', checkingTest: 'Checking connections and preparing the 90-second test…', checkingFull: 'Checking connections and preparing the full video…', testQueued: 'Test queued', pasteScriptError: 'Paste the final script before continuing.', chooseAudioError: 'Select the final voiceover before continuing.', planning: 'Transcribing audio and creating prompts…', promptsReady: '{count} prompts ready.', promptsReadyHelp: 'Each has a number and the exact assembly timing.', downloadPrompts: 'Download prompts (.txt)', audioReady: 'final audio ready', folderReady: 'Folder selected · now check the clips', createPromptsFirst: 'Create the prompts first.', chooseFolder: 'Select the clips folder.', checkingClips: 'Checking names and durations…', allReady: 'Everything is ready.', allReadyHelp: '{count} clips are present and long enough.', cannotAssemble: 'The video cannot be assembled yet.', missing: 'Missing: {numbers}', tooShort: 'Too short: {clips}', enterFinalTitle: 'Enter a title for the final video.', completeSteps: 'Complete the previous steps before assembling.', queueAssembly: 'Adding the assembly to Production…', assemblyQueued: 'Assembly queued', resumeActive: 'This video is already in production', resumeQueued: 'Resume queued from checkpoint'
  }
};
function t(key) { return translations[currentLanguage]?.[key] || translations.es[key] || key; }
function tr(key, values = {}) { return String(t(key)).replace(/\{(\w+)\}/g, (_match, name) => values[name] ?? ''); }
function applyLanguage(language) {
  currentLanguage = language === 'en' ? 'en' : 'es';
  document.documentElement.lang = currentLanguage;
  const set = (selector, key) => { const element = $(selector); if (element) element.textContent = t(key); };
  const english = currentLanguage === 'en';
  set('[data-view="create"]', 'create'); set('[data-view="history"]', 'history'); set('[data-view="channels"]', 'channels'); $('#settingsButton')?.setAttribute('aria-label', t('settings')); set('#styleButton', 'approvedStyle');
  $('.brand span').textContent = english ? 'Automatic video studio' : 'Estudio de vídeo automático'; $('.workflow-switch').setAttribute('aria-label', english ? 'Workflow' : 'Flujo de trabajo');
  const heroTitle = $('.hero-row h1'); if (heroTitle) heroTitle.innerHTML = english ? 'A finished video.<br><em>Without editing.</em>' : 'Un vídeo terminado.<br><em>Sin editar nada.</em>';
  const heroEyebrow = $('.hero-row .eyebrow'); if (heroEyebrow) heroEyebrow.textContent = english ? 'HEYGEN → FULL VIDEO' : 'HEYGEN → VÍDEO COMPLETO';
  const lede = $('.lede'); if (lede) lede.textContent = english ? 'Add the HeyGen video. VYT analyses the narration, creates the B-roll and delivers the final edit to Downloads.' : 'Añade el vídeo de HeyGen. VYT analiza la narración, crea el B-roll y entrega el montaje final en Descargas.';
  set('label[for="titleInput"]', 'videoTitle'); $('#titleInput').placeholder = t('videoTitlePlaceholder');
  if (!selectedVideo) { set('#fileTitle', 'addHeygen'); set('#fileMeta', 'heygenMeta'); } set('#browseButton', 'select');
  const toggles = $$('.toggle-row'); if (toggles[0]) { toggles[0].querySelector('strong').textContent = t('allowBrands'); toggles[0].querySelector('small').textContent = t('allowBrandsHelp'); } if (toggles[1]) { toggles[1].querySelector('strong').textContent = t('salesQr'); toggles[1].querySelector('small').textContent = t('salesQrHelp'); }
  if (!selectedProductQr) { set('#productQrName', 'selectCard'); $('#productQrPreview').textContent = 'QR'; } set('.qr-picker small', 'selectCardHelp'); set('.qr-picker b', 'choose'); set('.product-qr-panel p', 'qrHelp');
  set('.mix-head span:first-child', 'mix'); set('.mix-head span:last-child', 'mixHelp'); const legend = $$('.mix-legend span'); [t('clips'), t('images'), t('avatar'), t('split')].forEach((label, index) => { if (legend[index]) legend[index].lastChild.textContent = ` ${label}`; });
  set('#testButton', 'test'); const create = $('#createButton'); if (create) create.innerHTML = `${t('createFull')} <span>→</span>`; set('.limit-note', 'limit');
  set('[data-workflow="automatic"]', 'automaticTab'); set('[data-workflow="external"]', 'externalTab'); set('.external-intro b', 'externalTitle'); set('.external-intro span', 'externalCopy'); $$('.external-steps span').forEach((item, index) => { item.textContent = t('externalSteps')[index]; });
  set('label[for="externalTitleInput"]', 'videoTitle'); $('#externalTitleInput').placeholder = t('externalTitlePlaceholder'); set('label[for="externalScriptInput"]', 'pasteScript'); $('#externalScriptInput').placeholder = t('scriptPlaceholder'); set('label[for="externalStyleSelect"]', 'promptStyle');
  const styles = $$('#externalStyleSelect option'); [t('styleDocumentary'), t('styleYoutube'), t('styleNatural')].forEach((label, index) => { if (styles[index]) styles[index].textContent = label; });
  if (!selectedExternalAudio) { set('#externalAudioTitle', 'addAudio'); set('#externalAudioMeta', 'audioMeta'); } set('#externalAudioButton', 'select'); if (!selectedExternalClipsFolder) { set('#externalClipsTitle', 'selectClips'); set('#externalClipsMeta', 'clipsMeta'); } set('#externalClipsButton', 'select'); set('#externalPlanButton', 'createPrompts'); set('#externalCheckButton', 'checkClips'); const assemble = $('#externalRenderButton'); if (assemble) assemble.innerHTML = `${t('assembleVideo')} <span>→</span>`;
  set('.panel-title span:first-child', 'production'); set('#jobsEmpty strong', 'progress'); set('#jobsEmpty p', 'progressHelp');
  set('#historyView .eyebrow', 'results'); set('#historyView h2', 'simpleHistory'); set('#historyView .history-head p', 'historyHelp'); set('#channelsView .eyebrow', 'personal'); set('#channelsView h2', 'channels'); set('#channelsView .channels-head p', 'channelHelp');
  const search = $('#channelSearch'); if (search) search.placeholder = t('search'); set('#toggleChannelEmails', showChannelEmails ? 'hideEmails' : 'showEmails'); set('#importChannelsButton', 'import'); set('#newChannelButton', 'newChannel'); set('#channelsConnection', 'channelsConnection');
  $$('#channelsView th').forEach((item, index) => { if ([t('channel'), t('proxy'), t('status')][index]) item.textContent = [t('channel'), t('proxy'), t('status')][index]; });
  set('#settingsDialog .eyebrow', 'settingsEyebrow'); set('#settingsDialog h3', 'settings'); set('#settingsDialog .modal-copy', 'settingsCopy'); set('#testConnections', 'check'); set('#saveSettings', 'saveConnections');
  const settingLabels = $$('#settingsDialog label'); if (settingLabels[0]) settingLabels[0].childNodes[0].textContent = 'GeminiGen / SnapGen API key'; if (settingLabels[1]) settingLabels[1].childNodes[0].textContent = 'Algrow API key'; if (settingLabels[2]) { settingLabels[2].childNodes[0].textContent = 'Vercel AI Gateway API key '; settingLabels[2].querySelector('small').textContent = t('gatewayHelp'); } if (settingLabels[3]) settingLabels[3].childNodes[0].textContent = t('language'); ['#geminigenKey', '#algrowKey', '#gatewayKey'].forEach((selector) => { $(selector).placeholder = t('leaveBlank'); });
  set('#styleDialog .eyebrow', 'styleEyebrow'); set('#styleDialog h3', 'styleTitle'); set('.style-summary b', 'styleName'); set('.style-summary span', 'styleCopy');
  set('#channelDialog .eyebrow', 'channelDialog'); const channelLabels = $$('#channelDialog label'); const names = ['gmail', 'password', 'recoveryEmail', 'phone', 'twofa', 'channelName', null, 'year', 'status', 'source', 'proxy', 'labels', 'notes']; channelLabels.forEach((label, index) => { if (names[index]) label.childNodes[0].textContent = t(names[index]); }); const channelStatusOptions = $$('#channelStatus option'); ['Aged', 'Active', 'Under review', 'Monetized'].forEach((label, index) => { if (channelStatusOptions[index]) channelStatusOptions[index].textContent = label; }); set('#saveChannelButton', 'saveChannel'); $$('#channelDialog .modal-actions button[value="cancel"], #importChannelsDialog .modal-actions button[value="cancel"]').forEach((button) => { button.textContent = t('cancel'); });
  set('#importChannelsDialog .eyebrow', 'importEyebrow'); set('#importChannelsDialog h3', 'importTitle'); set('#importChannelsDialog .modal-copy', 'importCopy'); $('#channelsImportText').placeholder = t('importPlaceholder'); set('#runChannelsImport', 'import');
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
  if (!Number.isFinite(seconds) || seconds <= 0) return t('calculating');
  const minutes = Math.ceil(seconds / 60);
  return minutes < 2 ? t('underTwoMinutes') : tr('aboutMinutes', { minutes });
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

function displayPhase(phase) {
  if (currentLanguage !== 'en') return phase;
  const phases = {
    'En cola': 'Queued', 'Preparando el vídeo': 'Preparing video', 'Montando clips': 'Assembling clips',
    'Montando': 'Rendering', 'Vídeo terminado': 'Video complete', 'No se pudo terminar': 'Could not finish',
    'Esperando provider': 'Waiting for provider', 'Esperando descarga': 'Waiting for download',
    'Retomando análisis': 'Resuming analysis', 'Analizando el vídeo': 'Analyzing video',
    'Entendiendo la historia': 'Understanding the story', 'Comprobando clips': 'Checking clips',
  };
  return phases[phase] || phase;
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
  $('#fileMeta').textContent = `${formatDuration(info.duration)} · ${info.width}×${info.height} · ${t('audioReady')}`;
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

function switchWorkflow(workflow) {
  const external = workflow === 'external';
  $('#automaticWorkflow').hidden = external;
  $('#externalWorkflow').hidden = !external;
  $$('.workflow-tab').forEach((button) => button.classList.toggle('active', button.dataset.workflow === workflow));
}

function downloadText(filename, content, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function selectExternalAudio() {
  const info = await window.vyt.chooseAudio();
  if (!info) return;
  selectedExternalAudio = info;
  $('#externalAudioTitle').textContent = info.name;
  $('#externalAudioMeta').textContent = `${formatDuration(info.duration)} · ${t('audioReady')}`;
  if (!$('#externalTitleInput').value.trim()) $('#externalTitleInput').value = info.name.replace(/\.[^.]+$/, '');
  showError($('#externalError'), '');
}

async function createExternalPlan() {
  try {
    const script = $('#externalScriptInput').value.trim();
    if (!script) throw new Error(t('pasteScriptError'));
    if (!selectedExternalAudio) throw new Error(t('chooseAudioError'));
    const button = $('#externalPlanButton');
    button.disabled = true;
    showError($('#externalError'), t('planning'));
    const result = await window.vyt.createExternalPlan({ script, audio: selectedExternalAudio.path, style: $('#externalStyleSelect').value });
    externalPlan = result;
    const prompts = result.plan.scenes.map((scene) => scene.prompt).join('\n\n');
    $('#externalPlanSummary').hidden = false;
    $('#externalPlanSummary').innerHTML = `<b>${tr('promptsReady', { count: result.plan.scenes.length })}</b> ${t('promptsReadyHelp')} <a href="#" id="downloadExternalPrompts">${t('downloadPrompts')}</a>`;
    $('#externalClipArea').hidden = false;
    $('#downloadExternalPrompts').onclick = (event) => { event.preventDefault(); downloadText('veo-prompts.txt', prompts); };
    showError($('#externalError'), '');
  } catch (error) { showError($('#externalError'), error.message); }
  finally { $('#externalPlanButton').disabled = false; }
}

async function selectExternalClipsFolder() {
  const info = await window.vyt.chooseClipsFolder();
  if (!info) return;
  selectedExternalClipsFolder = info;
  $('#externalClipsTitle').textContent = info.name;
  $('#externalClipsMeta').textContent = t('folderReady');
  $('#externalRenderButton').disabled = true;
  showError($('#externalError'), '');
}

async function checkExternalClips() {
  try {
    if (!externalPlan) throw new Error(t('createPromptsFirst'));
    if (!selectedExternalClipsFolder) throw new Error(t('chooseFolder'));
    $('#externalCheckButton').disabled = true;
    showError($('#externalError'), t('checkingClips'));
    const result = await window.vyt.validateExternalClips(externalPlan.planPath, selectedExternalClipsFolder.path);
    const report = result.report;
    const summary = $('#externalClipReport');
    summary.hidden = false;
    summary.classList.toggle('warning', !report.ready);
    if (report.ready) {
      summary.innerHTML = `<b>${t('allReady')}</b> ${tr('allReadyHelp', { count: report.ready_numbers.length })}`;
      $('#externalRenderButton').disabled = false;
    } else {
      const problems = [report.missing.length ? tr('missing', { numbers: report.missing.map((n) => String(n).padStart(3, '0')).join(', ') }) : '', report.too_short.length ? tr('tooShort', { clips: report.too_short.map((item) => `${String(item.number).padStart(3, '0')} (${item.actual}s / ${item.required}s)`).join(', ') }) : ''].filter(Boolean);
      summary.innerHTML = `<b>${t('cannotAssemble')}</b><ul>${problems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
      $('#externalRenderButton').disabled = true;
    }
    showError($('#externalError'), '');
  } catch (error) { showError($('#externalError'), error.message); }
  finally { $('#externalCheckButton').disabled = false; }
}

async function renderExternalVideo() {
  try {
    const title = $('#externalTitleInput').value.trim();
    if (!title) throw new Error(t('enterFinalTitle'));
    if (!externalPlan || !selectedExternalAudio || !selectedExternalClipsFolder) throw new Error(t('completeSteps'));
    $('#externalRenderButton').disabled = true;
    showError($('#externalError'), t('queueAssembly'));
    await window.vyt.renderExternal({ title, planPath: externalPlan.planPath, audio: selectedExternalAudio.path, clipsFolder: selectedExternalClipsFolder.path });
    showError($('#externalError'), ''); showToast(t('assemblyQueued'));
  } catch (error) { showError($('#externalError'), error.message); $('#externalRenderButton').disabled = false; }
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
    if (!selectedVideo) throw new Error(t('addHeygenFirst'));
    const title = $('#titleInput').value.trim();
    if (!title) throw new Error(t('enterTitle'));
    $('#createButton').disabled = true; $('#testButton').disabled = true;
    const productEnabled = $('#productQrToggle').checked;
    if (productEnabled && !selectedProductQr) throw new Error(t('chooseQr'));
    const cardDataUrl = productEnabled ? selectedProductQr.dataUrl : '';
    showError($('#formError'), testMode ? t('checkingTest') : t('checkingFull'));
    await window.vyt.createJob({
      title, source: selectedVideo.path, branding: $('#brandingToggle').checked, testMode,
      productSale: productEnabled ? {
        enabled: true,
        cardDataUrl
      } : { enabled: false }
    });
    $('#createButton').disabled = false; $('#testButton').disabled = false;
    showError($('#formError'), '');
    showToast(t('testQueued'));
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
  $('#jobsList').innerHTML = active.map((job) => {
    const cost = job.external
      ? t('externalCost')
      : `${Number(job.spentUsd || 0).toFixed(2)} $ / ${Number(job.maxCostUsd || (job.testMode ? 1.5 : 7)).toFixed(2)} $`;
    return `
    <article class="job-card">
      <div class="job-title-row"><strong>${escapeHtml(job.title)}</strong><span class="job-percent">${Math.round(job.progress || 0)}%</span></div>
      <div class="progress-track"><i style="width:${Math.max(0, Math.min(100, job.progress || 0))}%"></i></div>
      <div class="job-detail"><span>${escapeHtml(displayPhase(job.phase || statusLabel('queued')))}</span><span>${job.status === 'queued' ? t('waiting') : formatEta(job.etaSeconds)}</span></div>
      <div class="job-cost"><span>${escapeHtml(job.detail || (job.external ? t('externalEdit') : (job.testMode ? t('test') : t('createFull'))))}</span><b>${cost}</b></div>
      <div class="job-metrics"><span>${escapeHtml(statusLabel(job.status))}</span><span>${costMetric(job.costLedger, 'avoided_duplicate')} ${t('recovered') || 'recuperados'}</span><span>${costMetric(job.costLedger, 'charged')} ${t('charged') || 'cobrados'}</span><span>${operationMetric(job.providerOperations)} ${t('operations') || 'operaciones'}</span></div>
      ${job.warning ? `<div class="job-warning">${escapeHtml(job.warning)}</div>` : ''}
      <div class="job-actions"><button data-cancel="${job.id}">${t('cancel')}</button></div>
    </article>`;
  }).join('');
  $$('[data-cancel]').forEach((button) => button.onclick = () => window.vyt.cancelJob(button.dataset.cancel));
}

function statusLabel(status) {
  const labels = { queued: t('statusQueued'), running: t('statusRunning'), paused: t('statusPaused'), waiting_for_provider: t('statusProvider'), waiting_for_download: t('statusDownload'), rendering: t('statusRendering'), recoverable: t('statusRecoverable') };
  return labels[status] || status || labels.queued;
}

function costMetric(ledger, kind) {
  const value = Number(ledger?.[kind] || 0);
  return `${value.toFixed(2)} $`;
}

function operationMetric(summary) {
  const waiting = ['submitted', 'polling', 'download_pending']
    .reduce((total, status) => total + Number(summary?.[status] || 0), 0);
  return `${waiting} ${t('recoveryOperations')}`;
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
      ? ` · ${counts.video || 0} ${t('clips').toLowerCase()} · ${counts.image || 0} ${t('images').toLowerCase()} · ${counts.avatar || 0} ${t('avatar').toLowerCase()}`
      : '';
    const costDetail = Object.keys(breakdown).length
      ? `${t('video')} ${Number(breakdown.video || 0).toFixed(2)} $ · ${t('images').toLowerCase()} ${Number(breakdown.image || 0).toFixed(2)} $ · ${t('analysisReview')} ${Number((breakdown.analysis || 0) + (breakdown.review || 0)).toFixed(2)} $`
      : '';
    return `
    <article class="history-item">
      <div><strong>${escapeHtml(item.title)}</strong><small>${item.status === 'completed' ? `${formatDuration(item.duration)} · ${new Date(item.completedAt).toLocaleString(currentLanguage === 'en' ? 'en-US' : 'es-ES')}${production}` : escapeHtml(item.error || t('unfinished'))}</small>${item.warning ? `<small>${escapeHtml(item.warning)}</small>` : ''}${costDetail ? `<small>${costDetail}</small>` : ''}</div>
      <div class="history-cost">${Number(item.costUsd || 0).toFixed(2)} $</div>
      <button data-open="${escapeHtml(item.outputPath)}" ${item.outputPath ? '' : 'disabled'}>${item.status === 'completed' ? t('openVideo') : t('noFile')}</button>
      ${item.resumable ? `<button data-resume="${escapeHtml(item.id)}" class="primary">${item.testMode ? t('resumeTest') : t('resumeVideo')}</button>` : ''}
    </article>`;
  }).join('');
  $$('[data-open]').forEach((button) => button.onclick = () => window.vyt.openOutput(button.dataset.open));
  $$('[data-resume]').forEach((button) => button.onclick = async () => {
    try {
      button.disabled = true;
      const result = await window.vyt.resumeJob(button.dataset.resume);
      showToast(result?.alreadyRunning ? t('resumeActive') : t('resumeQueued'));
      $('[data-view="create"]')?.click();
    } catch (error) {
      button.disabled = false;
      showToast(error.message);
    }
  });
}

function render(state) { appState = state; renderJobs(); renderHistory(); }

function channelStatusLabel(status) {
  return ({ aged: currentLanguage === 'en' ? 'Aged' : 'Antiguo', active: currentLanguage === 'en' ? 'Active' : 'Activo', under_review: currentLanguage === 'en' ? 'Under review' : 'En revisión', monetized: currentLanguage === 'en' ? 'Monetized' : 'Monetizado' })[status] || status;
}

function renderChannelFilters() {
  const filter = $('#channelProxyFilter');
  const current = filter.value;
  filter.innerHTML = `<option value="">${t('allProxies')}</option><option value="none">${t('noProxy')}</option>` +
    channelsState.proxies.map((proxy) => `<option value="${proxy.id}">${escapeHtml(proxy.label || proxy.host || `Proxy ${proxy.id}`)}</option>`).join('');
  filter.value = [...filter.options].some((option) => option.value === current) ? current : '';
  $('#channelProxy').innerHTML = `<option value="">${t('noProxy')}</option>` + channelsState.proxies.map((proxy) =>
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
  $('#toggleChannelEmails').textContent = showChannelEmails ? t('hideEmails') : t('showEmails');
  $('#channelsEmpty').style.display = items.length ? 'none' : 'block';
  $('#channelsEmpty').textContent = channelsLoaded ? t('noMatches') : t('loading');
  $('#channelsBody').innerHTML = items.map((channel) => {
    const identity = channel.channel_name || channel.gmail || t('unnamed');
    return `<tr>
      <td><div class="channel-identity"><span class="channel-initial">${escapeHtml(identity[0]?.toUpperCase() || '?')}</span><div><strong>${escapeHtml(channel.channel_name || t('unnamed'))}</strong><span class="channel-email ${showChannelEmails ? '' : 'concealed'}">${escapeHtml(channel.gmail || t('noEmail'))}</span></div></div></td>
      <td>${channel.proxy ? `<div class="channel-proxy"><b>${escapeHtml(channel.proxy.port || '—')}</b><span>${escapeHtml(channel.proxy.host || channel.proxy.label || '')}</span></div>` : `<span class="channel-muted">${t('unassigned')}</span>`}</td>
      <td><span class="channel-status ${escapeHtml(channel.status)}">${escapeHtml(channelStatusLabel(channel.status))}</span></td>
      <td><div class="channel-actions"><button data-channel-copy="${channel.id}">${t('copyAccess')}</button><button class="danger" data-channel-delete="${channel.id}">${t('delete')}</button><button class="primary" data-channel-edit="${channel.id}">${t('edit')}</button></div></td>
    </tr>`;
  }).join('');
  $$('[data-channel-copy]').forEach((button) => button.onclick = async () => {
    try { await window.vyt.copyChannelAccess(Number(button.dataset.channelCopy)); showToast(t('copied')); }
    catch (error) { showError($('#channelsError'), error.message); }
  });
  $$('[data-channel-delete]').forEach((button) => button.onclick = () => deleteSavedChannel(Number(button.dataset.channelDelete), button));
  $$('[data-channel-edit]').forEach((button) => button.onclick = () => openChannelEditor(Number(button.dataset.channelEdit)));
}

async function deleteSavedChannel(channelId, button) {
  const channel = channelsState.channels.find((item) => item.id === channelId);
  const identity = channel?.channel_name || channel?.gmail || t('channel');
  const confirmed = window.confirm(tr('deleteChannel', { name: identity }));
  if (!confirmed) return;
  try {
    showError($('#channelsError'), '');
    button.disabled = true;
    await window.vyt.deleteChannel(channelId);
    await loadChannels();
    showToast(t('deleted'));
  } catch (error) {
    showError($('#channelsError'), error.message);
    button.disabled = false;
  }
}

async function loadChannels() {
  try {
    showError($('#channelsError'), '');
    $('#channelsConnection').textContent = t('connecting');
    const result = await window.vyt.channelsList();
    channelsState = result;
    channelsLoaded = true;
    $('#channelsConnection').textContent = t('connected');
    $('#channelsConnection').classList.add('connected');
    renderChannelFilters();
    renderChannels();
  } catch (error) {
    channelsLoaded = true;
    $('#channelsConnection').textContent = t('unavailable');
    $('#channelsConnection').classList.remove('connected');
    $('#channelsEmpty').textContent = t('channelsUnavailable');
    showError($('#channelsError'), error.message);
  }
}

function setChannelField(selector, value) { $(selector).value = value ?? ''; }

function renderEditableLabels() {
  $('#channelLabels').innerHTML = channelsState.labels.length ? channelsState.labels.map((label) =>
    `<button type="button" class="channel-label ${selectedChannelLabels.has(label.id) ? 'selected' : ''}" data-channel-label="${label.id}" style="--label-color:${escapeHtml(label.color || '#8a7355')}">${escapeHtml(label.name)}</button>`
  ).join('') : `<span class="channel-muted">${t('noLabels')}</span>`;
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
      if (!found) throw new Error(t('channelNotFound'));
      const secrets = await window.vyt.channelSecrets(channelId);
      channel = { ...channel, ...found, ...secrets };
    }
    $('#channelDialogTitle').textContent = channelId ? t('editChannel') : t('channelNew');
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
    showToast(t('channelSaved'));
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
  if (!rows.length) { showError($('#channelsImportError'), t('importInvalid')); return; }
  try {
    showError($('#channelsImportError'), '');
    $('#runChannelsImport').disabled = true;
    for (const row of rows) await window.vyt.saveChannel(row);
    $('#importChannelsDialog').close();
    $('#channelsImportText').value = '';
    await loadChannels();
    showToast(`${rows.length} ${rows.length === 1 ? t('channelImported') : t('channelsImported')}`);
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
$$('.workflow-tab').forEach((button) => button.onclick = () => switchWorkflow(button.dataset.workflow));
$('#externalAudioButton').onclick = selectExternalAudio;
$('#externalAudioZone').onclick = (event) => { if (event.target.id !== 'externalAudioButton') selectExternalAudio(); };
$('#externalPlanButton').onclick = createExternalPlan;
$('#externalClipsButton').onclick = selectExternalClipsFolder;
$('#externalClipsZone').onclick = (event) => { if (event.target.id !== 'externalClipsButton') selectExternalClipsFolder(); };
$('#externalCheckButton').onclick = checkExternalClips;
$('#externalRenderButton').onclick = renderExternalVideo;
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
    showToast(t('languageChanged'));
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
    showError($('#settingsError'), t('connectionsChecking'));
    await window.vyt.saveSettings({
      geminigenKey: $('#geminigenKey').value,
      algrowKey: $('#algrowKey').value,
      gatewayKey: $('#gatewayKey').value,
      language: $('#languageSelect').value
    });
    const result = await window.vyt.testSettings();
    const connectionMessages = [...(result.errors || []), ...(result.warnings || [])];
    showError($('#settingsError'), connectionMessages.length ? connectionMessages.join(' · ') : t('connectionsGood'));
    await refreshSettings();
  } catch (error) { showError($('#settingsError'), error.message); }
};

window.vyt.onState((state) => { if (state.language) applyLanguage(state.language); render(state); });
window.vyt.getState().then((state) => { if (state.language) applyLanguage(state.language); render(state); });
refreshSettings();
