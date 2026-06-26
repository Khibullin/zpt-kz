(function () {
  function getModal() {
    return document.getElementById('feedbackModal');
  }

  function getForm() {
    return document.getElementById('feedbackModalForm');
  }

  function getSuccessBox() {
    return document.getElementById('feedbackModalSuccess');
  }

  function getErrorBox() {
    return document.getElementById('feedbackModalError');
  }

  function resetModalState() {
    var form = getForm();
    var successBox = getSuccessBox();
    var errorBox = getErrorBox();

    if (form) {
      form.reset();
      form.hidden = false;
    }

    if (successBox) {
      successBox.hidden = true;
      successBox.textContent = '';
    }

    if (errorBox) {
      errorBox.hidden = true;
      errorBox.textContent = '';
    }
  }

  function openFeedbackModal(event) {
    if (event) {
      event.preventDefault();
    }

    var modal = getModal();
    if (!modal) {
      return;
    }

    resetModalState();
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('feedback-modal-open');

    var firstInput = document.getElementById('feedbackModalName');
    if (firstInput) {
      window.setTimeout(function () {
        firstInput.focus();
      }, 50);
    }
  }

  function closeFeedbackModal() {
    var modal = getModal();
    if (!modal) {
      return;
    }

    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('feedback-modal-open');
  }

  function bindTriggers() {
    document.querySelectorAll('[data-feedback-open]').forEach(function (link) {
      link.addEventListener('click', openFeedbackModal);
    });

    document.querySelectorAll('a[href*="/feedback"]').forEach(function (link) {
      if (!link.hasAttribute('data-feedback-open')) {
        link.setAttribute('data-feedback-open', '');
        link.addEventListener('click', openFeedbackModal);
      }
    });
  }

  function bindModalControls() {
    var modal = getModal();
    if (!modal) {
      return;
    }

    modal.querySelectorAll('[data-feedback-close]').forEach(function (el) {
      el.addEventListener('click', closeFeedbackModal);
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && modal.classList.contains('is-open')) {
        closeFeedbackModal();
      }
    });
  }

  function bindFormSubmit() {
    var form = getForm();
    var modal = getModal();
    if (!form || !modal) {
      return;
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();

      var errorBox = getErrorBox();
      var successBox = getSuccessBox();
      var submitUrl = modal.getAttribute('data-feedback-url') || '/feedback/';

      if (errorBox) {
        errorBox.hidden = true;
        errorBox.textContent = '';
      }

      fetch(submitUrl, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: new FormData(form),
      })
        .then(function (response) {
          return response.json().then(function (data) {
            return { ok: response.ok, data: data };
          });
        })
        .then(function (result) {
          if (result.ok && result.data.success) {
            form.hidden = true;
            if (successBox) {
              successBox.hidden = false;
              successBox.textContent = result.data.message;
            }
            return;
          }

          if (errorBox) {
            errorBox.hidden = false;
            errorBox.textContent = result.data.message || 'Не удалось отправить сообщение.';
          }
        })
        .catch(function () {
          if (errorBox) {
            errorBox.hidden = true;
            errorBox.textContent = 'Ошибка сети. Попробуйте ещё раз.';
            errorBox.hidden = false;
          }
        });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindTriggers();
    bindModalControls();
    bindFormSubmit();
  });

  window.ZPTFeedbackModal = {
    open: openFeedbackModal,
    close: closeFeedbackModal,
  };
})();
