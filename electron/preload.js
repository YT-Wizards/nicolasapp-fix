const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vyt', {
  chooseVideo: () => ipcRenderer.invoke('choose-video'),
  chooseAudio: () => ipcRenderer.invoke('choose-audio'),
  chooseScript: () => ipcRenderer.invoke('choose-script'),
  chooseClipsFolder: () => ipcRenderer.invoke('choose-clips-folder'),
  chooseProductQr: () => ipcRenderer.invoke('choose-product-qr'),
  inspectVideo: (filePath) => ipcRenderer.invoke('inspect-video', filePath),
  createJob: (payload) => ipcRenderer.invoke('create-job', payload),
  createExternalPlan: (payload) => ipcRenderer.invoke('external-create-plan', payload),
  validateExternalClips: (planPath, clipsFolder) => ipcRenderer.invoke('external-validate-clips', planPath, clipsFolder),
  renderExternal: (payload) => ipcRenderer.invoke('external-render', payload),
  resumeJob: (historyId) => ipcRenderer.invoke('resume-job', historyId),
  cancelJob: (jobId) => ipcRenderer.invoke('cancel-job', jobId),
  getState: () => ipcRenderer.invoke('get-state'),
  getSettingsStatus: () => ipcRenderer.invoke('settings-status'),
  saveSettings: (settings) => ipcRenderer.invoke('settings-save', settings),
  testSettings: () => ipcRenderer.invoke('settings-test'),
  revealOutput: (filePath) => ipcRenderer.invoke('reveal-output', filePath),
  openOutput: (filePath) => ipcRenderer.invoke('open-output', filePath),
  channelsStatus: () => ipcRenderer.invoke('channels-status'),
  channelsList: () => ipcRenderer.invoke('channels-list'),
  channelSecrets: (channelId) => ipcRenderer.invoke('channels-secrets', channelId),
  saveChannel: (payload) => ipcRenderer.invoke('channels-save', payload),
  deleteChannel: (channelId) => ipcRenderer.invoke('channels-delete', channelId),
  copyChannelAccess: (channelId) => ipcRenderer.invoke('channels-copy-access', channelId),
  onState: (callback) => {
    const handler = (_event, state) => callback(state);
    ipcRenderer.on('state', handler);
    return () => ipcRenderer.removeListener('state', handler);
  }
});
