(function () {
  var toggle = document.getElementById('cp-menu-toggle');
  var sidebar = document.getElementById('cp-sidebar');
  if (!toggle || !sidebar) return;
  toggle.addEventListener('click', function () {
    sidebar.classList.toggle('is-open');
  });
})();
