(function () {
  const root = document.getElementById('sr-page-root');
  if (!root) return;

  const csrfInput = root.querySelector('input[name="csrfmiddlewaretoken"]');
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = (csrfInput && csrfInput.value) || (csrfMeta && csrfMeta.content) || '';
  const whatsappBtn = document.getElementById('sr-whatsapp');
  const callBtn = document.getElementById('sr-call');
  const contactError = document.getElementById('sr-contact-error');
  const declineActions = document.getElementById('sr-decline-actions');
  const declineNote = document.getElementById('sr-decline-note');
  const declineError = document.getElementById('sr-decline-error');

  function whatsappUrl() {
    return root.getAttribute('data-whatsapp-url') || '';
  }

  function telUrl() {
    return root.getAttribute('data-tel-url') || '';
  }

  function setError(node, message) {
    if (!node) return;
    if (message) {
      node.textContent = message;
      node.hidden = false;
    } else {
      node.textContent = '';
      node.hidden = true;
    }
  }

  function applyUrls(data) {
    if (data.whatsapp_url) root.setAttribute('data-whatsapp-url', data.whatsapp_url);
    if (data.tel_url) root.setAttribute('data-tel-url', data.tel_url);
  }

  function openWhatsApp(url) {
    const target = url || whatsappUrl();
    if (!target) {
      setError(contactError, 'Не удалось открыть WhatsApp.');
      return;
    }
    window.open(target, '_blank', 'noopener,noreferrer');
  }

  function openDialer(url) {
    const target = url || telUrl();
    if (!target) {
      setError(contactError, 'Не удалось начать звонок.');
      return;
    }
    window.location.href = target;
  }

  function postAction(action) {
    const body = new FormData();
    body.append('action', action);
    if (csrfToken) body.append('csrfmiddlewaretoken', csrfToken);
    return fetch(window.location.pathname, {
      method: 'POST',
      body: body,
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': csrfToken,
        'X-Requested-With': 'XMLHttpRequest',
      },
    }).then(function (response) {
      return response.json().then(function (data) {
        data._status = response.status;
        return data;
      }).catch(function () {
        return { ok: false, error: 'Не удалось обработать ответ.', _status: response.status };
      });
    });
  }

  function registerWhatsAppThenOpen() {
    setError(contactError, '');
    if (whatsappBtn) whatsappBtn.disabled = true;
    return postAction('whatsapp_click').then(function (data) {
      if (whatsappBtn) whatsappBtn.disabled = false;
      if (!data.ok) {
        setError(contactError, data.error || 'Не удалось сохранить действие. Попробуйте ещё раз.');
        return;
      }
      applyUrls(data);
      openWhatsApp(data.whatsapp_url);
    }).catch(function () {
      if (whatsappBtn) whatsappBtn.disabled = false;
      setError(contactError, 'Не удалось сохранить действие. Попробуйте ещё раз.');
    });
  }

  if (whatsappBtn) {
    whatsappBtn.addEventListener('click', function (event) {
      event.preventDefault();
      registerWhatsAppThenOpen();
    });
  }

  if (callBtn) {
    callBtn.addEventListener('click', function (event) {
      event.preventDefault();
      setError(contactError, '');
      callBtn.disabled = true;
      postAction('call_click').then(function (data) {
        callBtn.disabled = false;
        if (!data.ok) {
          setError(contactError, data.error || 'Не удалось сохранить действие. Попробуйте ещё раз.');
          return;
        }
        applyUrls(data);
        openDialer(data.tel_url);
      }).catch(function () {
        callBtn.disabled = false;
        setError(contactError, 'Не удалось сохранить действие. Попробуйте ещё раз.');
      });
    });
  }

  if (declineActions) {
    declineActions.querySelectorAll('[data-sr-action]').forEach(function (button) {
      button.addEventListener('click', function () {
        const action = button.getAttribute('data-sr-action');
        button.disabled = true;
        setError(declineError, '');
        postAction(action).then(function (data) {
          button.disabled = false;
          if (data.decline_message && declineNote) {
            declineNote.textContent = data.decline_message;
            declineNote.hidden = false;
          }
          if (data.ok || data._status === 409) {
            declineActions.hidden = true;
          }
          if (!data.ok) {
            setError(declineError, data.error || 'Не удалось сохранить ответ.');
          }
        }).catch(function () {
          button.disabled = false;
          setError(declineError, 'Не удалось сохранить ответ.');
        });
      });
    });
  }
})();
