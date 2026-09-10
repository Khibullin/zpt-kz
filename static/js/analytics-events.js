(function () {
  'use strict';

  function pushEvent(payload) {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(payload);
  }

  function trackBuyerRequestSuccess() {
    if (window.location.pathname !== '/request-parts/') return;
    const msg = document.getElementById('msg');
    if (!msg) return;

    const fireIfAccepted = function () {
      if (!msg.classList.contains('success')) return;
      const text = (msg.textContent || '').trim();
      if (!/Заявка №\d+ принята\./.test(text)) return;
      if (msg.dataset.analyticsTracked === '1') return;
      msg.dataset.analyticsTracked = '1';
      pushEvent({ event: 'zpt_request_submitted' });
    };

    fireIfAccepted();
    new MutationObserver(fireIfAccepted).observe(msg, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['class'],
    });
  }

  function trackOrderCreated() {
    const match = window.location.pathname.match(
      /^\/orders\/(\d+)\/[0-9a-f-]+\/success\/$/i
    );
    if (!match) return;

    const orderId = match[1];
    const storageKey = 'zpt_order_created:' + orderId;
    try {
      if (window.localStorage.getItem(storageKey) === '1') return;
      window.localStorage.setItem(storageKey, '1');
    } catch (err) {
      // Tracking must never affect the success page if storage is unavailable.
    }

    pushEvent({
      event: 'zpt_order_created',
      order_id: orderId,
    });
  }

  function init() {
    trackBuyerRequestSuccess();
    trackOrderCreated();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
