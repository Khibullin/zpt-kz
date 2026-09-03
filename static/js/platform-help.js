(function () {
  'use strict';

  var MAX_SECONDS = 60;
  var ASK_URL = '/api/platform-help/ask/';
  var TRANSCRIBE_URL = '/api/platform-help/transcribe/';
  var HISTORY_URL = '/api/platform-help/history/';
  var NEW_URL = '/api/platform-help/new-conversation/';
  var WELCOME =
    'Здравствуйте! Я помощник ZPT.KZ.\n' +
    'Могу подсказать по заявкам покупателей, размещению товаров,\n' +
    'оптовым предложениям, кабинету продавца и другим возможностям платформы.';

  var messagesEl = document.getElementById('help-messages');
  var chipsEl = document.getElementById('help-chips');
  var formEl = document.getElementById('help-form');
  var inputEl = document.getElementById('help-input');
  var statusEl = document.getElementById('help-status');
  var sendEl = document.getElementById('help-send');
  var micEl = document.getElementById('help-mic');
  var newEl = document.getElementById('help-new-dialog');

  var recorder = null;
  var recordedChunks = [];
  var recordedMime = '';
  var recordTimer = null;
  var pendingInputMode = 'text';
  var busy = false;

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    if (match) {
      return decodeURIComponent(match[1]);
    }
    var hidden = formEl && formEl.querySelector('[name=csrfmiddlewaretoken]');
    return hidden ? hidden.value : '';
  }

  function setStatus(text) {
    if (statusEl) {
      statusEl.textContent = text || '';
    }
  }

  function setBusy(next) {
    busy = next;
    if (sendEl) sendEl.disabled = next;
    if (micEl && micEl.getAttribute('data-unsupported') !== '1') {
      micEl.disabled = next && !(recorder && recorder.state === 'recording');
    }
    if (newEl) newEl.disabled = next;
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
    var bubble = document.createElement('div');
    bubble.className = 'platform-help__bubble platform-help__bubble--' + role;
    appendZptLinks(bubble, text);
    messagesEl.appendChild(bubble);
    bubble.scrollIntoView({ block: 'end' });
  }

  function showWelcome() {
    messagesEl.replaceChildren();
    addBubble('assistant', WELCOME);
    if (chipsEl) chipsEl.hidden = false;
  }

  function showHistory(items) {
    messagesEl.replaceChildren();
    if (!items || !items.length) {
      showWelcome();
      return;
    }
    if (chipsEl) chipsEl.hidden = true;
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

  function loadHistory() {
    return fetch(HISTORY_URL, {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.ok) {
          showWelcome();
          return;
        }
        showHistory(data.messages || []);
      });
    }).catch(function () {
      showWelcome();
    });
  }

  function sendQuestion(text, inputMode) {
    var question = String(text || '').trim();
    if (!question || busy) return;
    setBusy(true);
    setStatus('Отправляю вопрос…');
    if (chipsEl) chipsEl.hidden = true;
    addBubble('user', question);
    inputEl.value = '';
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
        setStatus('');
      });
    }).catch(function (err) {
      setStatus(err && err.message ? err.message : 'Ошибка сети. Проверьте соединение и попробуйте ещё раз.');
    }).then(function () {
      setBusy(false);
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

  function stopRecording() {
    if (recordTimer) {
      clearTimeout(recordTimer);
      recordTimer = null;
    }
    if (recorder && recorder.state === 'recording') {
      recorder.stop();
    }
  }

  function transcribeBlob(blob, mime) {
    setStatus('Распознаю речь…');
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
        setStatus('Текст распознан. Проверьте его и нажмите «Отправить».');
        inputEl.focus();
      });
    }).catch(function (err) {
      setStatus(err && err.message ? err.message : 'Не удалось распознать голос. Попробуйте ещё раз или напишите вопрос текстом.');
    }).then(function () {
      setBusy(false);
      micEl.classList.remove('is-recording');
      micEl.setAttribute('aria-pressed', 'false');
      micEl.textContent = 'Говорить';
    });
  }

  function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      setStatus('Голосовой ввод недоступен на этом устройстве. Напишите вопрос текстом.');
      return;
    }
    setBusy(true);
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      recordedChunks = [];
      recordedMime = pickMimeType();
      try {
        recorder = recordedMime
          ? new MediaRecorder(stream, { mimeType: recordedMime })
          : new MediaRecorder(stream);
      } catch (err) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        throw err;
      }
      recordedMime = recorder.mimeType || recordedMime || 'audio/webm';
      recorder.addEventListener('dataavailable', function (event) {
        if (event.data && event.data.size) recordedChunks.push(event.data);
      });
      recorder.addEventListener('stop', function () {
        stream.getTracks().forEach(function (track) { track.stop(); });
        var blob = new Blob(recordedChunks, { type: recordedMime || 'audio/webm' });
        recorder = null;
        transcribeBlob(blob, recordedMime);
      });
      recorder.start();
      micEl.classList.add('is-recording');
      micEl.setAttribute('aria-pressed', 'true');
      micEl.textContent = 'Стоп';
      micEl.disabled = false;
      setStatus('Идёт запись… Нажмите ещё раз, чтобы остановить.');
      recordTimer = setTimeout(stopRecording, MAX_SECONDS * 1000);
    }).catch(function (err) {
      setBusy(false);
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

  if (chipsEl) {
    chipsEl.addEventListener('click', function (event) {
      var button = event.target.closest('[data-help-example]');
      if (!button) return;
      sendQuestion(button.getAttribute('data-help-example'), 'text');
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
        inputEl.value = '';
        showWelcome();
        setStatus('');
      }).catch(function (err) {
        setStatus(err && err.message ? err.message : 'Не удалось начать новый диалог.');
      }).then(function () {
        setBusy(false);
      });
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
          stopRecording();
          return;
        }
        if (busy) return;
        startRecording();
      });
    }
  }

  loadHistory();
})();
