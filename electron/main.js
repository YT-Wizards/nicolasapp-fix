const { app, BrowserWindow, clipboard, dialog, ipcMain, safeStorage, shell } = require('electron');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const channels = require('./channels');
const { isFullVideoDurationAllowed } = require('./duration');
const { JobStore } = require('./job-store');
const { selectRunnableJobs } = require('./job-queue');

const MAX_RUNNING = 2;
const FULL_MAX_COST_USD = 7.0;
const TEST_MAX_COST_USD = 1.5;
const state = { jobs: [], history: [] };
const processes = new Map();
let mainWindow = null;
let lastConnectionCheck = null;
let jobStore = null;
let appIsQuitting = false;

const rootDir = () => {
  if (!app.isPackaged) return path.resolve(__dirname, '..');
  const unpacked = path.join(process.resourcesPath, 'app.asar.unpacked');
  return fs.existsSync(path.join(unpacked, 'engine', 'vyt.py')) ? unpacked : process.resourcesPath;
};
const uiFile = (name) => app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar', 'ui', name)
  : path.join(path.resolve(__dirname, '..'), 'ui', name);
const userFile = (name) => path.join(app.getPath('userData'), name);
const nowIso = () => new Date().toISOString();

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  fs.renameSync(temp, file);
}

function loadPersistentState() {
  jobStore = new JobStore(userFile('vyt.sqlite'));
  jobStore.recoverInterruptedJobs();
  state.jobs = jobStore.loadJobs();
  const saved = readJson(userFile('history.json'), { history: [] });
  state.history = Array.isArray(saved.history) ? saved.history.slice(0, 50) : [];
  const cardsDir = userFile('product-cards');
  const referencedCards = new Set(
    state.jobs.map((job) => job.productSale?.cardPath).filter(Boolean),
  );
  const staleBefore = Date.now() - 30 * 24 * 60 * 60 * 1000;
  try {
    for (const name of fs.readdirSync(cardsDir)) {
      const candidate = path.join(cardsDir, name);
      if (!referencedCards.has(candidate) && fs.statSync(candidate).mtimeMs < staleBefore) fs.unlinkSync(candidate);
    }
  } catch { /* No temporary product cards yet. */ }
}

function persistHistory() {
  writeJson(userFile('history.json'), { history: state.history.slice(0, 50) });
}

function publicState() {
  return {
    jobs: state.jobs.map(({ secretConfig, productSale, ...job }) => ({
      ...job,
      productSale: { enabled: Boolean(productSale?.enabled) },
      costLedger: jobStore?.costTotals(job.id) || {},
    })),
    history: state.history
  };
}

function broadcast() {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('state', publicState());
}

function encryptedSettings() {
  return readJson(userFile('settings.secure.json'), {});
}

function decryptValue(value) {
  if (!value || !safeStorage.isEncryptionAvailable()) return '';
  try { return safeStorage.decryptString(Buffer.from(value, 'base64')); } catch { return ''; }
}

function encryptValue(value) {
  if (!value) return '';
  if (!safeStorage.isEncryptionAvailable()) throw new Error('El llavero seguro de macOS no está disponible.');
  return safeStorage.encryptString(value).toString('base64');
}

function getSecrets() {
  const saved = encryptedSettings();
  return {
    geminigenKey: decryptValue(saved.geminigenKey),
    algrowKey: decryptValue(saved.algrowKey),
    gatewayKey: decryptValue(saved.gatewayKey)
  };
}

function settingsStatus() {
  const s = getSecrets();
  return {
    geminigen: Boolean(s.geminigenKey),
    algrow: Boolean(s.algrowKey),
    gateway: Boolean(s.gatewayKey)
  };
}

async function diagnoseAlgrowJob(jobId) {
  const safeJobId = String(jobId || '').trim();
  if (!/^[a-f0-9-]{20,80}$/i.test(safeJobId)) throw new Error('Identificador de Algrow no válido.');
  const key = getSecrets().algrowKey;
  if (!key) throw new Error('No hay una clave de Algrow guardada.');
  const response = await fetch(
    `https://api.algrow.online/api/job-status/${encodeURIComponent(safeJobId)}`,
    { headers: { Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(20000) },
  );
  const raw = await response.text();
  let body = {};
  try { body = JSON.parse(raw); } catch { body = {}; }
  return {
    http: response.status,
    status: body.status || body.state || null,
    error: body.error || body.message || null,
    hasImage: Boolean(body.image_url || (body.image_urls || []).length),
  };
}

function execFilePromise(command, args) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout: 20000 }, (error, stdout, stderr) => {
      if (error) reject(new Error(stderr.trim() || error.message));
      else resolve(stdout);
    });
  });
}

function findBinary(names) {
  for (const candidate of names) if (fs.existsSync(candidate)) return candidate;
  return names[names.length - 1];
}

const FFPROBE = findBinary(['/opt/homebrew/bin/ffprobe', '/usr/local/bin/ffprobe', 'ffprobe']);
const PYTHON = findBinary(['/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3']);

async function inspectVideo(filePath) {
  if (!filePath || !fs.existsSync(filePath)) throw new Error('No encuentro el vídeo seleccionado.');
  const raw = await execFilePromise(FFPROBE, [
    '-v', 'error', '-show_entries', 'format=duration:stream=width,height,codec_type', '-of', 'json', filePath
  ]);
  const info = JSON.parse(raw);
  const video = (info.streams || []).find((stream) => stream.codec_type === 'video') || {};
  return {
    path: filePath,
    name: path.basename(filePath),
    duration: Number(info.format?.duration || 0),
    width: Number(video.width || 0),
    height: Number(video.height || 0),
    hasAudio: (info.streams || []).some((stream) => stream.codec_type === 'audio')
  };
}

function sanitizeTitle(title) {
  return String(title || 'video-vyt')
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[\\/:*?"<>|]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 110) || 'video-vyt';
}

function samePath(left, right) {
  if (!left || !right) return false;
  return path.resolve(left).toLocaleLowerCase() === path.resolve(right).toLocaleLowerCase();
}

function saveProductCard(jobId, dataUrl) {
  const match = String(dataUrl || '').match(/^data:image\/png;base64,([A-Za-z0-9+/=]+)$/);
  if (!match) throw new Error('No se pudo preparar la tarjeta del QR. Vuelve a seleccionar la imagen.');
  const bytes = Buffer.from(match[1], 'base64');
  if (bytes.length < 500 || bytes.length > 6 * 1024 * 1024) throw new Error('La imagen del QR no es válida o pesa demasiado.');
  const directory = userFile('product-cards');
  fs.mkdirSync(directory, { recursive: true });
  const output = path.join(directory, `${jobId}.png`);
  fs.writeFileSync(output, bytes, { mode: 0o600 });
  return output;
}

function removeProductCard(job) {
  try {
    if (job?.productSale?.cardPath) fs.unlinkSync(job.productSale.cardPath);
  } catch { /* Best-effort cleanup after the renderer has finished. */ }
}

function chooseOutputPath(title, source) {
  const downloads = app.getPath('downloads');
  const cleanTitle = sanitizeTitle(title);
  const candidates = [
    `${cleanTitle}.mp4`,
    `${cleanTitle} - VYT.mp4`,
    ...Array.from({ length: 98 }, (_, index) => `${cleanTitle} - VYT (${index + 2}).mp4`)
  ];
  for (const name of candidates) {
    const candidate = path.join(downloads, name);
    const claimed = state.jobs.some((item) => ['queued', 'running'].includes(item.status) && samePath(item.outputPath, candidate));
    if (!samePath(candidate, source) && !fs.existsSync(candidate) && !claimed) return candidate;
  }
  return path.join(downloads, `${cleanTitle} - VYT-${crypto.randomUUID().slice(0, 8)}.mp4`);
}

function updateJob(jobId, patch) {
  const job = state.jobs.find((item) => item.id === jobId);
  if (!job) return;
  const cleanPatch = Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== undefined));
  Object.assign(job, cleanPatch, { updatedAt: nowIso() });
  if (jobStore) jobStore.upsertJob(job);
  broadcast();
}

function parseEngineLine(jobId, line) {
  if (!line.startsWith('VYT_EVENT:')) return;
  try {
    const event = JSON.parse(line.slice('VYT_EVENT:'.length));
    updateJob(jobId, {
      status: event.status || state.jobs.find((j) => j.id === jobId)?.status,
      progress: Number.isFinite(event.progress) ? event.progress : undefined,
      phase: event.phase,
      detail: event.detail,
      etaSeconds: Number.isFinite(event.eta_seconds) ? event.eta_seconds : undefined,
      spentUsd: Number.isFinite(event.spent_usd) ? event.spent_usd : undefined,
      estimateUsd: Number.isFinite(event.estimate_usd) ? event.estimate_usd : undefined
    });
  } catch { /* Ignore non-protocol output. */ }
}

function completeJob(job, result) {
  const historyItem = {
    id: job.id,
    title: job.title,
    status: result.ok ? 'completed' : 'failed',
    outputPath: result.output_path || '',
    costUsd: Number(result.cost_usd || job.spentUsd || 0),
    duration: job.duration,
    createdAt: job.createdAt,
    completedAt: nowIso(),
    error: result.error || '',
    costBreakdown: result.cost_breakdown || {},
    generatedCounts: result.generated_counts || {},
    requestedCounts: result.requested_counts || {},
    failures: Array.isArray(result.failures) ? result.failures : [],
    warning: result.warning || '',
    maxCostUsd: Number(job.maxCostUsd || (job.testMode ? TEST_MAX_COST_USD : FULL_MAX_COST_USD))
  };
  state.history.unshift(historyItem);
  persistHistory();
  updateJob(job.id, {
    status: result.ok ? 'completed' : 'failed',
    progress: result.ok ? 100 : job.progress,
    phase: result.ok ? 'Vídeo terminado' : 'No se pudo terminar',
    detail: result.ok ? 'Guardado en Descargas' : result.error,
    outputPath: historyItem.outputPath,
    spentUsd: historyItem.costUsd,
    error: historyItem.error
  });
  removeProductCard(job);
}

function runNextJobs() {
  // A cancelled job keeps its slot until the Python process has really closed.
  // This prevents a slow provider call from briefly creating a third process.
  selectRunnableJobs(state.jobs, processes.keys(), MAX_RUNNING).forEach(startJob);
}

function startJob(job) {
  const secrets = getSecrets();
  const engine = path.join(rootDir(), 'engine', 'vyt.py');
  const outputPath = job.outputPath || chooseOutputPath(job.title, job.source);
  const args = [engine, '--job', JSON.stringify({
    id: job.id,
    title: job.title,
    source: job.source,
    output: outputPath,
    branding: Boolean(job.branding),
    test_seconds: job.testMode ? 90 : 0,
    duration: job.duration,
    max_cost_usd: Number(job.maxCostUsd || (job.testMode ? TEST_MAX_COST_USD : FULL_MAX_COST_USD)),
    root_dir: rootDir(),
    user_data_dir: app.getPath('userData'),
    product_sale: job.productSale?.enabled ? {
      enabled: true,
      product_name: '',
      card_path: job.productSale.cardPath
    } : { enabled: false }
  })];
  const child = spawn(PYTHON, args, {
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      VYT_GEMINIGEN_KEY: secrets.geminigenKey,
      VYT_ALGROW_KEY: secrets.algrowKey,
      VYT_GATEWAY_KEY: secrets.gatewayKey
    },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  processes.set(job.id, child);
  updateJob(job.id, { status: 'running', phase: 'Preparando el vídeo', progress: 1, outputPath });

  let stdoutBuffer = '';
  let stderrTail = '';
  let engineResult = null;
  let spawnError = null;
  child.stdout.on('data', (chunk) => {
    stdoutBuffer += chunk.toString();
    const lines = stdoutBuffer.split(/\r?\n/);
    stdoutBuffer = lines.pop() || '';
    lines.forEach((line) => {
      parseEngineLine(job.id, line);
      if (line.startsWith('VYT_RESULT:')) {
        try { engineResult = JSON.parse(line.slice('VYT_RESULT:'.length)); } catch { /* Keep reading. */ }
      }
    });
  });
  child.stderr.on('data', (chunk) => { stderrTail = `${stderrTail}${chunk}`.slice(-8000); });
  child.on('error', (error) => { spawnError = error; });
  child.on('close', (code) => {
    processes.delete(job.id);
    if (job.status === 'cancelled' || (appIsQuitting && job.status === 'paused')) {
      if (jobStore) jobStore.upsertJob(job);
      if (!appIsQuitting) broadcast();
      runNextJobs();
      return;
    }
    let result = engineResult;
    for (const line of `${stdoutBuffer}\n${stderrTail}`.split(/\r?\n/)) {
      if (line.startsWith('VYT_RESULT:')) {
        try { result = JSON.parse(line.slice('VYT_RESULT:'.length)); } catch { /* noop */ }
      }
    }
    if (!result) result = {
      ok: code === 0 && fs.existsSync(outputPath),
      output_path: fs.existsSync(outputPath) ? outputPath : '',
      error: spawnError?.message || stderrTail.trim() || `El proceso terminó con código ${code}.`
    };
    completeJob(job, result);
    runNextJobs();
  });
}

async function createJob(payload) {
  const sourceIdentity = path.resolve(String(payload.source || '')).toLocaleLowerCase();
  const duplicate = jobStore?.findActiveBySource(sourceIdentity) || state.jobs.find((job) =>
    !['completed', 'failed', 'cancelled'].includes(job.status) && samePath(job.source, payload.source)
  );
  if (duplicate) throw new Error('Ese mismo vídeo ya está en producción. Espera a que termine o cancélalo antes de volver a añadirlo.');
  const settings = settingsStatus();
  if (!settings.geminigen || !settings.algrow || !settings.gateway) {
    throw new Error('Primero conecta GeminiGen, Algrow y Vercel AI Gateway en Ajustes.');
  }
  const recentCheck = lastConnectionCheck && (Date.now() - lastConnectionCheck.at < 10 * 60 * 1000)
    ? lastConnectionCheck.result
    : await testSettings();
  if (recentCheck.errors.length) throw new Error(recentCheck.errors.join(' · '));
  // Free public health gate. Do this before video inspection, transcription and
  // paid AI analysis so a provider incident never creates a failed history item.
  let veoHealth;
  try {
    const response = await fetch('https://api.snapgen.ai/api/v1/models/status?window=1h', {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    veoHealth = (payload.models || []).find((item) => item.group_key === 'veo-3.1-fast');
  } catch {
    throw new Error('VYT no pudo comprobar gratuitamente el estado de Veo. No se inició el trabajo para proteger tus créditos.');
  }
  if (!veoHealth) {
    throw new Error('SnapGen no publicó el estado de Veo 3.1 Fast. VYT no inició el trabajo para proteger tus créditos.');
  }
  const veoSuccessRate = Number(veoHealth.success_rate || 0);
  if (String(veoHealth.status || '').toLowerCase() !== 'operational' || veoSuccessRate < 80) {
    throw new Error(`Veo 3.1 Fast está inestable en SnapGen (${Math.round(veoSuccessRate)}% de éxito). VYT no inició el trabajo ni gastó créditos. Inténtalo más tarde.`);
  }
  const info = await inspectVideo(payload.source);
  const effectiveDuration = payload.testMode ? Math.min(90, info.duration) : info.duration;
  if (info.width <= info.height) throw new Error('El vídeo de HeyGen debe ser horizontal.');
  if (!info.hasAudio) throw new Error('El vídeo de HeyGen no contiene una pista de audio.');
  if (!payload.testMode && !isFullVideoDurationAllowed(effectiveDuration)) {
    throw new Error('El vídeo completo debe durar entre 8 y 35 minutos. Para validar un vídeo más corto usa “Prueba de 90 s”.');
  }
  const jobId = crypto.randomUUID();
  let productSale = { enabled: false };
  if (payload.productSale?.enabled) {
    productSale = {
      enabled: true,
      cardPath: saveProductCard(jobId, payload.productSale.cardDataUrl)
    };
  }
  const job = {
    id: jobId,
    title: sanitizeTitle(payload.title),
    source: payload.source,
    sourceIdentity,
    duration: effectiveDuration,
    branding: Boolean(payload.branding),
    productSale,
    testMode: Boolean(payload.testMode),
    maxCostUsd: payload.testMode ? TEST_MAX_COST_USD : FULL_MAX_COST_USD,
    status: 'queued',
    progress: 0,
    phase: 'En cola',
    detail: '',
    etaSeconds: null,
    spentUsd: 0,
    estimateUsd: 0,
    outputPath: '',
    createdAt: nowIso(),
    updatedAt: nowIso()
  };
  state.jobs.unshift(job);
  jobStore.upsertJob(job);
  broadcast();
  runNextJobs();
  return { ok: true, id: job.id };
}

async function testJson(url, headers) {
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function testGatewayModel(apiKey, model, order) {
  const response = await fetch('https://ai-gateway.vercel.sh/v1/chat/completions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      max_tokens: 64,
      messages: [{ role: 'user', content: 'Reply with exactly OK.' }],
      providerOptions: { gateway: { order } }
    }),
    signal: AbortSignal.timeout(45000)
  });
  if (!response.ok) {
    const body = await response.text();
    if (response.status === 403 && /credit card|customer_verification_required/i.test(body)) {
      throw new Error('Vercel necesita una tarjeta verificada para activar el uso de AI Gateway y los créditos gratuitos.');
    }
    if (response.status === 403 && /free tier|paid credits|top-up/i.test(body)) {
      throw new Error('Vercel no permite usar este modelo con saldo gratuito; hay que añadir saldo de pago en AI Gateway.');
    }
    throw new Error(`HTTP ${response.status}: ${body.slice(0, 350)}`);
  }
  const data = await response.json();
  if (!data?.choices?.[0]?.message) throw new Error('respuesta vacía');
  return true;
}

async function testSettings() {
  const s = getSecrets();
  const result = { geminigen: false, algrow: false, gateway: false, errors: [] };
  if (s.geminigenKey) {
    try {
      const response = await fetch('https://api.snapgen.ai/uapi/v1/history/vyt-connection-check', {
        headers: { 'x-api-key': s.geminigenKey, Accept: 'application/json' },
        signal: AbortSignal.timeout(15000)
      });
      const body = await response.text();
      const invalidKey = response.status === 401 || response.status === 403 || /API_KEY_NOT_FOUND|incorrect api key|invalid api key/i.test(body);
      result.geminigen = !invalidKey;
      if (!result.geminigen) result.errors.push('GeminiGen: la API key guardada ya no es válida. Crea y guarda una nueva.');
    }
    catch (error) { result.errors.push(`GeminiGen: ${error.message}`); }
  }
  if (s.algrowKey) {
    try {
      const response = await fetch('https://api.algrow.online/api/job-status/vyt-connection-check', { headers: { Authorization: `Bearer ${s.algrowKey}` }, signal: AbortSignal.timeout(15000) });
      result.algrow = response.status !== 401 && response.status !== 403;
      if (!result.algrow) result.errors.push(`Algrow: HTTP ${response.status}`);
    } catch (error) { result.errors.push(`Algrow: ${error.message}`); }
  }
  if (s.gatewayKey) {
    let claudeReady = false;
    let geminiReady = false;
    try {
      await testGatewayModel(s.gatewayKey, 'anthropic/claude-sonnet-5', ['anthropic', 'vertex', 'bedrock', 'claudeaws']);
      claudeReady = true;
    } catch (error) { result.errors.push(`Claude: ${error.message}`); }
    try {
      await testGatewayModel(s.gatewayKey, 'google/gemini-2.5-flash', ['google', 'vertex']);
      geminiReady = true;
    } catch (error) { result.errors.push(`Gemini: ${error.message}`); }
    result.gateway = claudeReady && geminiReady;
  }
  lastConnectionCheck = { at: Date.now(), result };
  return result;
}

function testOneVeoClip() {
  return new Promise((resolve, reject) => {
    const key = getSecrets().geminigenKey;
    if (!key) {
      reject(new Error('No se pudo recuperar la clave de GeminiGen guardada en VYT.'));
      return;
    }
    const engineDir = path.join(rootDir(), 'engine');
    const output = path.join(app.getPath('downloads'), 'VYT-comprobacion-Veo.mp4');
    const code = [
      'import os, sys',
      `sys.path.insert(0, ${JSON.stringify(engineDir)})`,
      `sys.path.insert(0, ${JSON.stringify(path.join(engineDir, 'vendor'))})`,
      'from providers import GeminiGenClient',
      'prompt = "Raw ordinary handheld smartphone documentary video. A Belgian Malinois walks calmly beside a dog handler across a plain outdoor training field under an overcast sky. Natural movement, deep focus, muted colors, realistic compression, no cinematic lighting, no text, no logos, no watermark."',
      `result = GeminiGenClient(os.environ['VYT_GEMINIGEN_KEY']).generate_video(prompt, ${JSON.stringify(output)})`,
      'print("VEO_OK:" + str(result), flush=True)',
    ].join('\n');
    const child = spawn(PYTHON, ['-c', code], {
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: '1',
        VYT_GEMINIGEN_KEY: key,
      },
      stdio: ['ignore', 'pipe', 'pipe']
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('error', reject);
    child.on('close', (codeValue) => {
      if (codeValue === 0 && fs.existsSync(output)) resolve({ output, stdout: stdout.trim() });
      else reject(new Error(stderr.trim() || stdout.trim() || `La comprobación terminó con código ${codeValue}.`));
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 1040,
    minHeight: 700,
    backgroundColor: '#090b0e',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });
  mainWindow.loadFile(uiFile('index.html'));
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  loadPersistentState();
  if (process.argv.includes('--test-veo-one')) {
    try { console.log('VYT_VEO_TEST:' + JSON.stringify({ ok: true, ...(await testOneVeoClip()) })); }
    catch (error) { console.log('VYT_VEO_TEST:' + JSON.stringify({ ok: false, error: error.message })); }
    app.quit();
    return;
  }
  if (process.argv.includes('--diagnose-connections')) {
    try { console.log('VYT_DIAGNOSTIC:' + JSON.stringify(await testSettings())); }
    catch (error) { console.log('VYT_DIAGNOSTIC:' + JSON.stringify({ errors: [error.message] })); }
    app.quit();
    return;
  }
  const algrowDiagnostic = process.argv.find((value) => value.startsWith('--diagnose-algrow-job='));
  if (algrowDiagnostic) {
    try {
      const jobId = algrowDiagnostic.slice('--diagnose-algrow-job='.length);
      console.log('VYT_ALGROW_DIAGNOSTIC:' + JSON.stringify(await diagnoseAlgrowJob(jobId)));
    } catch (error) {
      console.log('VYT_ALGROW_DIAGNOSTIC:' + JSON.stringify({ error: error.message }));
    }
    app.quit();
    return;
  }
  createWindow();
  runNextJobs();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', () => {
  appIsQuitting = true;
  for (const job of state.jobs) {
    if (processes.has(job.id) && job.status === 'running') {
      updateJob(job.id, {
        status: 'paused',
        phase: 'Пауза',
        detail: 'Приложение закрывается; задание будет продолжено после запуска.'
      });
    }
  }
  for (const child of processes.values()) {
    if (!child.killed) child.kill('SIGTERM');
  }
});

ipcMain.handle('choose-video', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Selecciona el vídeo completo de HeyGen',
    properties: ['openFile'],
    filters: [{ name: 'Vídeo', extensions: ['mp4', 'mov', 'm4v'] }]
  });
  return result.canceled ? null : inspectVideo(result.filePaths[0]);
});
ipcMain.handle('choose-product-qr', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Selecciona el QR del producto',
    properties: ['openFile'],
    filters: [{ name: 'Imagen del QR', extensions: ['png', 'jpg', 'jpeg', 'webp'] }]
  });
  if (result.canceled) return null;
  const filePath = result.filePaths[0];
  const bytes = fs.readFileSync(filePath);
  if (bytes.length > 6 * 1024 * 1024) throw new Error('El QR debe pesar menos de 6 MB.');
  const extension = path.extname(filePath).toLowerCase();
  const mime = extension === '.png' ? 'image/png' : extension === '.webp' ? 'image/webp' : 'image/jpeg';
  return { name: path.basename(filePath), dataUrl: `data:${mime};base64,${bytes.toString('base64')}` };
});
ipcMain.handle('inspect-video', (_event, filePath) => inspectVideo(filePath));
ipcMain.handle('create-job', (_event, payload) => createJob(payload));
ipcMain.handle('get-state', () => publicState());
ipcMain.handle('settings-status', () => settingsStatus());
ipcMain.handle('settings-save', (_event, incoming) => {
  const previous = encryptedSettings();
  const next = { ...previous };
  for (const key of ['geminigenKey', 'algrowKey', 'gatewayKey']) {
    if (typeof incoming[key] === 'string' && incoming[key].trim()) next[key] = encryptValue(incoming[key].trim());
  }
  delete next.openrouterKey;
  delete next.plannerModel;
  writeJson(userFile('settings.secure.json'), next);
  lastConnectionCheck = null;
  return settingsStatus();
});
ipcMain.handle('settings-test', () => testSettings());
ipcMain.handle('reveal-output', (_event, filePath) => { if (filePath) shell.showItemInFolder(filePath); });
ipcMain.handle('open-output', (_event, filePath) => filePath ? shell.openPath(filePath) : '');
ipcMain.handle('cancel-job', (_event, jobId) => {
  const child = processes.get(jobId);
  if (child) {
    child.kill('SIGTERM');
    const forceStop = setTimeout(() => {
      if (processes.get(jobId) === child && child.exitCode === null && child.pid) {
        try { process.kill(child.pid, 'SIGKILL'); } catch { /* It already closed. */ }
      }
    }, 8000);
    forceStop.unref();
  }
  const job = state.jobs.find((item) => item.id === jobId);
  if (!job) return { ok: false };
  updateJob(jobId, { status: 'cancelled', phase: 'Cancelado', detail: 'El progreso válido queda guardado para poder reanudar.' });
  return { ok: true };
});
// The channels database is deliberately isolated from every video job. These
// handlers never touch state.jobs, the production engine or API credentials.
ipcMain.handle('channels-status', () => channels.status());
ipcMain.handle('channels-list', () => channels.listChannels());
ipcMain.handle('channels-secrets', (_event, channelId) => channels.channelSecrets(channelId));
ipcMain.handle('channels-save', (_event, payload) => channels.saveChannel(payload));
ipcMain.handle('channels-delete', (_event, channelId) => channels.deleteChannel(channelId));
ipcMain.handle('channels-copy-access', async (_event, channelId) => {
  const secrets = await channels.channelSecrets(channelId);
  const values = [secrets.gmail, secrets.password];
  if (secrets.twofa_secret) values.push(secrets.twofa_secret);
  clipboard.writeText(values.join('\t'));
  return { ok: true };
});
