// Native disclosure: all filters remain available and submit without JavaScript.
(() => {
  const filters = document.querySelector('[data-dismantlers-filters]');
  if (!filters) return;
  const narrow = window.matchMedia('(max-width: 760px)');
  const adapt = () => { filters.open = !narrow.matches; };
  adapt();
  narrow.addEventListener('change', adapt);
})();
