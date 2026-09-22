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

  function isWebView() {
    var agent = ua();
    return /FBAN|FBAV|Instagram|Line\/|Twitter|TikTok|Snapchat|WhatsApp|MicroMessenger|wv\)|; wv/i.test(agent);
  }

  function isIosSafari() {
    var agent = ua();
    if (!isIos()) return false;
    return !/CriOS|FxiOS|EdgiOS|OPiOS|Instagram|FBAN|FBAV/.test(agent);
  }

  function isChrome() {
    var agent = ua();
    return /Chrome|CriOS/.test(agent) && !/Edg|OPR|SamsungBrowser/.test(agent);
  }

  function isEdge() {
    return /Edg/.test(ua());
  }

  function isSamsung() {
    return /SamsungBrowser/i.test(ua());
  }

  function isFirefox() {
    return /Firefox|FxiOS/i.test(ua());
  }

  function isSafariDesktop() {
    var agent = ua();
    return /Safari/.test(agent) && !/Chrome|Chromium|Edg|OPR/.test(agent) && !isIos() && !isAndroid();
  }

  function instructionsFor(kind) {
    if (kind === 'webview') {
      return 'Установка из этого встроенного браузера недоступна. Откройте zpt.kz в Chrome на Android или в Safari на iPhone, затем добавьте иконку.';
    }
    if (kind === 'ios') {
      if (isIosSafari() || kind === 'ios') {
        return 'В Safari нажмите «Поделиться» (квадрат со стрелкой), прокрутите список и выберите «На экран „Домой“». Затем нажмите «Добавить». Если пункта нет: «Поделиться» → «Изменить действия» → включите «На экран „Домой“».';
      }
    }
    if (kind === 'android') {
      if (isChrome() || isSamsung() || isEdge()) {
        return 'В Chrome откройте меню «⋮» справа вверху → «Добавить на главный экран» → «Установить приложение». Если браузер предлагает системное окно установки — подтвердите его. В Samsung Internet: меню → «Добавить страницу на» → «Главный экран».';
      }
      if (isFirefox()) {
        return 'В Firefox на Android откройте меню → «Установить». Если пункта нет, выберите «Добавить на главный экран».';
      }
      return 'Откройте меню браузера и выберите «Установить приложение» или «Добавить на главный экран».';
    }
    if (kind === 'desktop') {
      if (isChrome()) {
        return 'В Google Chrome нажмите меню «⋮» → «Сохранить и поделиться» → «Установить страницу как приложение». Либо значок установки в правой части адресной строки, если он появился.';
      }
      if (isEdge()) {
        return 'В Microsoft Edge откройте меню «…» → «Приложения» → «Установить этот сайт как приложение».';
      }
      if (isSafariDesktop()) {
        return 'В Safari на Mac: меню «Файл» → «Добавить на Dock…», затем подтвердите.';
      }
      if (isFirefox()) {
        return 'Firefox на компьютере не устанавливает сайт как приложение. Добавьте закладку или откройте zpt.kz в Chrome либо Edge и установите оттуда.';
      }
      return 'В меню браузера найдите «Установить приложение» / «Установить страницу как приложение», либо значок установки в адресной строке.';
    }
    return '';
  }

  function detectedKind() {
    if (isStandalone()) return 'standalone';
    if (isWebView()) return 'webview';
    if (isIos()) return 'ios';
    if (isAndroid()) return 'android';
    return 'desktop';
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
    } catch (err) { /* ignore */ }
  }

  function hidePrompt(root) {
    if (!root) return;
    root.hidden = true;
    root.setAttribute('aria-hidden', 'true');
  }

  function fillInstructions(box, kind) {
    if (!box) return;
    box.hidden = false;
    box.textContent = instructionsFor(kind);
  }

  function tryNativeInstall(root) {
    if (!deferredPrompt || typeof deferredPrompt.prompt !== 'function') {
      return false;
    }
    var promptEvent = deferredPrompt;
    deferredPrompt = null;
    promptEvent.prompt();
    if (promptEvent.userChoice && typeof promptEvent.userChoice.then === 'function') {
      promptEvent.userChoice.then(function (choice) {
        if (choice && choice.outcome === 'accepted') {
          hidePrompt(root);
          markDismissedThisVisit();
          var status = root.querySelector('[data-zpt-pwa-instructions]');
          if (status) {
            status.hidden = false;
            status.textContent = 'Иконка добавлена.';
          }
        }
      }).catch(function () { /* keep instructions */ });
    }
    return true;
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
        if (!tryNativeInstall(root)) {
          var box = root.querySelector('[data-zpt-pwa-instructions]');
          fillInstructions(box, detectedKind());
          box.hidden = false;
        }
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
    var toggle = panel.querySelector('[data-zpt-pwa-toggle]');
    var boxWrap = panel.querySelector('[data-zpt-pwa-panel]');
    var box = panel.querySelector('[data-zpt-pwa-instructions]');
    var addBtn = panel.querySelector('[data-zpt-pwa-add]');
    var pick = panel.querySelector('[data-zpt-pwa-pick]');
    var kind = detectedKind();

    function showGuideInstructions(selected) {
      if (boxWrap) boxWrap.hidden = false;
      if (kind === 'standalone') {
        fillInstructions(box, 'standalone');
        if (box) box.textContent = 'Приложение уже открыто с главного экрана.';
        if (addBtn) addBtn.hidden = true;
        if (pick) pick.hidden = true;
        return;
      }
      fillInstructions(box, selected || kind);
      if (addBtn) {
        addBtn.hidden = !(deferredPrompt && selected !== 'ios' && selected !== 'webview');
      }
      if (pick) pick.hidden = false;
    }

    if (toggle) {
      toggle.addEventListener('click', function () {
        if (boxWrap && !boxWrap.hidden) {
          boxWrap.hidden = true;
          toggle.setAttribute('aria-expanded', 'false');
          return;
        }
        toggle.setAttribute('aria-expanded', 'true');
        showGuideInstructions(kind);
      });
    }
    if (pick) {
      pick.addEventListener('click', function (event) {
        var btn = event.target.closest('[data-pwa-kind]');
        if (!btn) return;
        showGuideInstructions(btn.getAttribute('data-pwa-kind'));
      });
    }
    if (addBtn) {
      addBtn.addEventListener('click', function () {
        if (!tryNativeInstall(panel)) {
          showGuideInstructions(kind);
        }
      });
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
