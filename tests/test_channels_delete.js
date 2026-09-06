const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const SQLITE = '/usr/bin/sqlite3';

function query(database, sql) {
  const output = execFileSync(SQLITE, ['-json', database, sql], { encoding: 'utf8' }).trim();
  return output ? JSON.parse(output) : [];
}

function createSyntheticDatabase(database) {
  execFileSync(SQLITE, [database, `
    PRAGMA foreign_keys=ON;
    CREATE TABLE proxies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'HTTP',
      host TEXT NOT NULL DEFAULT '', port INTEGER, username TEXT, password TEXT,
      location TEXT, provider TEXT, status TEXT NOT NULL DEFAULT 'active', notes TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE channels (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      gmail TEXT, password TEXT, recovery_email TEXT, phone TEXT, twofa_secret TEXT,
      channel_url TEXT, channel_name TEXT, year INTEGER, source TEXT,
      status TEXT NOT NULL DEFAULT 'aged',
      proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
      notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE labels (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE, color TEXT NOT NULL DEFAULT '#8a7355',
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE channel_labels (
      channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
      label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
      PRIMARY KEY (channel_id, label_id)
    );

    INSERT INTO proxies(id,label,type,host,port,status)
      VALUES(1,'Proxy de prueba','HTTP','127.0.0.1',8080,'active');
    INSERT INTO labels(id,name,color) VALUES
      (1,'Principal','#c09040'),
      (2,'Secundaria','#4060c0');
    INSERT INTO channels(id,gmail,channel_name,status,proxy_id,notes) VALUES
      (1,'delete-me@example.test','Canal a borrar','active',1,'fixture temporal'),
      (2,'keep-me@example.test','Canal que permanece','monetized',1,'fixture temporal');
    INSERT INTO channel_labels(channel_id,label_id) VALUES(1,1),(1,2),(2,2);
  `]);
}

async function run() {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'vyt-channel-delete-'));
  const previousRoot = process.env.VYT_CHANNELS_ROOT;
  const previousDatabase = process.env.VYT_CHANNELS_DATABASE;
  try {
    const database = path.join(fixture, 'data', 'canales.db');
    fs.mkdirSync(path.dirname(database), { recursive: true });
    // A syntactically valid throwaway key keeps the module fully isolated from
    // the real Canales directory even if its encryption helper is reached.
    fs.writeFileSync(path.join(fixture, '.env'), `ENCRYPTION_KEY=${'ab'.repeat(32)}\n`);
    createSyntheticDatabase(database);
    process.env.VYT_CHANNELS_ROOT = fixture;
    process.env.VYT_CHANNELS_DATABASE = database;

    const channels = require('../electron/channels');
    assert.strictEqual(
      channels.status().database,
      database,
      'la prueba debe apuntar exclusivamente a su base temporal'
    );
    assert.strictEqual(
      typeof channels.deleteChannel,
      'function',
      'electron/channels debe exportar deleteChannel(channelId)'
    );

    const result = await channels.deleteChannel(1);
    assert.deepStrictEqual(result, { ok: true, id: 1 });

    const remaining = await channels.listChannels();
    assert.deepStrictEqual(
      remaining.channels.map((channel) => channel.id),
      [2],
      'solo se elimina el canal solicitado'
    );
    assert.strictEqual(remaining.channels[0].channel_name, 'Canal que permanece');
    assert.strictEqual(remaining.proxies.length, 1, 'el proxy asociado no se elimina');
    assert.deepStrictEqual(
      remaining.labels.map((label) => label.id),
      [1, 2],
      'las etiquetas compartidas no se eliminan'
    );
    assert.deepStrictEqual(
      query(database, 'SELECT channel_id,label_id FROM channel_labels ORDER BY channel_id,label_id;'),
      [{ channel_id: 2, label_id: 2 }],
      'se limpian las relaciones del canal y no las de otros canales'
    );

    execFileSync(SQLITE, [database, `
      INSERT INTO channels(id,gmail,channel_name,status,notes)
        VALUES(3,'rollback@example.test','Canal de rollback','aged','fixture temporal');
      INSERT INTO channel_labels(channel_id,label_id) VALUES(3,1);
      CREATE TRIGGER force_delete_failure
        BEFORE DELETE ON channels WHEN OLD.id=3
        BEGIN SELECT RAISE(ABORT,'forced delete failure'); END;
    `]);
    await assert.rejects(
      () => channels.deleteChannel(3),
      /forced delete failure/,
      'un error de SQLite debe propagarse al usuario'
    );
    assert.deepStrictEqual(
      query(database, 'SELECT id FROM channels WHERE id=3;'),
      [{ id: 3 }],
      'el canal debe permanecer si falla el borrado'
    );
    assert.deepStrictEqual(
      query(database, 'SELECT channel_id,label_id FROM channel_labels WHERE channel_id=3;'),
      [{ channel_id: 3, label_id: 1 }],
      'la transacción debe restaurar las relaciones si falla el borrado del canal'
    );

    await assert.rejects(
      () => channels.deleteChannel(999),
      /Canal no encontrado\./,
      'un id válido pero inexistente debe informar de que el canal no existe'
    );

    for (const invalidId of [undefined, null, '', 0, -1, 1.5, '1 OR 1=1']) {
      await assert.rejects(
        () => channels.deleteChannel(invalidId),
        /Canal no válido\./,
        `el id ${String(invalidId)} no debe llegar a SQLite`
      );
    }
    assert.deepStrictEqual(
      query(database, 'SELECT id FROM channels ORDER BY id;'),
      [{ id: 2 }, { id: 3 }],
      'los intentos fallidos no modifican la base temporal'
    );
    console.log('Canales delete integration: OK');
  } finally {
    if (previousRoot === undefined) delete process.env.VYT_CHANNELS_ROOT;
    else process.env.VYT_CHANNELS_ROOT = previousRoot;
    if (previousDatabase === undefined) delete process.env.VYT_CHANNELS_DATABASE;
    else process.env.VYT_CHANNELS_DATABASE = previousDatabase;
    fs.rmSync(fixture, { recursive: true, force: true });
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
