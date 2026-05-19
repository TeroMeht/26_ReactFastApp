// Service worker. Holds the "current chart tab" id between row clicks and
// routes incoming OPEN_TRADINGVIEW requests to it.
//
// Browser extension tab APIs (chrome.tabs / chrome.windows) are NOT subject
// to Cross-Origin-Opener-Policy, so we can address and navigate the
// TradingView tab even after it has been loaded -- which is what the
// pure web-page approach cannot do.

const TRADINGVIEW_HOST = 'www.tradingview.com';

// Remembered chart tab. Persist to chrome.storage.session so the worker
// being put to sleep doesn't lose it.
let chartTabId = null;
let chartWindowId = null;

async function loadState() {
  const stored = await chrome.storage.session.get(['chartTabId', 'chartWindowId']);
  chartTabId = stored.chartTabId ?? null;
  chartWindowId = stored.chartWindowId ?? null;
}
async function saveState() {
  await chrome.storage.session.set({ chartTabId, chartWindowId });
}

chrome.runtime.onInstalled.addListener(() => { loadState(); });
chrome.runtime.onStartup.addListener(() => { loadState(); });
loadState();

chrome.tabs.onRemoved.addListener(async (tabId) => {
  if (tabId === chartTabId) {
    chartTabId = null;
    chartWindowId = null;
    await saveState();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.type !== 'string') return false;

  if (msg.type === 'PING') {
    sendResponse({ ok: true, version: chrome.runtime.getManifest().version });
    return false;
  }

  if (msg.type === 'OPEN_TRADINGVIEW' && typeof msg.url === 'string') {
    handleOpen(msg.url)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => {
        console.error('OPEN_TRADINGVIEW failed', err);
        sendResponse({ ok: false, error: String(err) });
      });
    return true; // keep channel open for async sendResponse
  }

  return false;
});

async function handleOpen(url) {
  await loadState();

  // 1. Try the remembered chart tab.
  if (chartTabId !== null) {
    try {
      const tab = await chrome.tabs.get(chartTabId);
      if (tab) {
        await chrome.tabs.update(chartTabId, { url, active: true });
        if (chartWindowId !== null) {
          try {
            await chrome.windows.update(chartWindowId, { focused: true });
          } catch { /* window may be minimized; ignore */ }
        }
        return;
      }
    } catch {
      chartTabId = null;
      chartWindowId = null;
    }
  }

  // 2. Fall back to scanning all tabs for an existing TradingView chart.
  // This lets us latch onto a tab the user opened earlier (even before
  // installing the extension) instead of opening a new one.
  const candidates = await chrome.tabs.query({
    url: `*://${TRADINGVIEW_HOST}/chart/*`,
  });
  if (candidates.length > 0) {
    const tab = candidates[0];
    chartTabId = tab.id;
    chartWindowId = tab.windowId;
    await saveState();
    await chrome.tabs.update(tab.id, { url, active: true });
    try {
      await chrome.windows.update(tab.windowId, { focused: true });
    } catch { /* ignore */ }
    return;
  }

  // 3. Nothing usable -- open a new tab and remember it.
  const tab = await chrome.tabs.create({ url, active: true });
  chartTabId = tab.id;
  chartWindowId = tab.windowId;
  await saveState();
}
