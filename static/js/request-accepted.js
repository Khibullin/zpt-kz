(function () {
  'use strict';
  window.ZPTRequestAccepted = function (form, options) {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({event: 'zpt_request_submitted'});
    const panel = document.createElement('section');
    panel.setAttribute('role', 'status');
    panel.setAttribute('tabindex', '-1');
    panel.style.cssText = 'padding:24px;border:1px solid #bbf7d0;border-radius:16px;background:#f0fdf4;color:#14532d;line-height:1.6;overflow-wrap:anywhere';
    const title = document.createElement('h2');
    title.style.cssText = 'margin:0 0 12px;font-size:24px;color:#166534';
    title.textContent = '✓ Ваша заявка принята!';
    const message = document.createElement('p');
    message.textContent = options.message;
    panel.append(title, message);
    if (options.url) {
      const link = document.createElement('a');
      link.href = options.url;
      link.textContent = 'Открыть страницу заявки';
      link.style.cssText = 'display:inline-block;margin-top:8px;color:#166534;text-decoration:underline';
      panel.appendChild(link);
    }
    form.after(panel);
    form.hidden = true;
    panel.focus({preventScroll:true});
    panel.scrollIntoView({behavior:'smooth', block:'center'});
    return panel;
  };
})();
