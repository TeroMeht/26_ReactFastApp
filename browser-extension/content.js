// Content script — bridges the scanner page (window) and the background
// service worker (chrome.runtime). The page communicates via postMessage,
// which we forward to the extension.

(function () {
  // Forward outbound requests page -> background.
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== 'mehtanen-scanner') return;

    if (data.type === 'OPEN_TRADINGVIEW' && typeof data.url === 'string') {
      chrome.runtime.sendMessage(
        { type: 'OPEN_TRADINGVIEW', url: data.url },
        (resp) => {
          window.postMessage(
            {
              source: 'mehtanen-scanner-ext',
              type: 'OPEN_TRADINGVIEW_RESULT',
              ok: !!(resp && resp.ok),
              error: resp && resp.error,
            },
            window.location.origin,
          );
        },
      );
    } else if (data.type === 'PING') {
      chrome.runtime.sendMessage({ type: 'PING' }, (resp) => {
        window.postMessage(
          {
            source: 'mehtanen-scanner-ext',
            type: 'PING_RESULT',
            ok: !!(resp && resp.ok),
            version: resp ? resp.version : null,
          },
          window.location.origin,
        );
      });
    }
  });

  // Announce presence on load so the page can switch its row-click
  // handler from window.open() to postMessage routing.
  window.postMessage(
    {
      source: 'mehtanen-scanner-ext',
      type: 'HELLO',
      version: chrome.runtime.getManifest().version,
    },
    window.location.origin,
  );
})();
