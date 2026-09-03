(function () {
  'use strict';

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) {
      return meta.content;
    }
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) {
      return input.value;
    }
    return getCookie('csrftoken');
  }

  function parseJsonResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      return response.text().then(function (body) {
        const snippet = (body || '').replace(/\s+/g, ' ').slice(0, 120);
        throw new Error(
          'Сервер вернул не JSON (код ' + response.status + '): ' + snippet
        );
      });
    }
    return response.json();
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        Accept: 'application/json',
      },
      credentials: 'same-origin',
      body: JSON.stringify(payload || {}),
    }).then(parseJsonResponse);
  }

  function setHint(el, text, isError) {
    if (!el) {
      return;
    }
    if (!text) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle('field-hint-error', Boolean(isError));
  }

  function selectedOptionText(select) {
    if (!select || !select.options || select.selectedIndex < 0) {
      return '';
    }
    return (select.options[select.selectedIndex].textContent || '').trim();
  }

  function fieldLine(label, value) {
    const text = String(value || '').trim();
    if (!text) {
      return '';
    }
    return (
      '<div class="product-assistant-row"><span>' +
      ZPTDom.escapeHtml(label) +
      '</span><div>' +
      ZPTDom.escapeHtml(text) +
      '</div></div>'
    );
  }

  function confidenceLabel(value) {
    if (value === 'confirmed') {
      return 'подтверждено';
    }
    if (value === 'likely') {
      return 'вероятно';
    }
    return 'нужна проверка';
  }

  function isModelCatalogNote(text) {
    const value = String(text || '');
    return /не найдена в справочнике CarModel|относится к марке|Подходит для/i.test(value);
  }

  function renderPreview(container, data) {
    const fields = data.fields || {};
    const models = [fields.car_model_name]
      .concat((fields.selected_models || []).map(function (item) { return item.name; }))
      .filter(Boolean);
    const unmatched = data.unmatched || [];
    const sources = data.sources || [];
    const notes = data.research_notes || [];
    let html = '';
    let review = '';
    let modelNotes = '';

    html += '<div class="product-assistant-public">';
    html += fieldLine('Название', fields.title);
    html += fieldLine('Категория', fields.category_name);
    html += fieldLine('Марка', fields.brand_name);
    html += fieldLine('Модели', models.join(', '));
    html += fieldLine('Подходит для', fields.compatibility);
    html += fieldLine('Двигатели', fields.engine_compatibility);
    html += fieldLine('OEM / кросс-номера', fields.oem_cross_references);
    html += fieldLine('Описание', fields.description);
    html += fieldLine('Уверенность', confidenceLabel(data.confidence));
    html += '</div>';

    if (data.ai_error) {
      html += '<p class="field-hint">' + ZPTDom.escapeHtml(data.ai_error) + '</p>';
    }
    if (data.message) {
      html += '<p class="field-hint">' + ZPTDom.escapeHtml(data.message) + '</p>';
    }

    notes.forEach(function (item) {
      const text = (item && item.text) ? item.text : String(item || '');
      const severity = (item && item.severity) ? String(item.severity) : '';
      if (!text) {
        return;
      }
      if (severity === 'info' || isModelCatalogNote(text)) {
        modelNotes += '<li>' + ZPTDom.escapeHtml(text) + '</li>';
      } else {
        review += '<li>' + ZPTDom.escapeHtml(text) + '</li>';
      }
    });
    unmatched.forEach(function (item) {
      const text = String(item || '');
      if (!text) {
        return;
      }
      if (isModelCatalogNote(text)) {
        modelNotes += '<li>' + ZPTDom.escapeHtml(text) + '</li>';
      } else {
        review += '<li>' + ZPTDom.escapeHtml(text) + '</li>';
      }
    });
    sources.forEach(function (item) {
      const url = item.url || '';
      const title = item.title || url;
      if (!url) {
        return;
      }
      review +=
        '<li><a href="' +
        ZPTDom.escapeHtml(url) +
        '" target="_blank" rel="noopener noreferrer">' +
        ZPTDom.escapeHtml(title) +
        '</a></li>';
    });
    if (review) {
      html +=
        '<div class="product-assistant-review"><strong>Требует проверки</strong><ul>' +
        review +
        '</ul></div>';
    }
    if (modelNotes) {
      html +=
        '<div class="product-assistant-review"><strong>Модели справочника</strong><ul>' +
        modelNotes +
        '</ul></div>';
    }

    container.innerHTML = html || '<p class="field-hint">Данных пока нет.</p>';
  }

  function fetchJson(url) {
    return fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    }).then(parseJsonResponse);
  }

  function escapeRegExp(value) {
    return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function mentionIndex(blob, name) {
    const text = String(name || '').trim();
    if (!text) {
      return -1;
    }
    const re = new RegExp(
      '(^|[^0-9A-Za-zА-Яа-яЁё])' + escapeRegExp(text) + '(?=[^0-9A-Za-zА-Яа-яЁё]|$)',
      'i'
    );
    const match = blob.match(re);
    return match ? blob.search(re) : -1;
  }

  function firstMentioned(items, blob, getName) {
    let best = null;
    let bestPos = Infinity;
    (items || []).forEach(function (item) {
      const idx = mentionIndex(blob, getName(item));
      if (idx >= 0 && idx < bestPos) {
        best = item;
        bestPos = idx;
      }
    });
    return best;
  }

  function vehicleBlob(fields) {
    return [
      fields.brand_name,
      fields.title,
      fields.compatibility,
      fields.car_model_name,
    ]
      .concat((fields.selected_models || []).map(function (item) { return item && item.name; }))
      .filter(Boolean)
      .join(' \n ');
  }

  function applyVehicle(fields, urls) {
    const countrySelect = document.getElementById('id_country');
    const brandSelect = document.getElementById('id_brand');
    const modelSelect = document.getElementById('id_car_model');
    const compatibleBox = document.getElementById('id_selected_models');
    const extraIds = (fields.selected_models || []).map(function (item) {
      return String(item.id);
    });

    function setChecked(ids) {
      if (!compatibleBox) {
        return;
      }
      compatibleBox.querySelectorAll('input[type="checkbox"]').forEach(function (input) {
        input.checked = ids.indexOf(String(input.value)) !== -1;
      });
    }

    function fillVehicle(countryId, brandId, modelId, extra) {
      if (!countryId) {
        return Promise.resolve();
      }
      countrySelect.value = countryId;
      return fetchJson(urls.brands + '?country=' + encodeURIComponent(countryId))
        .then(function (brands) {
          ZPTDom.fillSelect(brandSelect, brands, 'Выберите марку');
          if (brandId) {
            brandSelect.value = brandId;
          }
          if (!brandSelect.value) {
            ZPTDom.fillSelect(modelSelect, [], '---------');
            ZPTDom.clearElement(compatibleBox);
            return null;
          }
          return Promise.all([
            fetchJson(urls.models + '?brand=' + encodeURIComponent(brandSelect.value)),
            fetchJson(urls.compatible + '?brand=' + encodeURIComponent(brandSelect.value)),
          ]);
        })
        .then(function (pair) {
          if (!pair) {
            return;
          }
          ZPTDom.fillSelect(modelSelect, pair[0], 'Выберите модель');
          if (modelId) {
            modelSelect.value = modelId;
          }
          if (!modelSelect.value && modelSelect.options) {
            const blob = vehicleBlob(fields);
            const catalogModels = pair[0] || [];
            const hinted = firstMentioned(catalogModels, blob, function (item) { return item.name; });
            if (hinted) {
              modelSelect.value = String(hinted.id);
            }
          }
          ZPTDom.renderCheckboxLabels(compatibleBox, pair[1], {
            name: 'selected_models',
            excludeId: modelSelect.value,
          });
          let ids = extra.slice();
          if (!ids.length) {
            const blob = vehicleBlob(fields);
            (pair[1] || []).forEach(function (item) {
              if (String(item.id) === String(modelSelect.value)) {
                return;
              }
              if (mentionIndex(blob, item.name) >= 0) {
                ids.push(String(item.id));
              }
            });
          }
          setChecked(ids);
        });
    }

    if (!countrySelect || !brandSelect || !modelSelect) {
      return Promise.resolve();
    }

    const countryId = fields.country_id ? String(fields.country_id) : '';
    const brandId = fields.brand_id ? String(fields.brand_id) : '';
    const modelId = fields.car_model_id ? String(fields.car_model_id) : '';
    if (countryId && brandId) {
      return fillVehicle(countryId, brandId, modelId, extraIds);
    }

    const blob = vehicleBlob(fields);
    if (!blob.trim()) {
      if (countryId) {
        return fillVehicle(countryId, brandId, modelId, extraIds);
      }
      return Promise.resolve();
    }

    const countryIds = Array.prototype.map.call(countrySelect.options, function (option) {
      return option.value;
    }).filter(Boolean);
    const searches = countryIds.map(function (id) {
      return fetchJson(urls.brands + '?country=' + encodeURIComponent(id)).then(function (brands) {
        return { countryId: id, brands: brands || [] };
      });
    });
    return Promise.all(searches).then(function (groups) {
      let resolved = null;
      let bestPos = Infinity;
      groups.forEach(function (group) {
        const brand = firstMentioned(group.brands, blob, function (item) { return item.name; });
        if (!brand) {
          return;
        }
        const pos = mentionIndex(blob, brand.name);
        if (pos >= 0 && pos < bestPos) {
          bestPos = pos;
          resolved = { countryId: group.countryId, brandId: String(brand.id) };
        }
      });
      if (!resolved) {
        if (countryId) {
          return fillVehicle(countryId, brandId, modelId, extraIds);
        }
        return null;
      }
      return fillVehicle(resolved.countryId, resolved.brandId, modelId, extraIds);
    });
  }

  function applyFields(fields, urls) {
    const mapping = [
      ['id_title', fields.title],
      ['id_compatibility', fields.compatibility],
      ['id_engine_compatibility', fields.engine_compatibility],
      ['id_oem_cross_references', fields.oem_cross_references],
      ['id_description', fields.description],
    ];
    mapping.forEach(function (item) {
      const el = document.getElementById(item[0]);
      if (el && item[1]) {
        el.value = item[1];
      }
    });
    const categorySelect = document.getElementById('id_category');
    if (categorySelect && fields.category_id) {
      categorySelect.value = String(fields.category_id);
    }
    return applyVehicle(fields, urls);
  }

  function bindAssistant(root) {
    const button = document.getElementById('js-product-assistant-btn');
    const preview = document.getElementById('js-product-assistant-preview');
    const previewBody = document.getElementById('js-product-assistant-preview-body');
    const applyBtn = document.getElementById('js-product-assistant-apply');
    const cancelBtn = document.getElementById('js-product-assistant-cancel');
    const status = document.getElementById('js-product-assistant-status');
    const articleInput = document.getElementById('id_article');
    let lastPayload = null;

    const urls = {
      assistant: root.getAttribute('data-assistant-url'),
      brands: root.getAttribute('data-brands-url'),
      models: root.getAttribute('data-models-url'),
      compatible: root.getAttribute('data-compatible-url'),
    };

    function hidePreview() {
      lastPayload = null;
      if (preview) {
        preview.hidden = true;
      }
    }

    if (button) {
      button.addEventListener('click', function () {
        const article = articleInput ? articleInput.value.trim() : '';
        if (!article) {
          setHint(status, 'Сначала укажите артикул.', true);
          return;
        }
        button.disabled = true;
        setHint(status, 'Ищем данные по артикулу…', false);
        postJson(urls.assistant, { article: article })
          .then(function (data) {
            if (!data || !data.ok) {
              setHint(status, (data && data.error) || 'Не удалось подобрать данные.', true);
              hidePreview();
              return;
            }
            lastPayload = data;
            renderPreview(previewBody, data);
            preview.hidden = false;
            const extra = data.ai_error || data.message || 'Проверьте данные и нажмите «Применить данные».';
            setHint(status, extra, false);
          })
          .catch(function (error) {
            setHint(status, error.message || 'Не удалось подобрать данные.', true);
            hidePreview();
          })
          .then(function () {
            button.disabled = false;
          });
      });
    }

    if (cancelBtn) {
      cancelBtn.addEventListener('click', function () {
        hidePreview();
        setHint(status, '', false);
      });
    }

    if (applyBtn) {
      applyBtn.addEventListener('click', function () {
        if (!lastPayload || !lastPayload.fields) {
          return;
        }
        applyBtn.disabled = true;
        applyFields(lastPayload.fields, urls)
          .catch(function () {
            setHint(status, 'Часть полей не удалось подставить автоматически.', true);
          })
          .then(function () {
            applyBtn.disabled = false;
            const unmatched = (lastPayload && lastPayload.unmatched) || [];
            const hasModelGap = unmatched.some(isModelCatalogNote);
            hidePreview();
            const msg = hasModelGap
              ? 'Данные подставлены. Модели, которых нет в справочнике, сохранены в поле «Подходит для» и не мешают сохранению.'
              : 'Данные подставлены. Проверьте карточку и сохраните.';
            setHint(status, msg, false);
          });
      });
    }
  }

  function bindPhotos(root) {
    const uploadBtn = document.getElementById('js-photo-upload-btn');
    const searchBtn = document.getElementById('js-photo-search-btn');
    const skipBtn = document.getElementById('js-photo-skip-btn');
    const fileInput = document.getElementById('id_main_image');
    const tokenInput = document.getElementById('id_remote_main_image_token');
    const tokensInput = document.getElementById('id_remote_image_tokens');
    const selectedBox = document.getElementById('js-photo-selected');
    const selectedList = document.getElementById('js-photo-selected-list');
    const selectedCount = document.getElementById('js-photo-selected-count');
    const saveNote = document.getElementById('js-photo-save-note');
    const searchBox = document.getElementById('js-photo-search');
    const grid = document.getElementById('js-photo-search-grid');
    const status = document.getElementById('js-photo-search-status');
    const searchUrl = root.getAttribute('data-image-search-url');
    const hasExistingMain = root.getAttribute('data-has-existing-main') === '1';
    const MAX_REMOTE = 5;
    let selected = [];

    function syncHidden() {
      if (tokenInput) {
        const main = selected.find(function (item) { return item.isMain; });
        tokenInput.value = main ? main.token : '';
      }
      if (tokensInput) {
        tokensInput.value = JSON.stringify(selected.map(function (item) { return item.token; }));
      }
    }

    function selectedCountText() {
      return 'Выбрано ' + selected.length + ' из 5';
    }

    function markCards() {
      if (!grid) {
        return;
      }
      const tokens = selected.map(function (item) { return item.token; });
      grid.querySelectorAll('.product-photo-card').forEach(function (card) {
        const token = card.getAttribute('data-token') || '';
        const on = tokens.indexOf(token) !== -1;
        card.classList.toggle('is-selected', on);
        const badge = card.querySelector('.product-photo-card-badge');
        const button = card.querySelector('button[data-use-photo]');
        if (badge) {
          badge.hidden = !on;
        }
        if (button) {
          button.disabled = on;
          button.textContent = on ? 'Выбрано' : 'Использовать фото';
        }
      });
    }

    function renderSelected() {
      syncHidden();
      if (selectedCount) {
        selectedCount.textContent = selectedCountText();
      }
      if (saveNote) {
        saveNote.hidden = selected.length === 0;
      }
      if (selectedBox) {
        selectedBox.hidden = selected.length === 0;
      }
      if (!selectedList) {
        markCards();
        return;
      }
      ZPTDom.clearElement(selectedList);
      selected.forEach(function (item, index) {
        const card = document.createElement('div');
        card.className = 'product-photo-picked' + (item.isMain ? ' is-main' : '');

        const img = document.createElement('img');
        img.src = item.thumb || '';
        img.alt = item.isMain ? 'Главное фото' : 'Дополнительное фото';
        card.appendChild(img);

        const role = document.createElement('div');
        role.className = 'product-photo-picked-role';
        role.textContent = item.isMain ? 'Главное фото' : 'Дополнительное фото';
        card.appendChild(role);

        const actions = document.createElement('div');
        actions.className = 'product-photo-picked-actions';
        if (!item.isMain) {
          const makeMain = document.createElement('button');
          makeMain.type = 'button';
          makeMain.className = 'btn outline';
          makeMain.textContent = 'Сделать главным';
          makeMain.addEventListener('click', function () {
            selected.forEach(function (entry) { entry.isMain = false; });
            selected[index].isMain = true;
            renderSelected();
          });
          actions.appendChild(makeMain);
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn outline';
        remove.textContent = 'Убрать';
        remove.addEventListener('click', function () {
          const wasMain = selected[index].isMain;
          selected.splice(index, 1);
          if (wasMain && selected.length) {
            selected[0].isMain = true;
          }
          renderSelected();
        });
        actions.appendChild(remove);
        card.appendChild(actions);
        selectedList.appendChild(card);
      });
      markCards();
    }

    function clearRemoteSelection() {
      selected = [];
      renderSelected();
    }

    function clearFile() {
      if (fileInput) {
        fileInput.value = '';
      }
    }

    function addRemote(item) {
      if (!item || !item.token) {
        return;
      }
      if (selected.some(function (entry) { return entry.token === item.token; })) {
        return;
      }
      if (selected.length >= MAX_REMOTE) {
        setHint(status, 'Можно выбрать не больше 5 фото.', true);
        return;
      }
      clearFile();
      const makeMain = selected.length === 0 && !hasExistingMain;
      selected.push({
        token: item.token,
        thumb: item.thumbnail_url || '',
        title: item.title || '',
        isMain: makeMain,
      });
      renderSelected();
      setHint(
        status,
        'Фото выбрано. Оно сохранится только после «Сохранить товар».',
        false
      );
    }

    if (uploadBtn && fileInput) {
      uploadBtn.addEventListener('click', function () {
        clearRemoteSelection();
        if (searchBox) {
          searchBox.hidden = true;
        }
        fileInput.click();
      });
      fileInput.addEventListener('change', function () {
        if (fileInput.files && fileInput.files[0]) {
          clearRemoteSelection();
          setHint(status, 'Выбрано своё фото. Оно сохранится вместе с карточкой.', false);
        }
      });
    }

    if (skipBtn) {
      skipBtn.addEventListener('click', function () {
        clearFile();
        clearRemoteSelection();
        if (searchBox) {
          searchBox.hidden = true;
        }
        setHint(status, 'Карточка будет без нового фото. Текущие фото товара не удаляются.', false);
      });
    }

    if (searchBtn) {
      searchBtn.addEventListener('click', function () {
        const articleInput = document.getElementById('id_article');
        const titleInput = document.getElementById('id_title');
        const brandSelect = document.getElementById('id_brand');
        const article = articleInput ? articleInput.value.trim() : '';
        if (!article) {
          setHint(status, 'Сначала укажите артикул.', true);
          return;
        }
        searchBtn.disabled = true;
        setHint(status, 'Ищем фото по артикулу…', false);
        postJson(searchUrl, {
          article: article,
          title: titleInput ? titleInput.value.trim() : '',
          brand: selectedOptionText(brandSelect),
        })
          .then(function (data) {
            ZPTDom.clearElement(grid);
            if (!data || !data.ok) {
              if (searchBox) {
                searchBox.hidden = true;
              }
              setHint(
                status,
                (data && data.error) || 'Не удалось найти фото. Загрузите своё.',
                true
              );
              return;
            }
            const images = data.images || [];
            if (!images.length) {
              if (searchBox) {
                searchBox.hidden = true;
              }
              setHint(status, 'Фото не найдены. Загрузите своё фото.', true);
              return;
            }
            images.forEach(function (item) {
              const card = document.createElement('div');
              card.className = 'product-photo-card';
              card.setAttribute('data-token', item.token || '');

              const badge = document.createElement('span');
              badge.className = 'product-photo-card-badge';
              badge.textContent = 'Выбрано';
              badge.hidden = true;
              card.appendChild(badge);

              const img = document.createElement('img');
              img.src = item.thumbnail_url || '';
              img.alt = item.title || 'Фото товара';
              card.appendChild(img);

              const meta = document.createElement('div');
              meta.className = 'product-photo-card-source';
              meta.textContent = item.source || item.title || 'Источник';
              card.appendChild(meta);

              if (item.source_url) {
                const openLink = document.createElement('a');
                openLink.href = item.source_url;
                openLink.target = '_blank';
                openLink.rel = 'noopener noreferrer';
                openLink.textContent = 'Открыть источник';
                card.appendChild(openLink);
              }

              const choose = document.createElement('button');
              choose.type = 'button';
              choose.className = 'btn outline';
              choose.setAttribute('data-use-photo', '1');
              choose.textContent = 'Использовать фото';
              choose.addEventListener('click', function () {
                addRemote(item);
              });
              card.appendChild(choose);
              grid.appendChild(card);
            });
            if (searchBox) {
              searchBox.hidden = false;
            }
            setHint(status, data.warning || 'Проверьте, что на фото изображён именно ваш товар.', false);
            markCards();
          })
          .catch(function (error) {
            setHint(status, error.message || 'Не удалось найти фото. Загрузите своё.', true);
          })
          .then(function () {
            searchBtn.disabled = false;
          });
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    const root = document.getElementById('product-assistant-root');
    if (!root) {
      return;
    }
    bindAssistant(root);
    bindPhotos(root);
  });
})();
