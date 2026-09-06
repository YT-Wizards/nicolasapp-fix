const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

ipcMain.handle('get-state', () => ({ jobs: [], history: [] }));
ipcMain.handle('settings-status', () => ({ geminigen: true, algrow: true, gateway: true }));

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1440, height: 1100, show: false, backgroundColor: '#090b0e',
    webPreferences: {
      preload: path.join(__dirname, '..', 'electron', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true
    }
  });
  await win.loadFile(path.join(__dirname, '..', 'ui', 'index.html'));
  const state = await win.webContents.executeJavaScript(`
    document.querySelector('#productQrToggle').click();
    ({checked: document.querySelector('#productQrToggle').checked, hidden: document.querySelector('#productQrPanel').hidden});
  `);
  console.log(state);
  await new Promise((resolve) => setTimeout(resolve, 500));
  const image = await win.webContents.capturePage();
  fs.writeFileSync('/private/tmp/vyt-qr-ui-3.png', image.toPNG());
  app.quit();
});
