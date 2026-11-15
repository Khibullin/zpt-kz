const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('static/office/navigation.js', 'utf8');
function visit(url, store, {listBack = null, disabled = false} = {}) {
  const events = {};
  const back = {href: listBack, hasAttribute: name => !!listBack && name === 'data-office-list-back'};
  const scroll = [];
  vm.runInNewContext(code, {
    location: new URL(url, 'https://zpt.kz'),
    sessionStorage: {
      getItem: key => {if (disabled) throw Error(); return store.get(key) ?? null;},
      setItem: (key, value) => store.set(key, value)
    },
    window: {scrollY: 725, addEventListener: (name, fn) => {events[name] = fn;}, scrollTo: (x, y) => scroll.push(y)},
    document: {querySelector: () => back},
    requestAnimationFrame: fn => fn()
  });
  return {back, events, scroll};
}
test('list → card → pricing → card → list preserves return route and scroll', () => {
  const store = new Map();
  const list = '/office/procurement/kaspi-products/?batch=receipt:1&q=WXQP';
  const card = list + '&line=0';
  const first = visit(list, store); first.events.pagehide();
  visit(card, store, {listBack: list});
  assert.equal(visit('/office/procurement/kaspi-products/pricing/', store).back.href, card);
  assert.equal(visit(card, store, {listBack: list}).back.href, list);
  const restored = visit(list, store); restored.events.pageshow();
  assert.deepEqual(restored.scroll, [725]);
  assert.equal(restored.back.href, '/office/procurement/');
});
test('direct entry and disabled storage have parent fallback', () => {
  assert.equal(visit('/office/procurement/kaspi-products/pricing/', new Map()).back.href, '/office/procurement/kaspi-products/');
  assert.equal(visit('/office/procurement/kaspi-products/pricing/', new Map(), {disabled: true}).back.href, '/office/procurement/kaspi-products/');
});
test('saving same pricing URL does not create a back loop', () => {
  const store = new Map();
  visit('/office/procurement/kaspi-products/?line=0', store);
  visit('/office/procurement/kaspi-products/pricing/', store);
  assert.equal(visit('/office/procurement/kaspi-products/pricing/', store).back.href, '/office/procurement/kaspi-products/?line=0');
});
