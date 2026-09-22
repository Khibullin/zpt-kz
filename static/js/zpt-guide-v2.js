(function () {
  'use strict';

  var dataEl = document.getElementById('zpt-guide-faq-data');
  var items = [];
  try {
    items = dataEl ? JSON.parse(dataEl.textContent || '[]') : [];
  } catch (err) {
    items = [];
  }
  var itemMap = {};
  items.forEach(function (item) {
    itemMap[item.id] = item;
  });
  var topics = (window.ZPT_GUIDE && window.ZPT_GUIDE.topics) || [];
  var voteUrl = (window.ZPT_GUIDE && window.ZPT_GUIDE.voteUrl) || '/api/zpt-guide/faq-vote/';

  var searchEl = document.getElementById('zpt-guide-search');
  var homeEl = document.getElementById('zpt-guide-faq-home');
  var resultsEl = document.getElementById('zpt-guide-faq-results');
  var resultsList = document.getElementById('zpt-guide-results-list');
  var resultsTitle = document.getElementById('zpt-guide-results-title');
  var emptyEl = document.getElementById('zpt-guide-empty');
  var backEl = document.getElementById('zpt-guide-faq-back');
  var form = document.getElementById('zpt-guide-feedback-form');
  var successEl = document.getElementById('zpt-guide-feedback-success');
  var errorEl = document.getElementById('zpt-guide-feedback-error');
  var dialog = document.getElementById('pomoshchnik');
  var openBtn = document.getElementById('zpt-guide-open-assistant');
  var closeBtn = document.getElementById('zpt-guide-assistant-close');
  var lastFocus = null;
  var hashFromUser = false;

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    if (match) return decodeURIComponent(match[1]);
    var hidden = document.querySelector('[name=csrfmiddlewaretoken]');
    return hidden ? hidden.value : '';
  }

  function normalize(text) {
    return String(text || '').toLowerCase().replace(/вин/g, 'vin');
  }

  function setNav(section) {
    document.querySelectorAll('[data-guide-nav]').forEach(function (el) {
      var current = (
        (section === 'faq' && el.getAttribute('data-guide-nav') === 'faq') ||
        (section === 'feedback' && el.getAttribute('data-guide-nav') === 'feedback') ||
        (section === 'assistant' && el.getAttribute('data-guide-nav') === 'assistant')
      );
      if (current) el.setAttribute('aria-current', 'page');
      else el.removeAttribute('aria-current');
    });
  }

  function showPanel(name) {
    document.querySelectorAll('[data-guide-panel]').forEach(function (panel) {
      panel.hidden = panel.getAttribute('data-guide-panel') !== name;
    });
    setNav(name === 'feedback' ? 'feedback' : 'faq');
  }

  function closeExclusive(except) {
    document.querySelectorAll('.zpt-guide-faq.is-open').forEach(function (row) {
      if (row === except) return;
      row.classList.remove('is-open');
      var btn = row.querySelector('.zpt-guide-faq__q');
      var answer = row.querySelector('.zpt-guide-faq__a');
      if (btn) btn.setAttribute('aria-expanded', 'false');
      if (answer) answer.hidden = true;
    });
  }

  function applyVoteView(root, data) {
    if (!root || !data) return;
    var stats = root.querySelector('[data-vote-stats]');
    var error = root.querySelector('[data-vote-error]');
    if (error) {
      error.hidden = true;
      error.textContent = '';
    }
    root.querySelectorAll('[data-vote]').forEach(function (btn) {
      var yes = btn.getAttribute('data-vote') === 'yes';
      btn.classList.toggle('is-active', data.my_vote === yes);
    });
    if (stats) {
      if (data.total > 0 && data.helpful_percent !== null && data.helpful_percent !== undefined) {
        stats.hidden = false;
        stats.textContent = data.helpful_percent + '% считают ответ полезным';
      } else {
        stats.hidden = true;
        stats.textContent = '';
      }
    }
  }

  function bindVote(root, faqId) {
    var box = root.querySelector('[data-faq-vote]');
    if (!box) return;
    var item = itemMap[faqId];
    if (item) applyVoteView(box, item);
    box.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-vote]');
      if (!btn) return;
      var helpful = btn.getAttribute('data-vote') === 'yes';
      var error = box.querySelector('[data-vote-error]');
      fetch(voteUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ faq_id: faqId, helpful: helpful }),
      }).then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok && data.ok, data: data };
        }).catch(function () {
          return { ok: false, data: {} };
        });
      }).then(function (result) {
        if (!result.ok) {
          if (error) {
            error.hidden = false;
            error.textContent = (result.data && result.data.message) ||
              'Не удалось сохранить оценку. Попробуйте ещё раз.';
          }
          return;
        }
        if (itemMap[faqId]) {
          itemMap[faqId].total = result.data.total;
          itemMap[faqId].helpful_percent = result.data.helpful_percent;
          itemMap[faqId].my_vote = result.data.my_vote;
        }
        applyVoteView(box, result.data);
      }).catch(function () {
        if (error) {
          error.hidden = false;
          error.textContent = 'Не удалось сохранить оценку. Попробуйте ещё раз.';
        }
      });
    });
  }

  function renderFaqList(target, ids, numbered) {
    target.replaceChildren();
    ids.forEach(function (faqId, index) {
      var item = itemMap[faqId];
      if (!item) return;
      var li = document.createElement('li');
      li.className = 'zpt-guide-faq';
      li.setAttribute('data-faq-id', item.id);
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'zpt-guide-faq__q';
      btn.setAttribute('aria-expanded', 'false');
      var num = document.createElement('span');
      num.className = 'zpt-guide-faq__num';
      num.textContent = numbered ? String(index + 1) : '';
      var text = document.createElement('span');
      text.className = 'zpt-guide-faq__text';
      text.textContent = item.question;
      var arrow = document.createElement('span');
      arrow.className = 'zpt-guide-faq__arrow';
      arrow.setAttribute('aria-hidden', 'true');
      btn.appendChild(num);
      btn.appendChild(text);
      btn.appendChild(arrow);
      var answer = document.createElement('div');
      answer.className = 'zpt-guide-faq__a';
      answer.hidden = true;
      var p = document.createElement('p');
      p.textContent = item.answer;
      answer.appendChild(p);
      if (item.links && item.links.length) {
        var links = document.createElement('p');
        links.className = 'zpt-guide-faq__links';
        item.links.forEach(function (link) {
          var a = document.createElement('a');
          a.href = link.url;
          a.textContent = link.label;
          links.appendChild(a);
        });
        answer.appendChild(links);
      }
      var updated = document.createElement('p');
      updated.className = 'zpt-guide-faq__updated';
      updated.textContent = item.updated_at ? ('Обновлено ' + item.updated_at) : '';
      answer.appendChild(updated);
      var vote = document.createElement('div');
      vote.className = 'zpt-guide-faq__vote';
      vote.setAttribute('data-faq-vote', item.id);
      vote.innerHTML =
        '<p class="zpt-guide-faq__vote-q">Ответ был полезным?</p>' +
        '<div class="zpt-guide-faq__vote-btns">' +
        '<button type="button" data-vote="yes">Да</button>' +
        '<button type="button" data-vote="no">Нет</button>' +
        '</div>' +
        '<p class="zpt-guide-faq__vote-stats" data-vote-stats hidden></p>' +
        '<p class="zpt-guide-faq__vote-error" data-vote-error hidden></p>';
      answer.appendChild(vote);
      li.appendChild(btn);
      li.appendChild(answer);
      target.appendChild(li);
      bindVote(li, item.id);
    });
  }

  function showResults(title, ids, numbered) {
    if (homeEl) homeEl.hidden = true;
    if (resultsEl) resultsEl.hidden = false;
    if (resultsTitle) resultsTitle.textContent = title;
    renderFaqList(resultsList, ids, numbered);
    if (emptyEl) emptyEl.hidden = ids.length !== 0;
  }

  function showHome() {
    if (homeEl) homeEl.hidden = false;
    if (resultsEl) resultsEl.hidden = true;
    if (emptyEl) emptyEl.hidden = true;
  }

  function searchFaq(query) {
    var needle = normalize(query).trim();
    if (!needle) {
      showHome();
      return;
    }
    var ids = items.filter(function (item) {
      return normalize(item.question + ' ' + item.answer).indexOf(needle) !== -1;
    }).map(function (item) { return item.id; });
    showResults('Результаты поиска', ids, false);
  }

  document.addEventListener('click', function (event) {
    var questionBtn = event.target.closest('.zpt-guide-faq__q');
    if (questionBtn) {
      var row = questionBtn.closest('.zpt-guide-faq');
      var answer = row && row.querySelector('.zpt-guide-faq__a');
      var open = questionBtn.getAttribute('aria-expanded') === 'true';
      closeExclusive(open ? null : row);
      if (row && answer) {
        row.classList.toggle('is-open', !open);
        questionBtn.setAttribute('aria-expanded', open ? 'false' : 'true');
        answer.hidden = open;
      }
      return;
    }
    var topicBtn = event.target.closest('[data-topic]');
    if (topicBtn) {
      var topicId = topicBtn.getAttribute('data-topic');
      var topic = topics.filter(function (item) { return item.id === topicId; })[0];
      if (!topic) return;
      document.querySelectorAll('[data-topic]').forEach(function (btn) {
        btn.classList.toggle('is-active', btn === topicBtn);
      });
      showResults(topic.title, topic.itemIds || [], false);
    }
  });

  document.querySelectorAll('#zpt-guide-top10 .zpt-guide-faq').forEach(function (row) {
    bindVote(row, row.getAttribute('data-faq-id'));
  });

  if (searchEl) {
    searchEl.addEventListener('input', function () {
      searchFaq(searchEl.value);
    });
  }
  if (backEl) {
    backEl.addEventListener('click', function () {
      if (searchEl) searchEl.value = '';
      document.querySelectorAll('[data-topic]').forEach(function (btn) {
        btn.classList.remove('is-active');
      });
      showHome();
    });
  }

  function lastUserQuestion() {
    if (window.ZPTGuideAssistant && window.ZPTGuideAssistant.lastUserQuestion) {
      return window.ZPTGuideAssistant.lastUserQuestion();
    }
    return '';
  }

  function closeAssistant(updateHash) {
    if (window.ZPTGuideAssistant && window.ZPTGuideAssistant.stop) {
      window.ZPTGuideAssistant.stop();
    }
    if (dialog && dialog.open) dialog.close();
    setNav('faq');
    if (lastFocus && typeof lastFocus.focus === 'function') {
      lastFocus.focus();
    }
    lastFocus = null;
    if (updateHash !== false && (location.hash === '#pomoshchnik' || location.hash === '#assistant')) {
      history.pushState(null, '', '#spravka');
    }
  }

  function openAssistant() {
    lastFocus = document.activeElement;
    showPanel('faq');
    setNav('assistant');
    if (dialog && typeof dialog.showModal === 'function' && !dialog.open) {
      dialog.showModal();
    } else if (dialog) {
      dialog.setAttribute('open', '');
    }
    if (window.ZPTGuideAssistant && window.ZPTGuideAssistant.focusInput) {
      window.ZPTGuideAssistant.focusInput();
    }
    fitAssistantToViewport();
    if (location.hash !== '#pomoshchnik') {
      history.pushState(null, '', '#pomoshchnik');
    }
  }

  function applyHash(hash) {
    var value = String(hash || '').replace('#', '');
    if (value === 'svyaz' || value === 'feedback') {
      closeAssistant(false);
      showPanel('feedback');
      setNav('feedback');
      return;
    }
    if (value === 'pomoshchnik' || value === 'assistant') {
      openAssistant();
      return;
    }
    closeAssistant(false);
    showPanel('faq');
    if (value === 'na-ekran') {
      var install = document.getElementById('na-ekran');
      if (install) {
        var toggle = install.querySelector('[data-zpt-pwa-toggle]');
        var panel = install.querySelector('[data-zpt-pwa-panel]');
        if (panel) panel.hidden = false;
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
        install.scrollIntoView({ block: 'nearest' });
      }
    }
  }

  document.querySelectorAll('[data-guide-nav]').forEach(function (el) {
    el.addEventListener('click', function (event) {
      var target = el.getAttribute('data-guide-nav');
      if (target === 'assistant') {
        event.preventDefault();
        openAssistant();
        return;
      }
      if (target === 'feedback') {
        event.preventDefault();
        closeAssistant(false);
        showPanel('feedback');
        history.pushState(null, '', '#svyaz');
        return;
      }
      if (target === 'faq') {
        event.preventDefault();
        closeAssistant(false);
        showPanel('faq');
        history.pushState(null, '', '#spravka');
      }
    });
  });

  if (closeBtn) closeBtn.addEventListener('click', function () { closeAssistant(); });
  if (dialog) {
    dialog.addEventListener('cancel', function (event) {
      event.preventDefault();
      closeAssistant();
    });
    dialog.addEventListener('close', function () {
      if (window.ZPTGuideAssistant && window.ZPTGuideAssistant.stop) {
        window.ZPTGuideAssistant.stop();
      }
    });
  }

  var handoffEl = document.getElementById('help-handoff');
  if (handoffEl) {
    handoffEl.addEventListener('click', function () {
      var question = lastUserQuestion();
      var messageEl = form && form.querySelector('[name="message"]');
      closeAssistant(false);
      showPanel('feedback');
      history.pushState(null, '', '#svyaz');
      if (messageEl && question) messageEl.value = question;
      if (messageEl) messageEl.focus();
    });
  }

  if (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      if (successEl) {
        successEl.hidden = true;
        successEl.textContent = '';
      }
      if (errorEl) {
        errorEl.hidden = true;
        errorEl.textContent = '';
      }
      var submit = form.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrfToken(),
          'X-Requested-With': 'XMLHttpRequest',
        },
      }).then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        }).catch(function () {
          return { ok: false, data: {} };
        });
      }).then(function (result) {
        if (result.ok && result.data && result.data.success) {
          form.reset();
          if (successEl) {
            successEl.hidden = false;
            successEl.textContent = result.data.message || 'Обращение принято.';
          }
          return;
        }
        if (errorEl) {
          errorEl.hidden = false;
          errorEl.textContent = (result.data && result.data.message) ||
            'Не удалось отправить обращение. Проверьте поля и попробуйте ещё раз.';
        }
      }).catch(function () {
        if (errorEl) {
          errorEl.hidden = false;
          errorEl.textContent = 'Не удалось отправить обращение. Проверьте соединение.';
        }
      }).then(function () {
        if (submit) submit.disabled = false;
      });
    });
  }

  function fitAssistantToViewport() {
    if (!dialog) return;
    if (!dialog.open) {
      dialog.style.height = '';
      dialog.style.maxHeight = '';
      dialog.style.top = '';
      return;
    }
    var vv = window.visualViewport;
    if (!vv || !window.matchMedia('(max-width: 720px)').matches) {
      dialog.style.height = '';
      dialog.style.maxHeight = '';
      dialog.style.top = '';
      return;
    }
    var height = Math.max(240, Math.round(vv.height));
    dialog.style.height = height + 'px';
    dialog.style.maxHeight = height + 'px';
    dialog.style.top = Math.round(vv.offsetTop || 0) + 'px';
  }

  window.addEventListener('hashchange', function () {
    hashFromUser = true;
    applyHash(location.hash);
  });
  window.addEventListener('popstate', function () {
    applyHash(location.hash);
  });
  applyHash(location.hash || '#spravka');

  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', fitAssistantToViewport);
    window.visualViewport.addEventListener('scroll', fitAssistantToViewport);
  }
  window.addEventListener('resize', fitAssistantToViewport);
  if (dialog) {
    dialog.addEventListener('close', fitAssistantToViewport);
  }
})();
