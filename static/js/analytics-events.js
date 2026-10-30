(function () {
  'use strict';

  function pushEvent(payload) {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(payload);
  }

  let lastSearchSignature = null;

  function trackCatalogSearch() {
    if (window.location.pathname !== '/' && window.location.pathname !== '/market/') return;

    const params = new URLSearchParams(window.location.search || '');
    const searchKeys = ['q', 'country', 'brand', 'model', 'category', 'city', 'offer', 'all', 'year', 'engine', 'subgroup', 'maker', 'stock'];
    const hasSearch = searchKeys.some(function (key) {
      return params.has(key) && String(params.get(key) || '').trim() !== '';
    });
    if (!hasSearch) return;
    // The URL is used only for in-memory deduplication, never sent to analytics.
    const signature = window.location.pathname + window.location.search;
    if (signature === lastSearchSignature) return;
    lastSearchSignature = signature;

    pushEvent({
      event: 'zpt_catalog_search',
      search_mode: params.has('q') && String(params.get('q') || '').trim() ? 'text' : 'filters',
    });
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
    document.querySelectorAll('[data-zpt-entry]').forEach(function (link) {
      link.addEventListener('click', function () {
        pushEvent({event: 'zpt_funnel_entry', audience: link.dataset.zptEntry});
      });
    });
    document.querySelectorAll('[data-zpt-funnel], #requestForm, #serviceForm, #home-parts-form').forEach(function (form) {
      form.addEventListener('input', function () {
        pushEvent({event: form.dataset.zptFunnel === 'seller' ? 'zpt_seller_registration_started' : 'zpt_request_started'});
      }, {once: true});
    });
    if (document.querySelector('[data-zpt-seller-registered]')) {
      pushEvent({event: 'zpt_seller_registered'});
    }
    document.addEventListener('zpt:products-added', trackCatalogSearch);
    document.addEventListener('zpt:cart-added', function (event) {
      const detail = event.detail || {};
      const productId = Number(detail.productId);
      const quantity = Number(detail.quantity);
      if (!Number.isSafeInteger(productId) || productId <= 0 || !Number.isSafeInteger(quantity) || quantity <= 0) return;
      pushEvent({event: 'zpt_add_to_cart', product_id: productId, quantity: quantity});
    });
    const product = document.querySelector('[data-zpt-product-view]');
    if (product) {
      const productId = Number(product.dataset.zptProductView);
      if (Number.isSafeInteger(productId) && productId > 0) pushEvent({event: 'zpt_product_view', product_id: productId});
    }
    if (document.getElementById('checkoutForm')) pushEvent({event: 'zpt_checkout_started'});
    document.addEventListener('click', function (event) {
      const link = event.target.closest('a.product-buy-btn');
      if (!link) return;
      let url;
      try { url = new URL(link.href, window.location.origin); } catch (_) { return; }
      if (url.hostname === 'kaspi.kz' || url.hostname.endsWith('.kaspi.kz')) pushEvent({event:'zpt_kaspi_buy_click'});
    });
    trackCatalogSearch();
    trackBuyerRequestSuccess();
    trackOrderCreated();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
