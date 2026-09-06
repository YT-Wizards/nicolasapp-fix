const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const SQLITE = '/usr/bin/sqlite3';

function createSyntheticDatabase(database) {
  execFileSync(SQLITE, [database, `
    CREATE TABLE proxies (
      id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL DEFAULT '',
      type TEXT NOT NULL DEFAULT 'HTTP', host TEXT NOT NULL DEFAULT '', port INTEGER,
      username TEXT, password TEXT, location TEXT, provider TEXT,
      status TEXT NOT NULL DEFAULT 'active', notes TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE channels (
      id INTEGER PRIMARY KEY AUTOINCREMENT, gmail TEXT, password TEXT,
      recovery_email TEXT, phone TEXT, twofa_secret TEXT, channel_url TEXT,
      channel_name TEXT, year INTEGER, source TEXT, status TEXT NOT NULL DEFAULT 'aged',
      proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL, notes TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE labels (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
      color TEXT NOT NULL DEFAULT '#8a7355',
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE channel_labels (
      channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
      label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
      PRIMARY KEY (channel_id, label_id)
    );
    INSERT INTO labels(id,name,color) VALUES(1,'Principal','#c09040');
    INSERT INTO channels(id,gmail,channel_name,status)
      VALUES(1,'existing@example.test','Canal existente','aged');
  `]);
}

async function run() {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'vyt-channels-'));
  const previousRoot = process.env.VYT_CHANNELS_ROOT;
  const previousDatabase = process.env.VYT_CHANNELS_DATABASE;
  try {
    const database = path.join(fixture, 'data', 'canales.db');
    fs.mkdirSync(path.dirname(database), { recursive: true });
    fs.writeFileSync(path.join(fixture, '.env'), `ENCRYPTION_KEY=${'ab'.repeat(32)}\n`);
    createSyntheticDatabase(database);
    process.env.VYT_CHANNELS_ROOT = fixture;
    process.env.VYT_CHANNELS_DATABASE = database;
    const channels = require('../electron/channels');

    const before = await channels.listChannels();
    assert.strictEqual(before.channels.length, 1, 'synthetic channel loads');
    assert.strictEqual((await channels.channelSecrets(before.channels[0].id)).gmail, 'existing@example.test');

    const created = await channels.saveChannel({
      gmail: 'vyt-fixture@example.test', password: 'fixture-password', twofa_secret: 'FIXTURE2FA',
      channel_name: 'VYT fixture', status: 'active', label_ids: [1]
    });
    const savedSecrets = await channels.channelSecrets(created.id);
    assert.strictEqual(savedSecrets.password, 'fixture-password');
    assert.strictEqual(savedSecrets.twofa_secret, 'FIXTURE2FA');

    await channels.saveChannel({
      id: created.id, gmail: 'vyt-fixture@example.test', password: 'changed-password', twofa_secret: 'FIXTURE2FA',
      channel_name: 'VYT fixture edited', status: 'monetized', label_ids: []
    });
    const after = await channels.listChannels();
    const edited = after.channels.find((item) => item.id === created.id);
    assert.strictEqual(edited.channel_name, 'VYT fixture edited');
    assert.strictEqual(edited.status, 'monetized');
    assert.strictEqual((await channels.channelSecrets(created.id)).password, 'changed-password');
    console.log('Canales integration: OK');
  } finally {
    if (previousRoot === undefined) delete process.env.VYT_CHANNELS_ROOT;
    else process.env.VYT_CHANNELS_ROOT = previousRoot;
    if (previousDatabase === undefined) delete process.env.VYT_CHANNELS_DATABASE;
    else process.env.VYT_CHANNELS_DATABASE = previousDatabase;
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}

run().catch((error) => { console.error(error); process.exit(1); });
