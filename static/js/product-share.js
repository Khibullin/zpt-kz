(function () {
  function absoluteProductUrl(path) {
    if (!path || path.charAt(0) !== '/') return '';
    if (path.indexOf('?') !== -1) return '';
    try {
      return new URL(path, window.location.origin).href;
    } catch (err) {
      return '';
    }
  }

  function showCopied(button) {
    var copied = button.querySelector('.product-share-copied');
    if (!copied) return;
    copied.hidden = false;
    button.classList.add('is-copied');
    window.setTimeout(function () {
      copied.hidden = true;
      button.classList.remove('is-copied');
    }, 1800);
  }

  function copyUrl(url) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(url);
    }
    return new Promise(function (resolve, reject) {
      var input = document.createElement('textarea');
      input.value = url;
      input.setAttribute('readonly', '');
      input.style.position = 'fixed';
      input.style.left = '-9999px';
      document.body.appendChild(input);
      input.select();
      try {
        if (!document.execCommand('copy')) {
          reject(new Error('copy failed'));
          return;
        }
        resolve();
      } catch (err) {
        reject(err);
      } finally {
        document.body.removeChild(input);
      }
    });
  }

  function shareProduct(button) {
    var path = button.getAttribute('data-share-path') || '';
    var title = button.getAttribute('data-share-title') || '';
    var url = absoluteProductUrl(path);
    if (!url) return;

    var payload = { title: title, url: url };
    if (navigator.share) {
      navigator.share(payload).catch(function (err) {
        if (err && err.name === 'AbortError') return;
        copyUrl(url).then(function () {
          showCopied(button);
        }).catch(function () {});
      });
      return;
    }

    copyUrl(url).then(function () {
      showCopied(button);
    }).catch(function () {});
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-product-share]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    shareProduct(button);
  });
})();
