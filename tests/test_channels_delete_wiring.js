const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');

const preload = read('electron/preload.js');
const main = read('electron/main.js');
const renderer = read('ui/renderer.js');

assert.match(
  preload,
  /deleteChannel\s*:\s*\(\s*channelId\s*\)\s*=>\s*ipcRenderer\.invoke\(\s*["']channels-delete["']\s*,\s*channelId\s*\)/,
  'el puente seguro debe exponer deleteChannel mediante channels-delete'
);
assert.match(
  main,
  /ipcMain\.handle\(\s*["']channels-delete["']\s*,\s*\(\s*_event\s*,\s*channelId\s*\)\s*=>\s*channels\.deleteChannel\(\s*channelId\s*\)\s*\)/,
  'el proceso principal debe delegar channels-delete en el módulo aislado de canales'
);

assert.match(
  renderer,
  /<button\b[^>]*data-channel-delete=["']\$\{channel\.id\}["'][^>]*>\s*\$\{t\('delete'\)\}\s*<\/button>/,
  'cada fila debe ofrecer una acción Eliminar asociada al id de ese canal'
);
assert.match(
  renderer,
  /\$\$\(\s*["']\[data-channel-delete\]["']\s*\)[\s\S]{0,240}(?:onclick\s*=|addEventListener\(\s*["']click["'])/,
  'la interfaz debe escuchar el clic de todos los botones Eliminar renderizados'
);
assert.match(
  renderer,
  /(?:window\.)?confirm\([\s\S]{1,240}?\)/,
  'el borrado debe pedir confirmación explícita'
);
assert.match(
  renderer,
  /if\s*\(\s*!\s*(?:confirmed|confirmation|shouldDelete)\s*\)\s*(?:\{\s*)?return\b|if\s*\(\s*!\s*(?:window\.)?confirm\([\s\S]{1,240}?\)\s*\)\s*(?:\{\s*)?return\b/,
  'cancelar la confirmación debe detener el borrado'
);
assert.match(
  renderer,
  /Number\(\s*button\.dataset\.channelDelete\s*\)/,
  'el id de la fila debe normalizarse antes de pasarlo al flujo de borrado'
);
assert.match(
  renderer,
  /await\s+window\.vyt\.deleteChannel\(\s*(?:Number\(\s*button\.dataset\.channelDelete\s*\)|channelId)\s*\)/,
  'tras confirmar se debe enviar al backend el id seleccionado'
);
assert.match(
  renderer,
  /await\s+loadChannels\(\s*\)/,
  'después del borrado la lista debe recargarse desde la base'
);
assert.match(
  renderer,
  /showToast\(\s*t\('deleted'\)\s*\)/,
  'el usuario debe recibir confirmación visual del borrado'
);
assert.match(
  renderer,
  /showError\(\s*\$\(\s*["']#channelsError["']\s*\)\s*,\s*error\.message\s*\)/,
  'un fallo del borrado debe mostrarse sin retirar silenciosamente la fila'
);

console.log('Canales delete UI wiring: OK');
