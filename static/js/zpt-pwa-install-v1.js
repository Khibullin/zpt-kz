(function () {
  'use strict';

  var SESSION_KEY = 'zptPwaInstallDismissed';
  var deferredPrompt = null;

  function isStandalone() {
    if (window.navigator.standalone === true) return true;
    return window.matchMedia('(display-mode: standalone)').matches;
  }

  function ua() {
    return String(window.navigator.userAgent || '');
  }

  function isAndroid() {
    return /Android/i.test(ua());
  }

  function isIos() {
    var agent = ua();
    var platform = window.navigator.platform || '';
    return /iPad|iPhone|iPod/.test(agent) ||
      (platform === 'MacIntel' && window.navigator.maxTouchPoints > 1);
  }

  function isIosSafari() {
    var agent = ua();
    if (!isIos()) return false;
    return !/CriOS|FxiOS|EdgiOS|OPiOS/.test(agent);
  }

  function instructionsText() {
    if (isIosSafari()) {
      return 'В Safari нажмите «Поделиться», затем «На экран Домой».';
    }
    if (isIos()) {
      return 'Откройте zpt.kz в Safari, нажмите «Поделиться» и выберите «На экран Домой».';
    }
    if (isAndroid()) {
      return 'В меню браузера выберите «Установить приложение» или «Добавить на главный экран».';
    }
    return 'В меню браузера выберите установку приложения или добавление ярлыка на рабочий стол.';
  }

  function wasDismissedThisVisit() {
    try {
      return window.sessionStorage.getItem(SESSION_KEY) === '1';
    } catch (err) {
      return false;
    }
  }

  function markDismissedThisVisit() {
    try {
      window.sessionStorage.setItem(SESSION_KEY, '1');
    } catch (err) {
      /* ignore */
    }
  }

  function hidePrompt(root) {
    if (!root) return;
    root.hidden = true;
    root.setAttribute('aria-hidden', 'true');
  }

  function showInstructions(root) {
    var box = root.querySelector('[data-zpt-pwa-instructions]');
    if (!box) return;
    box.hidden = false;
    box.textContent = instructionsText();
  }

  function tryNativeInstall(root) {
    if (!deferredPrompt || typeof deferredPrompt.prompt !== 'function') {
      showInstructions(root);
      return;
    }
    var promptEvent = deferredPrompt;
    deferredPrompt = null;
    promptEvent.prompt();
    if (!promptEvent.userChoice || typeof promptEvent.userChoice.then !== 'function') {
      return;
    }
    promptEvent.userChoice.then(function (choice) {
      if (choice && choice.outcome === 'accepted') {
        hidePrompt(root);
        markDismissedThisVisit();
      }
    }).catch(function () {
      showInstructions(root);
    });
  }

  function bindPrompt(root) {
    if (!root) return;
    root.querySelectorAll('[data-zpt-pwa-dismiss]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        hidePrompt(root);
        markDismissedThisVisit();
      });
    });
    var addBtn = root.querySelector('[data-zpt-pwa-add]');
    if (addBtn) {
      addBtn.addEventListener('click', function () {
        tryNativeInstall(root);
      });
    }
  }

  function maybeShowHomePrompt() {
    var root = document.querySelector('[data-zpt-pwa="home-success"]');
    if (!root) return;
    if (isStandalone() || wasDismissedThisVisit()) {
      hidePrompt(root);
      return;
    }
    root.hidden = false;
    root.setAttribute('aria-hidden', 'false');
    bindPrompt(root);
  }

  function initGuidePanel() {
    var panel = document.querySelector('[data-zpt-pwa="guide"]');
    if (!panel) return;
    var box = panel.querySelector('[data-zpt-pwa-instructions]');
    if (box) {
      box.hidden = false;
      box.textContent = isStandalone()
        ? 'Приложение уже открыто с главного экрана.'
        : instructionsText();
    }
    var addBtn = panel.querySelector('[data-zpt-pwa-add]');
    if (addBtn) {
      if (isStandalone()) {
        addBtn.hidden = true;
      } else {
        addBtn.addEventListener('click', function () {
          tryNativeInstall(panel);
        });
      }
    }
  }

  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    deferredPrompt = event;
  });

  function init() {
    maybeShowHomePrompt();
    initGuidePanel();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
