(() => {
  if (!/^\/(office|control|marketing|admin)\//.test(location.pathname)) return;
  const current = location.pathname + location.search;
  const key = 'office-scroll:' + current;
  const parent = location.pathname.replace(/[^/]+\/$/, '') || '/office/';
  let previous = location.pathname === '/office/' ? '/' : parent === '/' || parent === location.pathname ? '/office/' : parent;
  try {
    let trail = JSON.parse(sessionStorage.getItem('office-page-trail') || '[]');
    if (!Array.isArray(trail)) trail = [];
    if (trail[trail.length - 1] !== current) {
      const index = trail.lastIndexOf(current);
      trail = index >= 0 ? trail.slice(0, index + 1) : [...trail, current].slice(-40);
    }
    previous = trail.length > 1 ? trail[trail.length - 2] : previous;
    sessionStorage.setItem('office-page-trail', JSON.stringify(trail));
    window.addEventListener('pagehide', () => sessionStorage.setItem(key, String(window.scrollY)));
    window.addEventListener('pageshow', () => {
      const saved = sessionStorage.getItem(key);
      if (saved !== null) requestAnimationFrame(() => window.scrollTo(0, Number(saved)));
    });
  } catch (_) { /* Navigation also works with storage disabled. */ }
  let back = document.querySelector('[data-office-back], [data-office-list-back]');
  if (!back) {
    const nav = document.createElement('nav');
    nav.className = 'office-home-nav';
    nav.setAttribute('aria-label', 'Возврат');
    back = document.createElement('a');
    back.className = 'button office-back-link';
    back.textContent = '← Назад';
    back.setAttribute('data-office-back', '');
    nav.appendChild(back);
    const main = document.querySelector('main') || document.body;
    main.prepend(nav);
  }
  if (!back.hasAttribute('data-office-list-back')) back.href = previous;
})();
