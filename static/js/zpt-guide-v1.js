(function () {
  'use strict';

  var searchEl = document.getElementById('zpt-guide-search');
  var emptyEl = document.getElementById('zpt-guide-empty');
  var items = Array.prototype.slice.call(document.querySelectorAll('[data-faq-item]'));
  var sections = Array.prototype.slice.call(document.querySelectorAll('[data-faq-section]'));
  var form = document.getElementById('zpt-guide-feedback-form');
  var successEl = document.getElementById('zpt-guide-feedback-success');
  var errorEl = document.getElementById('zpt-guide-feedback-error');
  var handoffEl = document.getElementById('help-handoff');
  var messageEl = form ? form.querySelector('[name="message"]') : null;

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    if (match) return decodeURIComponent(match[1]);
    var hidden = form && form.querySelector('[name=csrfmiddlewaretoken]');
    return hidden ? hidden.value : '';
  }

  function lastUserQuestion() {
    var bubbles = document.querySelectorAll('[data-help-role="user"]');
    if (!bubbles.length) return '';
    return (bubbles[bubbles.length - 1].textContent || '').trim();
  }

  function normalize(text) {
    return String(text || '')
      .toLowerCase()
      .replace(/вин/g, 'vin');
  }

  function filterFaq() {
    var query = searchEl ? normalize(searchEl.value).trim() : '';
    var visible = 0;
    items.forEach(function (item) {
      var text = normalize(item.textContent || '');
      var match = !query || text.indexOf(query) !== -1;
      item.hidden = !match;
      if (match) visible += 1;
    });
    sections.forEach(function (section) {
      var any = section.querySelector('[data-faq-item]:not([hidden])');
      section.hidden = !any;
    });
    if (emptyEl) emptyEl.hidden = visible !== 0;
  }

  if (searchEl) {
    searchEl.addEventListener('input', filterFaq);
  }

  if (handoffEl) {
    handoffEl.addEventListener('click', function () {
      var question = lastUserQuestion();
      if (messageEl) {
        if (question) messageEl.value = question;
        messageEl.focus();
      }
      if (form && typeof form.scrollIntoView === 'function') {
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      var status = document.getElementById('help-status');
      if (status) {
        status.textContent = 'Проверьте текст обращения и отправьте форму, если всё верно.';
      }
    });
  }

  if (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      if (successEl) {
        successEl.hidden = true;
        successEl.textContent = '';
      }
      if (errorEl) {
        errorEl.hidden = true;
        errorEl.textContent = '';
      }
      var submit = form.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      var body = new FormData(form);
      fetch(form.action, {
        method: 'POST',
        body: body,
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrfToken(),
          'X-Requested-With': 'XMLHttpRequest',
        },
      }).then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        }).catch(function () {
          return { ok: false, data: {} };
        });
      }).then(function (result) {
        if (result.ok && result.data && result.data.success) {
          form.reset();
          if (successEl) {
            successEl.hidden = false;
            successEl.textContent = result.data.message || 'Обращение принято.';
          }
          return;
        }
        if (errorEl) {
          errorEl.hidden = false;
          errorEl.textContent = (result.data && result.data.message) ||
            'Не удалось отправить обращение. Проверьте поля и попробуйте ещё раз.';
        }
      }).catch(function () {
        if (errorEl) {
          errorEl.hidden = false;
          errorEl.textContent = 'Не удалось отправить обращение. Проверьте соединение.';
        }
      }).then(function () {
        if (submit) submit.disabled = false;
      });
    });
  }
})();
