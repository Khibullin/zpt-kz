(() => {
  const panel = document.querySelector('.service-filter-panel');
  if (!panel) return;
  const mobile = window.matchMedia('(max-width: 760px)');
  const adapt = () => { panel.open = !mobile.matches; };
  adapt();
  mobile.addEventListener('change', adapt);
})();
