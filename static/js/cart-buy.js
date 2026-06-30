(function () {
  const config = window.ZPT_CART || {};
  const addUrl = config.addUrl || '/cart/add/';
  const countUrl = config.countUrl || '/cart/count/';

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

  function parseJsonResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      return response.text().then(function (body) {
        const snippet = (body || '').replace(/\s+/g, ' ').slice(0, 120);
        throw new Error(
          'Сервер вернул не JSON (код ' + response.status + '): ' + snippet
        );
      });
    }
    return response.json();
  }

  function updateCartBadge(count) {
    document.querySelectorAll('[data-cart-count]').forEach(function (el) {
      el.textContent = String(count);
      el.hidden = count <= 0;
    });
  }

  function setBuyButtonSuccess(button) {
    const textEl = button.querySelector('.product-buy-btn-text');
    const original = textEl ? textEl.textContent : button.textContent;
    button.disabled = true;
    button.classList.add('is-success');
    if (textEl) {
      textEl.textContent = 'В корзине! ✓';
    } else {
      button.textContent = 'В корзине! ✓';
    }
    window.setTimeout(function () {
      button.disabled = false;
      button.classList.remove('is-success');
      if (textEl) {
        textEl.textContent = original;
      } else {
        button.textContent = original;
      }
    }, 1800);
  }

  function bindQtyControls(root) {
    const input = root.querySelector('.qty-input');
    const minus = root.querySelector('[data-qty-minus]');
    const plus = root.querySelector('[data-qty-plus]');

    if (!input || !minus || !plus) {
      return;
    }

    function readQty() {
      return Math.max(1, parseInt(input.value, 10) || 1);
    }

    minus.addEventListener('click', function () {
      input.value = String(Math.max(1, readQty() - 1));
    });

    plus.addEventListener('click', function () {
      input.value = String(readQty() + 1);
    });
  }

  function bindBuyButton(root) {
    const productId = root.dataset.productId;
    const buyButton = root.querySelector('[data-cart-add]');
    const qtyInput = root.querySelector('.qty-input');

    if (!productId || !buyButton) {
      return;
    }

    buyButton.addEventListener('click', function () {
      const csrfToken = getCsrfToken();
      if (!csrfToken) {
        window.alert('Не удалось получить CSRF-токен. Обновите страницу и попробуйте снова.');
        return;
      }

      const quantity = Math.max(1, parseInt(qtyInput ? qtyInput.value : '1', 10) || 1);
      const formData = new FormData();
      formData.append('product_id', productId);
      formData.append('quantity', String(quantity));

      buyButton.disabled = true;

      fetch(addUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: formData,
      })
        .then(parseJsonResponse)
        .then(function (data) {
          if (!data.ok) {
            throw new Error(data.message || 'Не удалось добавить товар в корзину');
          }
          updateCartBadge(data.cart_count);
          setBuyButtonSuccess(buyButton);
        })
        .catch(function (error) {
          window.alert(error.message || 'Ошибка добавления в корзину');
        })
        .finally(function () {
          buyButton.disabled = false;
        });
    });
  }

  document.querySelectorAll('.product-buy-controls').forEach(function (root) {
    bindQtyControls(root);
    bindBuyButton(root);
  });

  fetch(countUrl, {
    credentials: 'same-origin',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json',
    },
  })
    .then(parseJsonResponse)
    .then(function (data) {
      if (data && data.ok) {
        updateCartBadge(data.cart_count);
      }
    })
    .catch(function () {
      /* ignore */
    });
})();
