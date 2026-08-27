(function () {
  const config = window.ZPT_WHOLESALE_CART || {};
  const addUrl = config.addUrl || '/cart/add/';

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) {
      return meta.content;
    }
    return getCookie('csrftoken');
  }

  document.querySelectorAll('[data-wholesale-add]').forEach(function (button) {
    button.addEventListener('click', function () {
      const productId = parseInt(button.getAttribute('data-product-id') || '', 10);
      const article = (button.getAttribute('data-product-article') || '').trim();
      const csrfToken = getCsrfToken();
      if (!csrfToken || (!Number.isFinite(productId) && !article)) {
        window.alert('Не удалось добавить товар. Обновите страницу.');
        return;
      }
      button.disabled = true;
      fetch(addUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          product_id: Number.isFinite(productId) && productId > 0 ? productId : null,
          article: article || null,
          quantity: 1,
          mode: 'wholesale',
        }),
      })
        .then(function (response) {
          return response.json().then(function (payload) {
            payload._status = response.status;
            return payload;
          });
        })
        .then(function (payload) {
          if (payload.ok || payload.success) {
            window.location.href = '/cart/';
            return;
          }
          window.alert(payload.message || payload.error || 'Не удалось добавить товар.');
          button.disabled = false;
        })
        .catch(function () {
          window.alert('Не удалось добавить товар. Попробуйте ещё раз.');
          button.disabled = false;
        });
    });
  });
})();
