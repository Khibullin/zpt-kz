(() => {
  const root = () => document.querySelector('.retail-home');
  if (!root()) return;
  const groups = () => Array.from(document.querySelectorAll('[data-filter-key]'));
  const stateKey = 'zptStorefront';
  let pending;
  history.scrollRestoration = 'manual';
  const status = document.createElement('span');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)';
  root().after(status);

  function search(input) {
    const query = input.value.toLocaleLowerCase();
    input.nextElementSibling.querySelectorAll('.filter-option').forEach(option => {
      option.hidden = !option.textContent.toLocaleLowerCase().includes(query);
    });
  }
  function snapshot(anchorKey) {
    const anchor = groups().find(group => group.dataset.filterKey === anchorKey);
    return {
      y: window.scrollY,
      open: document.querySelector('.retail-filters').open,
      anchorKey,
      anchorTop: anchor?.getBoundingClientRect().top,
      groups: groups().map(group => ({
        key: group.dataset.filterKey, open: group.open,
        query: group.querySelector('input').value,
        top: group.querySelector('.filter-options').scrollTop
      }))
    };
  }
  function restore(state, keepAnchor) {
    document.querySelector('.retail-filters').open = state.open;
    groups().forEach(group => {
      const saved = state.groups.find(item => item.key === group.dataset.filterKey);
      if (!saved) return;
      group.open = saved.open;
      const input = group.querySelector('input');
      input.value = saved.query;
      search(input);
      group.querySelector('.filter-options').scrollTop = saved.top;
    });
    const anchor = groups().find(group => group.dataset.filterKey === state.anchorKey);
    // The vehicle picker changes height when a brand/model is selected.
    // Keep the active filter at the same screen position, not just the same scrollY.
    const y = keepAnchor && anchor && Number.isFinite(state.anchorTop)
      ? window.scrollY + anchor.getBoundingClientRect().top - state.anchorTop
      : state.y;
    window.scrollTo({top: y, behavior: 'instant'});
  }
  function remember(state) {
    history.replaceState({...history.state, [stateKey]: state}, '', location.href);
  }
  async function update(url, anchorKey, historyState, label) {
    pending?.abort();
    const controller = new AbortController();
    pending = controller;
    remember(snapshot());
    root().setAttribute('aria-busy', 'true');
    status.textContent = 'Обновляем товары…';
    try {
      const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin'});
      if (!response.ok) throw new Error('Storefront response failed');
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      const content = next.querySelector('.retail-home');
      if (!content || new URL(response.url).origin !== location.origin) throw new Error('Invalid storefront');
      if (controller.signal.aborted) return;
      const saved = historyState || snapshot(anchorKey);
      if (!historyState) remember(snapshot());
      root().replaceWith(content);
      document.title = next.title;
      if (!historyState) history.pushState(null, '', url);
      if (label) {
        const group = groups().find(item => item.dataset.filterKey === anchorKey);
        const option = Array.from(group?.querySelectorAll('.filter-option') || []).find(item => item.children[1]?.textContent === label);
        (option || group?.querySelector('summary'))?.focus({preventScroll: true});
      }
      restore(saved, !historyState);
      remember(snapshot());
      document.dispatchEvent(new CustomEvent('zpt:products-added', {detail: content}));
      status.textContent = content.querySelector('#results-title').textContent.trim();
    } catch (error) {
      if (error.name !== 'AbortError') location.assign(url);
    } finally {
      if (pending === controller) {
        root()?.removeAttribute('aria-busy');
        pending = null;
      }
    }
  }

  document.addEventListener('input', event => {
    if (event.target.matches('[data-filter-search]')) search(event.target);
  });
  document.addEventListener('click', event => {
    const expand = event.target.closest('[data-expand]');
    if (expand) {
      expand.previousElementSibling.querySelectorAll('[hidden]').forEach(card => card.hidden = false);
      expand.hidden = true;
      return;
    }
    const link = event.target.closest('.retail-filters a.filter-option');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname) return;
    event.preventDefault();
    update(url.href, link.closest('[data-filter-key]').dataset.filterKey, undefined, link.children[1]?.textContent);
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!form.matches('.stock-filter, .results-heading form')) return;
    event.preventDefault();
    const url = new URL(location.pathname, location.origin);
    url.search = new URLSearchParams(new FormData(form)).toString();
    update(url.href, form.matches('.stock-filter') ? 'maker' : undefined);
  });
  window.addEventListener('popstate', event => {
    if (event.state?.[stateKey]) update(location.href, undefined, event.state[stateKey]);
    else location.reload();
  });
  const filters = document.querySelector('.retail-filters');
  if (matchMedia('(max-width:767px)').matches) filters.open = false;
  remember(snapshot());
})();
