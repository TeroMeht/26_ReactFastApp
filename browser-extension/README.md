# Mehtanen Live Scanner — Chart Router (Browser Extension)

A tiny Chromium-only Manifest V3 extension that solves the
"every row click opens a new TradingView tab" problem.

Browser security (`Cross-Origin-Opener-Policy`) prevents the scanner web
app from re-targeting a TradingView tab once it has loaded. Extensions
are exempt from COOP for their privileged tab APIs, so the extension
takes over routing: it finds the existing TradingView chart tab (or
opens one if none exists) and navigates it to the new symbol.

## How it works

1. **Live Scanner page** detects the extension via a `HELLO` postMessage on load.
2. When the user clicks a row, the page sends `postMessage({ source: 'mehtanen-scanner', type: 'OPEN_TRADINGVIEW', url })`.
3. **content.js** forwards the message to **background.js** (service worker).
4. The background worker:
   * tries the remembered chart tab id,
   * falls back to any open `https://www.tradingview.com/chart/*` tab,
   * if nothing exists, opens a new tab and remembers it.
5. If the extension is NOT installed, the scanner falls back to its
   existing anchor-click / window.open behavior.

## Install (developer / unpacked)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked**.
4. Select this `browser-extension/` folder.
5. Refresh the Live Scanner page. You should see in DevTools console a
   `HELLO` message from `mehtanen-scanner-ext` arriving on load.

## Files

* `manifest.json` — Manifest V3 declaration.
* `background.js` — service worker holding the chart-tab id.
* `content.js` — page<->extension bridge.

## Permissions

* `tabs` — to query and update tabs.
* host permissions for `localhost:3000`, `127.0.0.1:3000` (scanner page)
  and `https://www.tradingview.com/*` (so we can `tabs.update()` it).
