(function () {
  const config = window.ZPT_CART_PAGE || {};
  const updateUrl = config.updateUrl || '/cart/update_quantity/';

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

  function formatPrice(value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function updateCartBadge(count) {
    document.querySelectorAll('[data-cart-count]').forEach(function (el) {
      el.textContent = String(count);
      el.hidden = count <= 0;
    });
  }

  function readDataAttr(element, attrName) {
    if (!element) {
      return '';
    }

    const value = element.getAttribute(attrName);
    if (value) {
      return value;
    }

    const lineItem = element.closest('.cart-line-item');
    if (lineItem && lineItem.getAttribute(attrName)) {
      return lineItem.getAttribute(attrName);
    }

    const controls = element.closest('.cart-qty-controls');
    if (controls && controls.getAttribute(attrName)) {
      return controls.getAttribute(attrName);
    }

    return '';
  }

  function readProductIdFromButton(button) {
    return readDataAttr(button, 'data-product-id');
  }

  function findLineItem(button) {
    return button.closest('.cart-line-item');
  }

  function findQtyControls(button) {
    return button.closest('.cart-qty-controls');
  }

  function readQuantity(controls) {
    const input = controls.querySelector('.cart-qty-input');
    return Math.max(1, parseInt(input ? input.value : '1', 10) || 1);
  }

  function setQuantityInput(controls, quantity) {
    const input = controls.querySelector('.cart-qty-input');
    if (input) {
      input.value = String(quantity);
    }
  }

  function setMinusDisabled(controls, quantity) {
    const minus = controls.querySelector('.cart-minus');
    if (minus) {
      minus.disabled = quantity <= 1;
    }
  }

  function updateLineDisplay(lineItem, data) {
    const qtyPrice = lineItem.querySelector('.cart-line-qty-price');
    const lineTotal = lineItem.querySelector('.cart-line-total');

    if (qtyPrice) {
      qtyPrice.textContent =
        data.quantity + ' × ' +
        (data.unit_price_display || formatPrice(data.unit_price || 0)) + ' ₸';
    }

    if (lineTotal) {
      lineTotal.textContent =
        '= ' + (data.item_total_price_display || formatPrice(data.item_total_price)) + ' ₸';
    }
  }

  function updateCartTotal(data) {
    const totalEl = document.getElementById('cart-total-price');
    if (totalEl) {
      totalEl.textContent =
        (data.cart_total_price_display || formatPrice(data.cart_total_price)) + ' ₸';
    }
  }

  function sendQuantityUpdate(payload, controls, lineItem) {
    const csrfToken = getCsrfToken();
    if (!csrfToken) {
      window.alert('Не удалось получить CSRF-токен. Обновите страницу и попробуйте снова.');
      return Promise.reject(new Error('CSRF missing'));
    }

    const buttons = controls.querySelectorAll('.cart-minus, .cart-plus');
    buttons.forEach(function (btn) {
      btn.disabled = true;
    });

    return fetch(updateUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify(payload),
    })
      .then(parseJsonResponse)
      .then(function (data) {
        if (!data.success && !data.ok) {
          throw new Error(data.error || data.message || 'Не удалось обновить количество');
        }

        if (data.product_id && lineItem) {
          lineItem.setAttribute('data-product-id', String(data.product_id));
        }

        setQuantityInput(controls, data.quantity);
        setMinusDisabled(controls, data.quantity);
        updateLineDisplay(lineItem, data);
        updateCartTotal(data);
        updateCartBadge(data.total_items != null ? data.total_items : data.cart_count);
        return data;
      })
      .finally(function () {
        const qty = readQuantity(controls);
        setMinusDisabled(controls, qty);
        const plus = controls.querySelector('.cart-plus');
        if (plus) {
          plus.disabled = false;
        }
      });
  }

  function handleQtyClick(event) {
    const button = event.currentTarget;
    const controls = findQtyControls(button);
    const lineItem = findLineItem(button);

    if (!controls || !lineItem) {
      return;
    }

    const idRaw = button.getAttribute('data-product-id') || readProductIdFromButton(button);
    const productId = parseInt(String(idRaw || '').trim(), 10);
    const article = readDataAttr(button, 'data-product-article').trim();
    const supplier = readDataAttr(button, 'data-product-supplier').trim();
    const currentQty = readQuantity(controls);
    let nextQty = currentQty;

    console.log('Корзина - меняем количество для ID:', productId, 'артикул:', article || '(нет)');

    if (button.classList.contains('cart-minus')) {
      if (currentQty <= 1) {
        return;
      }
      nextQty = currentQty - 1;
    } else if (button.classList.contains('cart-plus')) {
      nextQty = currentQty + 1;
    } else {
      return;
    }

    if ((!Number.isFinite(productId) || productId <= 0) && !article) {
      window.alert('Не удалось определить товар. Обновите страницу и попробуйте снова.');
      return;
    }

    sendQuantityUpdate(
      {
        product_id: Number.isFinite(productId) && productId > 0 ? productId : null,
        article: article || null,
        supplier: supplier || null,
        quantity: nextQty,
      },
      controls,
      lineItem
    ).catch(function (error) {
      window.alert(error.message || 'Ошибка обновления корзины');
    });
  }

  document.querySelectorAll('.cart-minus, .cart-plus').forEach(function (button) {
    button.addEventListener('click', handleQtyClick);
  });
})();
