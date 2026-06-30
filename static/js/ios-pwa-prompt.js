(function () {
  'use strict';

  var STORAGE_KEY = 'ios_pwa_prompt_dismissed';
  var SHOW_DELAY_MS = 3000;

  function isIOSDevice() {
    var ua = window.navigator.userAgent || '';
    var platform = window.navigator.platform || '';
    var isAppleMobile =
      /iPad|iPhone|iPod/.test(ua) ||
      (platform === 'MacIntel' && window.navigator.maxTouchPoints > 1);
    var isExcluded = /CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
    return isAppleMobile && !isExcluded;
  }

  function isStandaloneMode() {
    if (window.navigator.standalone === true) {
      return true;
    }
    return window.matchMedia('(display-mode: standalone)').matches;
  }

  function wasDismissed() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === '1';
    } catch (error) {
      return false;
    }
  }

  function markDismissed() {
    try {
      window.localStorage.setItem(STORAGE_KEY, '1');
    } catch (error) {
      /* ignore */
    }
  }

  function initIosPwaPrompt() {
    var prompt = document.getElementById('ios-pwa-prompt');
    if (!prompt) {
      return;
    }

    if (!isIOSDevice() || isStandaloneMode() || wasDismissed()) {
      return;
    }

    var closeButton = prompt.querySelector('[data-ios-pwa-close]');

    window.setTimeout(function () {
      prompt.classList.add('is-visible');
      prompt.setAttribute('aria-hidden', 'false');
    }, SHOW_DELAY_MS);

    if (closeButton) {
      closeButton.addEventListener('click', function () {
        prompt.classList.remove('is-visible');
        prompt.setAttribute('aria-hidden', 'true');
        markDismissed();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initIosPwaPrompt);
  } else {
    initIosPwaPrompt();
  }
})();
