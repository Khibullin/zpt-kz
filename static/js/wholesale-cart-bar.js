(function () {
  const bar = document.querySelector('[data-wholesale-cart-bar]');
  if (!bar) {
    return;
  }

  const desktopEl = bar.querySelector('[data-wholesale-cart-desktop]');
  const noteEl = bar.querySelector('[data-wholesale-cart-note]');
  const mobileEl = bar.querySelector('[data-wholesale-cart-mobile]');
  const ctaDesktopEl = bar.querySelector('[data-wholesale-cart-cta-desktop]');
  const ctaMobileEl = bar.querySelector('[data-wholesale-cart-cta-mobile]');
  const minQty = Math.max(
    1,
    parseInt(bar.getAttribute('data-wholesale-min'), 10) || 10
  );

  function formatKzt(value) {
    const amount = Math.round(Number(value) || 0);
    return String(amount).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₸';
  }

  function setNote(text) {
    if (!noteEl) {
      return;
    }
    if (text) {
      noteEl.textContent = text;
      noteEl.hidden = false;
    } else {
      noteEl.textContent = '';
      noteEl.hidden = true;
    }
  }

  function renderEmpty() {
    if (desktopEl) {
      desktopEl.textContent = '🛒 Корзина пуста · минимум ' + minQty + ' шт.';
    }
    setNote('');
    if (mobileEl) {
      mobileEl.textContent = '🛒 Корзина пуста';
    }
    if (ctaDesktopEl) {
      ctaDesktopEl.textContent = 'Корзина';
    }
    if (ctaMobileEl) {
      ctaMobileEl.textContent = 'Корзина';
    }
  }

  function renderRetail(count) {
    if (desktopEl) {
      desktopEl.textContent = '🛒 В корзине розничный заказ · ' + count + ' шт.';
    }
    setNote('');
    if (mobileEl) {
      mobileEl.textContent = '🛒 В корзине розничный заказ · ' + count + ' шт.';
    }
    if (ctaDesktopEl) {
      ctaDesktopEl.textContent = 'Перейти в корзину';
    }
    if (ctaMobileEl) {
      ctaMobileEl.textContent = 'Корзина';
    }
  }

  function renderWholesale(data) {
    const status = data.wholesale_status || {};
    const qty = Number(status.total_qty != null ? status.total_qty : data.cart_count) || 0;
    const remaining = Number(status.remaining) || 0;
    const canCheckout = Boolean(status.can_checkout);
    const totalLabel = formatKzt(data.cart_total);

    if (desktopEl) {
      desktopEl.textContent = '🛒 Корзина: ' + qty + ' шт. · ' + totalLabel;
    }
    if (mobileEl) {
      if (remaining > 0) {
        mobileEl.textContent = '🛒 ' + qty + ' шт. · ещё ' + remaining + ' до минимума';
      } else {
        mobileEl.textContent = '🛒 ' + qty + ' шт. · минимум набран';
      }
    }

    if (remaining > 0) {
      setNote('Ещё ' + remaining + ' шт. до минимального заказа');
      if (ctaDesktopEl) {
        ctaDesktopEl.textContent = 'Перейти в корзину';
      }
      if (ctaMobileEl) {
        ctaMobileEl.textContent = 'Корзина';
      }
      return;
    }

    setNote('✓ Минимальный заказ набран');
    if (ctaDesktopEl) {
      ctaDesktopEl.textContent = 'Оформить заказ';
    }
    if (ctaMobileEl) {
      ctaMobileEl.textContent = 'Оформить';
    }
    bar.classList.toggle('is-ready', canCheckout);
  }

  function render(data) {
    if (!data || typeof data.wholesale_status !== 'object' || data.wholesale_status === null) {
      return;
    }

    bar.classList.remove('is-ready');
    const count = Number(data.cart_count) || 0;
    const mode = data.cart_mode;

    if (mode === 'retail' && count > 0) {
      renderRetail(count);
      return;
    }

    if (count <= 0 || !data.is_wholesale) {
      renderEmpty();
      return;
    }

    renderWholesale(data);
  }

  document.addEventListener('zpt:cart-state', function (event) {
    render(event.detail);
  });
})();
