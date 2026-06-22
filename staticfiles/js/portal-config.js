(function (global) {
  'use strict';

  var body = document.body;
  var origin = global.location.origin;

  function joinBase(path) {
    if (!path) {
      return origin + '/api/';
    }

    if (path.charAt(0) !== '/') {
      path = '/' + path;
    }

    return origin + path;
  }

  global.ZPT_CONFIG = {
    apiBase: joinBase(body && body.dataset.apiBase),
    serviceApiBase: joinBase(body && body.dataset.serviceApiBase),
  };
})(window);
