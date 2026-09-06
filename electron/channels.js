const { execFile } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

// Canales remains an independent data module. VYT only talks to the existing
// encrypted database through this small boundary; none of the video pipeline
// imports or depends on it.
const CHANNELS_ROOT = process.env.VYT_CHANNELS_ROOT || path.join(os.homedir(), 'canales');
const DATABASE = process.env.VYT_CHANNELS_DATABASE || path.join(CHANNELS_ROOT, 'data', 'canales.db');
const ENV_FILE = path.join(CHANNELS_ROOT, '.env');
const SQLITE = '/usr/bin/sqlite3';
const STATUSES = new Set(['aged', 'active', 'under_review', 'monetized']);

function parseEnv(file) {
  if (!fs.existsSync(file)) return {};
  const values = {};
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (match) values[match[1]] = match[2].replace(/^["']|["']$/g, '').trim();
  }
  return values;
}

function encryptionKey() {
  const raw = parseEnv(ENV_FILE).ENCRYPTION_KEY || '';
  if (!/^[0-9a-f]{64}$/i.test(raw)) throw new Error('No encuentro la clave segura de la app Canales.');
  return Buffer.from(raw, 'hex');
}

function encrypt(value) {
  if (value == null || value === '') return null;
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', encryptionKey(), iv);
  const data = Buffer.concat([cipher.update(String(value), 'utf8'), cipher.final()]);
  return `v1:${iv.toString('base64')}:${cipher.getAuthTag().toString('base64')}:${data.toString('base64')}`;
}

function decrypt(value) {
  if (value == null || value === '') return '';
  const [version, iv, tag, data] = String(value).split(':');
  if (version !== 'v1' || !iv || !tag || !data) throw new Error('Uno de los accesos guardados está dañado.');
  const decipher = crypto.createDecipheriv('aes-256-gcm', encryptionKey(), Buffer.from(iv, 'base64'));
  decipher.setAuthTag(Buffer.from(tag, 'base64'));
  return Buffer.concat([decipher.update(Buffer.from(data, 'base64')), decipher.final()]).toString('utf8');
}

function literal(value) {
  if (value == null || value === '') return 'NULL';
  return `'${String(value).replaceAll("'", "''")}'`;
}

function integer(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? String(parsed) : 'NULL';
}

function idValue(value) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) throw new Error('Canal no válido.');
  return parsed;
}

function sqlite(sql, expectJson = true) {
  if (!fs.existsSync(DATABASE)) return Promise.reject(new Error('No encuentro la base de datos de Canales.'));
  return new Promise((resolve, reject) => {
    const cleanSql = String(sql).replace(/PRAGMA\s+busy_timeout\s*=\s*\d+\s*;/gi, '');
    const args = expectJson
      ? ['-cmd', '.timeout 5000', '-json', DATABASE, cleanSql]
      : ['-cmd', '.timeout 5000', DATABASE, cleanSql];
    execFile(SQLITE, args, { timeout: 15000, maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        const busy = /locked|busy/i.test(stderr || error.message);
        reject(new Error(busy ? 'Canales está ocupado. Cierra la app Canales separada e inténtalo otra vez.' : (stderr.trim() || error.message)));
        return;
      }
      if (!expectJson || !stdout.trim()) { resolve([]); return; }
      try { resolve(JSON.parse(stdout)); }
      catch { reject(new Error('No se pudieron leer los canales guardados.')); }
    });
  });
}

async function getLabelsByChannel() {
  const rows = await sqlite(`
    PRAGMA busy_timeout=5000;
    SELECT cl.channel_id,l.id,l.name,l.color
    FROM channel_labels cl JOIN labels l ON l.id=cl.label_id
    ORDER BY l.name;
  `);
  const grouped = new Map();
  for (const row of rows) {
    if (!grouped.has(row.channel_id)) grouped.set(row.channel_id, []);
    grouped.get(row.channel_id).push({ id: row.id, name: row.name, color: row.color });
  }
  return grouped;
}

async function listChannels() {
  const [channels, proxies, labelsByChannel, labels] = await Promise.all([
    sqlite(`
      PRAGMA busy_timeout=5000;
      SELECT c.id,c.gmail,c.recovery_email,c.phone,c.channel_url,c.channel_name,c.year,c.source,
             c.status,c.proxy_id,c.notes,c.created_at,c.updated_at,
             CASE WHEN c.password IS NULL OR c.password='' THEN 0 ELSE 1 END AS has_password,
             CASE WHEN c.twofa_secret IS NULL OR c.twofa_secret='' THEN 0 ELSE 1 END AS has_twofa,
             p.id AS proxy_ref_id,p.label AS proxy_label,p.host AS proxy_host,p.port AS proxy_port,
             p.type AS proxy_type,p.status AS proxy_status,p.notes AS proxy_notes
      FROM channels c LEFT JOIN proxies p ON p.id=c.proxy_id
      ORDER BY (c.proxy_id IS NULL),p.port,c.id;
    `),
    sqlite(`
      PRAGMA busy_timeout=5000;
      SELECT p.id,p.label,p.type,p.host,p.port,p.location,p.provider,p.status,p.notes,
             (SELECT COUNT(*) FROM channels c WHERE c.proxy_id=p.id) AS assigned_accounts
      FROM proxies p ORDER BY p.port,p.id;
    `),
    getLabelsByChannel(),
    sqlite('PRAGMA busy_timeout=5000; SELECT id,name,color FROM labels ORDER BY name;')
  ]);
  return {
    connected: true,
    channels: channels.map((row) => ({
      id: row.id,
      gmail: row.gmail || '',
      recovery_email: row.recovery_email || '',
      phone: row.phone || '',
      channel_url: row.channel_url || '',
      channel_name: row.channel_name || '',
      year: row.year || '',
      source: row.source || '',
      status: row.status || 'aged',
      proxy_id: row.proxy_id || '',
      notes: row.notes || '',
      has_password: Boolean(row.has_password),
      has_twofa: Boolean(row.has_twofa),
      labels: labelsByChannel.get(row.id) || [],
      proxy: row.proxy_ref_id ? {
        id: row.proxy_ref_id, label: row.proxy_label || '', host: row.proxy_host || '',
        port: row.proxy_port || '', type: row.proxy_type || '', status: row.proxy_status || '', notes: row.proxy_notes || ''
      } : null
    })),
    proxies,
    labels
  };
}

async function channelSecrets(rawId) {
  const id = idValue(rawId);
  const rows = await sqlite(`PRAGMA busy_timeout=5000; SELECT gmail,password,twofa_secret FROM channels WHERE id=${id};`);
  if (!rows[0]) throw new Error('Canal no encontrado.');
  return {
    gmail: rows[0].gmail || '',
    password: decrypt(rows[0].password),
    twofa_secret: decrypt(rows[0].twofa_secret)
  };
}

function normalizedPayload(payload = {}) {
  return {
    gmail: String(payload.gmail || '').trim(),
    password: payload.password == null ? '' : String(payload.password),
    recovery_email: String(payload.recovery_email || '').trim(),
    phone: String(payload.phone || '').trim(),
    twofa_secret: payload.twofa_secret == null ? '' : String(payload.twofa_secret).trim(),
    channel_url: String(payload.channel_url || '').trim(),
    channel_name: String(payload.channel_name || '').trim(),
    year: Number.isInteger(Number(payload.year)) && Number(payload.year) > 1900 ? Number(payload.year) : null,
    source: String(payload.source || '').trim(),
    status: STATUSES.has(payload.status) ? payload.status : 'aged',
    proxy_id: Number.isInteger(Number(payload.proxy_id)) && Number(payload.proxy_id) > 0 ? Number(payload.proxy_id) : null,
    notes: String(payload.notes || '').trim(),
    label_ids: [...new Set((Array.isArray(payload.label_ids) ? payload.label_ids : []).map(Number).filter((id) => Number.isInteger(id) && id > 0))]
  };
}

function labelSql(channelId, labelIds) {
  const inserts = labelIds.map((labelId) =>
    `INSERT OR IGNORE INTO channel_labels(channel_id,label_id) SELECT ${channelId},id FROM labels WHERE id=${labelId};`
  ).join('\n');
  return `DELETE FROM channel_labels WHERE channel_id=${channelId};\n${inserts}`;
}

async function saveChannel(payload) {
  const item = normalizedPayload(payload);
  let channelId;
  if (payload.id) {
    channelId = idValue(payload.id);
    const existing = await sqlite(`PRAGMA busy_timeout=5000; SELECT password,twofa_secret FROM channels WHERE id=${channelId};`);
    if (!existing[0]) throw new Error('Canal no encontrado.');
    const password = Object.prototype.hasOwnProperty.call(payload, 'password') ? encrypt(item.password) : existing[0].password;
    const twofa = Object.prototype.hasOwnProperty.call(payload, 'twofa_secret') ? encrypt(item.twofa_secret) : existing[0].twofa_secret;
    await sqlite(`
      PRAGMA busy_timeout=5000; BEGIN IMMEDIATE;
      UPDATE channels SET gmail=${literal(item.gmail)},password=${literal(password)},recovery_email=${literal(item.recovery_email)},
        phone=${literal(item.phone)},twofa_secret=${literal(twofa)},channel_url=${literal(item.channel_url)},
        channel_name=${literal(item.channel_name)},year=${integer(item.year)},source=${literal(item.source)},status=${literal(item.status)},
        proxy_id=${integer(item.proxy_id)},notes=${literal(item.notes)},updated_at=datetime('now') WHERE id=${channelId};
      ${labelSql(channelId, item.label_ids)}
      COMMIT;
    `, false);
  } else {
    const inserted = await sqlite(`
      PRAGMA busy_timeout=5000;
      INSERT INTO channels(gmail,password,recovery_email,phone,twofa_secret,channel_url,channel_name,year,source,status,proxy_id,notes)
      VALUES(${literal(item.gmail)},${literal(encrypt(item.password))},${literal(item.recovery_email)},${literal(item.phone)},
        ${literal(encrypt(item.twofa_secret))},${literal(item.channel_url)},${literal(item.channel_name)},${integer(item.year)},
        ${literal(item.source)},${literal(item.status)},${integer(item.proxy_id)},${literal(item.notes)}) RETURNING id;
    `);
    channelId = inserted[0]?.id;
    if (!channelId) throw new Error('No se pudo guardar el canal.');
    await sqlite(`PRAGMA busy_timeout=5000; BEGIN IMMEDIATE; ${labelSql(channelId, item.label_ids)} COMMIT;`, false);
  }
  return { ok: true, id: channelId };
}

async function deleteChannel(rawId) {
  const id = idValue(rawId);
  const deleted = await sqlite(`
    PRAGMA busy_timeout=5000; BEGIN IMMEDIATE;
    DELETE FROM channel_labels WHERE channel_id=${id};
    DELETE FROM channels WHERE id=${id} RETURNING id;
    COMMIT;
  `);
  if (!deleted[0]) throw new Error('Canal no encontrado.');
  return { ok: true, id };
}

function status() {
  return {
    connected: fs.existsSync(DATABASE) && fs.existsSync(ENV_FILE),
    database: DATABASE
  };
}

module.exports = { listChannels, channelSecrets, saveChannel, deleteChannel, status };
