/* Nova Wizard project page. Theme toggle and copy buttons.
   No network, no dependencies. Strings come from data- attributes on <body>
   so the English and Farsi pages can share this file. */
(function () {
  'use strict';

  var root = document.documentElement;
  var body = document.body;
  var S = body.dataset;

  /* ---- theme ---- */
  var btn = document.getElementById('theme');

  function paint() {
    if (!btn) return;
    var light = root.getAttribute('data-theme') === 'light';
    btn.textContent = light ? '☀' : '☾';
    btn.setAttribute('aria-label', light ? S.themeToDark : S.themeToLight);
  }

  if (btn) {
    btn.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('nova-theme', next); } catch (e) {}
      paint();
    });
    paint();
  }

  /* Follow the operating system while the reader has not picked a side. */
  try {
    var mq = window.matchMedia('(prefers-color-scheme: light)');
    var onSystem = function (e) {
      var stored = null;
      try { stored = localStorage.getItem('nova-theme'); } catch (err) {}
      if (stored) return;
      root.setAttribute('data-theme', e.matches ? 'light' : 'dark');
      paint();
    };
    if (mq.addEventListener) mq.addEventListener('change', onSystem);
    else if (mq.addListener) mq.addListener(onSystem);
  } catch (e) {}

  /* ---- toast ---- */
  var toast = document.getElementById('toast');
  var toastTimer = null;
  function say(text) {
    if (!toast) return;
    toast.textContent = text;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('show'); }, 1400);
  }

  /* ---- copy buttons ---- */
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function markDone(button) {
    button.dataset.done = '1';
    say(S.copied);
    setTimeout(function () { delete button.dataset.done; }, 1400);
  }

  Array.prototype.forEach.call(document.querySelectorAll('.copy'), function (button) {
    button.addEventListener('click', function () {
      var block = document.getElementById(button.getAttribute('data-for'));
      if (!block) return;
      var text = block.innerText.replace(/\s+$/, '');

      /* A button that does nothing at all is worse than one that says it
         failed. Some environments leave the clipboard promise pending
         forever, so the outcome is settled either way after 1.2 seconds. */
      var settled = false;
      function finish(ok) {
        if (settled) return;
        settled = true;
        if (ok || fallbackCopy(text)) markDone(button);
        else say(S.copyFailed);
      }

      if (navigator.clipboard && navigator.clipboard.writeText) {
        setTimeout(function () { finish(false); }, 1200);
        navigator.clipboard.writeText(text).then(
          function () { finish(true); },
          function () { finish(false); }
        );
      } else {
        finish(false);
      }
    });
  });
})();
