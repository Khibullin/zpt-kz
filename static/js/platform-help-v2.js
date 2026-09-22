(function () {
  'use strict';

  var MAX_SECONDS = 60;
  var ASK_URL = '/api/platform-help/ask/';
  var TRANSCRIBE_URL = '/api/platform-help/transcribe/';
  var HISTORY_URL = '/api/platform-help/history/';
  var NEW_URL = '/api/platform-help/new-conversation/';
  var DRAFT_KEY = 'zptGuideRequestDraft';

  var messagesEl = document.getElementById('help-messages');
  var formEl = document.getElementById('help-form');
  var inputEl = document.getElementById('help-input');
  var statusEl = document.getElementById('help-status');
  var sendEl = document.getElementById('help-send');
  var micEl = document.getElementById('help-mic');
  var cancelEl = document.getElementById('help-mic-cancel');
  var newEl = document.getElementById('help-new-dialog');
  var goRequestEl = document.getElementById('help-go-request');

  var recorder = null;
  var mediaStream = null;
  var recordedChunks = [];
  var recordedMime = '';
  var recordTimer = null;
  var pendingInputMode = 'text';
  var busy = false;
  var cancelRequested = false;
  var requestDraft = null;

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    if (match) return decodeURIComponent(match[1]);
    var hidden = formEl && formEl.querySelector('[name=csrfmiddlewaretoken]');
    return hidden ? hidden.value : '';
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text || '';
  }

  function setBusy(next) {
    busy = next;
    if (sendEl) sendEl.disabled = next;
    if (newEl) newEl.disabled = next;
    var handoff = document.getElementById('help-handoff');
    if (handoff) handoff.disabled = next;
    if (micEl && micEl.getAttribute('data-unsupported') !== '1') {
      micEl.disabled = next && !(recorder && recorder.state === 'recording');
    }
  }

  function appendZptLinks(container, text) {
    var source = String(text || '');
    var re = /https:\/\/zpt\.kz\/[^\s<]+/g;
    var last = 0;
    var match;
    while ((match = re.exec(source)) !== null) {
      if (match.index > last) {
        container.appendChild(document.createTextNode(source.slice(last, match.index)));
      }
      var raw = match[0];
      var trimmed = raw.replace(/[),.;!?]+$/, '');
      if (trimmed.indexOf('https://zpt.kz/') === 0) {
        var link = document.createElement('a');
        link.href = trimmed;
        link.textContent = trimmed;
        link.rel = 'noopener noreferrer';
        container.appendChild(link);
        last = match.index + trimmed.length;
        re.lastIndex = last;
      } else {
        container.appendChild(document.createTextNode(raw));
        last = match.index + raw.length;
      }
    }
    if (last < source.length) {
      container.appendChild(document.createTextNode(source.slice(last)));
    }
  }

  function addBubble(role, text) {
    if (!messagesEl) return;
    var bubble = document.createElement('div');
    bubble.className = 'platform-help__bubble platform-help__bubble--' + role;
    bubble.setAttribute('data-help-role', role);
    appendZptLinks(bubble, text);
    messagesEl.appendChild(bubble);
    bubble.scrollIntoView({ block: 'end' });
  }

  function showHistory(items) {
    if (!messagesEl) return;
    messagesEl.replaceChildren();
    if (!items || !items.length) return;
    items.forEach(function (item) {
      var role = item.role === 'user' ? 'user' : 'assistant';
      addBubble(role, item.content || '');
    });
  }

  function jsonHeaders() {
    return {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-CSRFToken': csrfToken(),
    };
  }

  function readError(data, fallback) {
    if (data && data.message) return data.message;
    return fallback;
  }

  function showDraftButton(draft) {
    requestDraft = sanitizeRequestDraft(draft);
    if (!goRequestEl) return;
    goRequestEl.hidden = !requestDraft;
  }

  function sanitizeRequestDraft(draft) {
    if (!draft || typeof draft !== 'object') return null;
    var query = String(draft.query || '').trim();
    if (!query) return null;
    return {
      query: query,
      brand: String(draft.brand || '').trim(),
      model: String(draft.model || '').trim(),
      year: String(draft.year || '').trim(),
      vin: String(draft.vin || '').trim(),
    };
  }

  function loadHistory() {
    return fetch(HISTORY_URL, {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.ok) {
          showHistory([]);
          return;
        }
        showHistory(data.messages || []);
      });
    }).catch(function () {
      showHistory([]);
    });
  }

  function sendQuestion(text, inputMode) {
    var question = String(text || '').trim();
    if (!question || busy) return;
    setBusy(true);
    setStatus('Ищу ответ…');
    addBubble('user', question);
    if (inputEl) inputEl.value = '';
    pendingInputMode = 'text';
    fetch(ASK_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({
        message: question,
        input_mode: inputMode || 'text',
      }),
    }).then(function (response) {
      return response.text().then(function (raw) {
        var data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch (err) { data = {}; }
        if (response.status === 429) {
          throw new Error(readError(data, 'Слишком много запросов. Попробуйте немного позже.'));
        }
        if (!response.ok || !data.ok) {
          throw new Error(readError(data, 'Сейчас не удалось получить ответ. Попробуйте ещё раз через минуту или напишите вопрос текстом.'));
        }
        addBubble('assistant', data.answer || '');
        if (data.request_draft) showDraftButton(data.request_draft);
        setStatus('');
      });
    }).catch(function (err) {
      var message = err && err.message ? err.message : 'Ошибка сети. Проверьте соединение и попробуйте ещё раз.';
      if (document.getElementById('help-handoff')) {
        message += ' Чтобы передать вопрос человеку, нажмите «Написать специалисту».';
      }
      setStatus(message);
    }).then(function () {
      setBusy(false);
      if (inputEl) inputEl.focus();
    });
  }

  function pickMimeType() {
    var types = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
      'audio/ogg;codecs=opus',
    ];
    if (!window.MediaRecorder || typeof MediaRecorder.isTypeSupported !== 'function') {
      return '';
    }
    for (var i = 0; i < types.length; i += 1) {
      if (MediaRecorder.isTypeSupported(types[i])) return types[i];
    }
    return '';
  }

  function extensionForMime(mime) {
    if (mime.indexOf('mp4') !== -1) return 'mp4';
    if (mime.indexOf('ogg') !== -1) return 'ogg';
    return 'webm';
  }

  function stopTracks() {
    if (!mediaStream) return;
    mediaStream.getTracks().forEach(function (track) { track.stop(); });
    mediaStream = null;
  }

  function resetMicButton() {
    if (!micEl) return;
    micEl.classList.remove('is-recording');
    micEl.setAttribute('aria-pressed', 'false');
    micEl.setAttribute('aria-label', 'Записать вопрос голосом');
    if (cancelEl) cancelEl.hidden = true;
  }

  function abortRecording(transcribe) {
    cancelRequested = !transcribe;
    if (recordTimer) {
      clearTimeout(recordTimer);
      recordTimer = null;
    }
    if (recorder && recorder.state === 'recording') {
      try { recorder.stop(); } catch (err) { stopTracks(); }
      return;
    }
    stopTracks();
    recorder = null;
    resetMicButton();
  }

  function transcribeBlob(blob, mime) {
    setStatus('Запись завершена. Распознаю ваш вопрос…');
    var form = new FormData();
    form.append('audio', blob, 'help-audio.' + extensionForMime(mime));
    fetch(TRANSCRIBE_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken() },
      body: form,
    }).then(function (response) {
      return response.text().then(function (raw) {
        var data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch (err) { data = {}; }
        if (response.status === 429) {
          throw new Error(readError(data, 'Слишком много запросов. Попробуйте немного позже.'));
        }
        if (!response.ok || !data.ok || !data.text) {
          throw new Error(readError(data, 'Не удалось распознать голос. Попробуйте ещё раз или напишите вопрос текстом.'));
        }
        inputEl.value = data.text;
        pendingInputMode = 'voice';
        setStatus('Готово. Проверьте текст и нажмите «Отправить».');
        inputEl.focus();
      });
    }).catch(function (err) {
      setStatus(err && err.message ? err.message : 'Не удалось распознать голос. Попробуйте ещё раз или напишите вопрос текстом.');
    }).then(function () {
      setBusy(false);
      resetMicButton();
    });
  }

  function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      resetMicButton();
      setStatus('Голосовой ввод недоступен на этом устройстве. Напишите вопрос текстом.');
      return;
    }
    setBusy(true);
    cancelRequested = false;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      mediaStream = stream;
      recordedChunks = [];
      recordedMime = pickMimeType();
      try {
        recorder = recordedMime
          ? new MediaRecorder(stream, { mimeType: recordedMime })
          : new MediaRecorder(stream);
      } catch (err) {
        stopTracks();
        throw err;
      }
      recordedMime = recorder.mimeType || recordedMime || 'audio/webm';
      recorder.addEventListener('dataavailable', function (event) {
        if (event.data && event.data.size) recordedChunks.push(event.data);
      });
      recorder.addEventListener('stop', function () {
        stopTracks();
        var blob = new Blob(recordedChunks, { type: recordedMime || 'audio/webm' });
        recorder = null;
        if (cancelRequested) {
          cancelRequested = false;
          setBusy(false);
          resetMicButton();
          setStatus('Запись отменена.');
          return;
        }
        if (!blob.size) {
          setBusy(false);
          resetMicButton();
          setStatus('Запись пустая. Попробуйте ещё раз или напишите вопрос текстом.');
          return;
        }
        transcribeBlob(blob, recordedMime);
      });
      recorder.start();
      micEl.classList.add('is-recording');
      micEl.setAttribute('aria-pressed', 'true');
      micEl.setAttribute('aria-label', 'Остановить запись');
      micEl.disabled = false;
      if (cancelEl) cancelEl.hidden = false;
      setStatus('Идёт запись. Нажмите микрофон, чтобы остановить, или «Отмена».');
      recordTimer = setTimeout(function () { abortRecording(true); }, MAX_SECONDS * 1000);
    }).catch(function (err) {
      setBusy(false);
      resetMicButton();
      var name = err && err.name ? err.name : '';
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        setStatus('Нет доступа к микрофону. Разрешите микрофон или напишите вопрос текстом.');
        return;
      }
      if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        setStatus('Микрофон не найден. Напишите вопрос текстом.');
        return;
      }
      setStatus('Голосовой ввод недоступен на этом устройстве. Напишите вопрос текстом.');
    });
  }

  if (formEl) {
    formEl.addEventListener('submit', function (event) {
      event.preventDefault();
      sendQuestion(inputEl.value, pendingInputMode);
    });
  }

  if (newEl) {
    newEl.addEventListener('click', function () {
      if (busy) return;
      setBusy(true);
      fetch(NEW_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: jsonHeaders(),
        body: '{}',
      }).then(function (response) {
        if (!response.ok) throw new Error('Не удалось начать новый диалог.');
        pendingInputMode = 'text';
        if (inputEl) inputEl.value = '';
        showHistory([]);
        showDraftButton(null);
        setStatus('');
      }).catch(function (err) {
        setStatus(err && err.message ? err.message : 'Не удалось начать новый диалог.');
      }).then(function () {
        setBusy(false);
      });
    });
  }

  if (goRequestEl) {
    goRequestEl.addEventListener('click', function () {
      if (!requestDraft) return;
      try {
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify(requestDraft));
      } catch (err) { /* ignore */ }
      window.location.href = '/';
    });
  }

  if (micEl) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      micEl.setAttribute('data-unsupported', '1');
      micEl.addEventListener('click', function () {
        setStatus('Голосовой ввод недоступен на этом устройстве. Напишите вопрос текстом.');
      });
    } else {
      micEl.addEventListener('click', function () {
        if (recorder && recorder.state === 'recording') {
          abortRecording(true);
          return;
        }
        if (busy) return;
        startRecording();
      });
    }
  }

  if (cancelEl) {
    cancelEl.addEventListener('click', function () {
      abortRecording(false);
    });
  }

  window.ZPTGuideAssistant = {
    loadHistory: loadHistory,
    lastUserQuestion: function () {
      var bubbles = document.querySelectorAll('[data-help-role="user"]');
      if (!bubbles.length) return '';
      return (bubbles[bubbles.length - 1].textContent || '').trim();
    },
    stop: function () {
      abortRecording(false);
    },
    focusInput: function () {
      if (inputEl) inputEl.focus();
    },
  };

  loadHistory();
})();
