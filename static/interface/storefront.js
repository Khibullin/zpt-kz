(() => {
  const root = () => document.querySelector('.retail-home');
  if (!root()) return;
  const groups = () => Array.from(document.querySelectorAll('[data-filter-key]'));
  const stateKey = 'zptStorefront';
  let pending;
  let navigation;
  try { navigation=JSON.parse(document.getElementById('vehicle-navigation')?.textContent || 'null'); } catch (_) {}
  history.scrollRestoration = 'manual';
  const status = document.createElement('span');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.className = 'retail-update-status';
  status.hidden = true;
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
  function pickerHref(target, key, value) {
    const url = new URL(target);
    const resets = {country:['brand','model','year','engine'],brand:['model','year','engine'],model:['year','engine']};
    (resets[key] || []).forEach(name => url.searchParams.delete(name));
    value ? url.searchParams.set(key,value) : url.searchParams.delete(key);
    url.searchParams.delete('page');
    const active = ['country','brand','model','year','engine','category','subgroup','maker','stock','q'].some(name => url.searchParams.get(name));
    active ? url.searchParams.set('picker','1') : url.searchParams.delete('picker');
    return url.pathname + url.search + url.hash;
  }
  function showPickerImmediately(target) {
    if (!navigation) return;
    const url = new URL(target);
    if (['q','stock','category','subgroup','maker'].some(key => url.searchParams.get(key)) || url.searchParams.get('model')) return;
    const brandId = url.searchParams.get('brand');
    const country = url.searchParams.get('country') || '';
    if (brandId && (!navigation[brandId] || (country && navigation[brandId].country !== country))) return;
    const options = brandId ? navigation[brandId].models : Object.entries(navigation)
      .filter(([,brand]) => !country || brand.country === country)
      .map(([value,brand]) => ({value,label:brand.name,count:brand.count,logo:brand.logo}))
      .sort((a,b) => { const x=a.label.toLocaleLowerCase(), y=b.label.toLocaleLowerCase(); return x<y ? -1 : x>y ? 1 : 0; });
    if (!options?.length) return;
    const picker = root().querySelector('.vehicle-picker');
    if (!picker) return;
    Array.from(picker.children).forEach(child => {
      if (!child.matches('h2, p, nav.region-pills')) child.remove();
    });
    picker.querySelectorAll('[data-picker-country]').forEach(link => {
      const value = link.dataset.pickerCountry;
      value === country ? link.setAttribute('aria-current','true') : link.removeAttribute('aria-current');
      link.href = pickerHref(target,'country',value === country ? '' : value);
    });
    const grid = document.createElement('div');
    grid.className = 'vehicle-grid';
    options.forEach((option,index) => {
      const link = document.createElement('a');
      link.className = 'vehicle-card';
      link.href = pickerHref(target,brandId ? 'model' : 'brand',option.value);
      link.rel = 'nofollow';
      link.hidden = index >= 7;
      if (option.logo) {
        const logo = document.createElement('img');
        logo.src = option.logo; logo.alt=''; logo.width=44; logo.height=44;
        logo.loading='lazy'; logo.decoding='async'; logo.addEventListener('error',() => { logo.hidden=true; }); link.append(logo);
      }
      const text = document.createElement('span');
      const name = document.createElement('span');
      name.className='vehicle-name'; name.textContent=option.label; text.append(name);
      if (option.count != null) {
        const count = document.createElement('small');
        count.textContent='Товаров: '+Number(option.count).toLocaleString('ru-RU'); text.append(count);
      }
      link.append(text); grid.append(link);
    });
    picker.append(grid);
    picker.dataset.instantPicker='';
    if (options.length > 7) {
      const expand = document.createElement('button');
      expand.type='button'; expand.className='show-options'; expand.dataset.expand='';
      expand.textContent=brandId ? 'Показать все модели' : 'Показать все марки'; picker.append(expand);
    }
  }
  async function update(url, anchorKey, historyState, label) {
    pending?.abort();
    const controller = new AbortController();
    pending = controller;
    remember(snapshot());
    root().setAttribute('aria-busy', 'true');
    const oldResults=root().querySelector('.retail-results');
    if (oldResults) oldResults.inert=true;
    status.hidden = false;
    status.textContent = 'Обновляем товары…';
    if (!historyState) showPickerImmediately(url);
    try {
      const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin'});
      if (!response.ok) throw new Error('Storefront response failed');
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      const content = next.querySelector('.retail-home');
      if (!navigation) { try { navigation=JSON.parse(next.getElementById('vehicle-navigation')?.textContent || 'null'); } catch (_) {} }
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
      if (pending === controller && error.name !== 'AbortError') location.assign(url);
    } finally {
      if (pending === controller) {
        root()?.removeAttribute('aria-busy');
        const results=root()?.querySelector('.retail-results');
        if (results) results.inert=false;
        status.hidden = true;
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
    const link = event.target.closest('.retail-filters a.filter-option, .vehicle-picker a, .selection-chips a, .retail-pages a');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname) return;
    event.preventDefault();
    update(url.href, link.closest('[data-filter-key]')?.dataset.filterKey, undefined, link.children[1]?.textContent);
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
