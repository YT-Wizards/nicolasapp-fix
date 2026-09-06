const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const channels = require('../electron/channels');

ipcMain.handle('get-state', () => ({ jobs: [], history: [] }));
ipcMain.handle('settings-status', () => ({ geminigen: true, algrow: true, gateway: true }));
ipcMain.handle('channels-list', () => channels.listChannels());

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1440, height: 900, show: false, backgroundColor: '#090b0e',
    webPreferences: {
      preload: path.join(__dirname, '..', 'electron', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true
    }
  });
  await win.loadFile(path.join(__dirname, '..', 'ui', 'index.html'));
  await win.webContents.executeJavaScript(`document.querySelector('[data-view="channels"]').click()`);
  await win.webContents.executeJavaScript(`new Promise((resolve) => {
    const started = Date.now();
    const waitForRows = () => {
      if (document.querySelector('[data-channel-edit]') || Date.now() - started > 5000) resolve();
      else setTimeout(waitForRows, 100);
    };
    waitForRows();
  })`);
  const image = await win.webContents.capturePage();
  fs.writeFileSync('/private/tmp/vyt-channels-ui.png', image.toPNG());
  app.quit();
});
