(function () {
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

  function renderPreview(box, data) {
    if (data.can_buy) {
      box.innerHTML =
        '<div class="b2b-price-preview-label">' +
        (data.label || 'Цена') + ': ' + formatPrice(data.unit_price) +
        ' ₸ / шт.</div>' +
        '<div class="b2b-price-preview-total">' +
        data.quantity + ' шт. = ' + formatPrice(data.total_price) +
        ' ₸</div>';
      return;
    }
    box.innerHTML =
      '<div class="b2b-price-preview-reason">' +
      (data.reason || 'Нельзя купить это количество.') +
      '</div>';
  }

  function bindPricePreview() {
    const box = document.querySelector('[data-b2b-price-preview]');
    if (!box) {
      return;
    }

    const previewUrl = box.getAttribute('data-preview-url');
    const productId = box.getAttribute('data-product-id');
    const qtyInput = document.querySelector('.product-detail-buy .qty-input');
    if (!previewUrl || !productId || !qtyInput) {
      return;
    }

    let requestSeq = 0;

    function refresh() {
      const quantity = Math.max(1, parseInt(qtyInput.value, 10) || 1);
      const seq = ++requestSeq;
      const url =
        previewUrl +
        '?product_id=' + encodeURIComponent(productId) +
        '&quantity=' + encodeURIComponent(quantity);

      fetch(url, {
        credentials: 'same-origin',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
        },
      })
        .then(parseJsonResponse)
        .then(function (data) {
          if (seq !== requestSeq) {
            return;
          }
          renderPreview(box, data);
        })
        .catch(function () {
          /* ignore preview errors; cart add re-checks on the server */
        });
    }

    qtyInput.addEventListener('qtychange', refresh);
  }

  function bindConsignmentForm() {
    const form = document.querySelector('[data-consignment-form]');
    if (!form) {
      return;
    }

    const messageEl = form.querySelector('[data-consignment-message]');
    const submitBtn = form.querySelector('.b2b-consignment-submit');

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      const action = form.getAttribute('data-action');
      const productId = form.getAttribute('data-product-id');
      const qtyInput = form.querySelector('input[name="quantity"]');
      const quantity = parseInt(qtyInput ? qtyInput.value : '0', 10);
      const csrfToken = getCsrfToken();

      if (!csrfToken) {
        window.alert('Обновите страницу и попробуйте снова.');
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
      }

      fetch(action, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          product_id: productId,
          quantity: quantity,
        }),
      })
        .then(parseJsonResponse)
        .then(function (data) {
          if (!data.ok && !data.success) {
            throw new Error(data.error || data.message || 'Не удалось отправить заявку');
          }
          if (messageEl) {
            messageEl.hidden = false;
            messageEl.classList.remove('is-error');
            messageEl.classList.add('is-success');
            messageEl.textContent = data.message || 'Заявка на реализацию принята.';
          }
        })
        .catch(function (error) {
          if (messageEl) {
            messageEl.hidden = false;
            messageEl.classList.remove('is-success');
            messageEl.classList.add('is-error');
            messageEl.textContent = error.message || 'Не удалось отправить заявку';
          } else {
            window.alert(error.message || 'Не удалось отправить заявку');
          }
        })
        .finally(function () {
          if (submitBtn) {
            submitBtn.disabled = false;
          }
        });
    });
  }

  bindPricePreview();
  bindConsignmentForm();
})();
