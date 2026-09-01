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

  function renderPreview(container, data) {
    const fields = data.fields || {};
    const models = [fields.car_model_name]
      .concat((fields.selected_models || []).map(function (item) { return item.name; }))
      .filter(Boolean);
    const unmatched = data.unmatched || [];
    const sources = data.sources || [];
    let html = '';

    html += fieldLine('Название', fields.title);
    html += fieldLine('Категория', fields.category_name);
    html += fieldLine('Марка', fields.brand_name);
    html += fieldLine('Модели', models.join(', '));
    html += fieldLine('Совместимость', fields.compatibility);
    html += fieldLine('Двигатели', fields.engine_compatibility);
    html += fieldLine('OEM / кросс-номера', fields.oem_cross_references);
    html += fieldLine('Описание', fields.description);
    html += fieldLine('Уверенность', confidenceLabel(data.confidence));

    if (data.ai_error) {
      html += '<p class="field-hint">' + ZPTDom.escapeHtml(data.ai_error) + '</p>';
    }
    if (data.message) {
      html += '<p class="field-hint">' + ZPTDom.escapeHtml(data.message) + '</p>';
    }
    if (unmatched.length) {
      html += '<div class="product-assistant-unmatched"><strong>Не удалось сопоставить со справочником</strong><ul>';
      unmatched.forEach(function (item) {
        html += '<li>' + ZPTDom.escapeHtml(item) + '</li>';
      });
      html += '</ul></div>';
    }
    if (sources.length) {
      html += '<div class="product-assistant-sources"><strong>Источники</strong><ul>';
      sources.forEach(function (item) {
        const url = item.url || '';
        const title = item.title || url;
        if (!url) {
          return;
        }
        html +=
          '<li><a href="' +
          ZPTDom.escapeHtml(url) +
          '" target="_blank" rel="noopener noreferrer">' +
          ZPTDom.escapeHtml(title) +
          '</a></li>';
      });
      html += '</ul></div>';
    }

    container.innerHTML = html || '<p class="field-hint">Данных пока нет.</p>';
  }

  function fetchJson(url) {
    return fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    }).then(parseJsonResponse);
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

    const countryId = fields.country_id ? String(fields.country_id) : '';
    const brandId = fields.brand_id ? String(fields.brand_id) : '';
    const modelId = fields.car_model_id ? String(fields.car_model_id) : '';

    if (!countrySelect || !brandSelect || !modelSelect) {
      return Promise.resolve();
    }
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
        ZPTDom.renderCheckboxLabels(compatibleBox, pair[1], {
          name: 'selected_models',
          excludeId: modelSelect.value,
        });
        setChecked(extraIds);
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
            hidePreview();
            setHint(status, 'Данные подставлены. Проверьте карточку и сохраните.', false);
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
    const selectedBox = document.getElementById('js-photo-selected');
    const selectedImg = document.getElementById('js-photo-selected-img');
    const searchBox = document.getElementById('js-photo-search');
    const grid = document.getElementById('js-photo-search-grid');
    const status = document.getElementById('js-photo-search-status');
    const searchUrl = root.getAttribute('data-image-search-url');

    function clearRemoteSelection() {
      if (tokenInput) {
        tokenInput.value = '';
      }
      if (selectedBox) {
        selectedBox.hidden = true;
      }
      if (selectedImg) {
        selectedImg.removeAttribute('src');
      }
    }

    function clearFile() {
      if (fileInput) {
        fileInput.value = '';
      }
    }

    function showSelected(src) {
      if (!selectedBox || !selectedImg || !src) {
        return;
      }
      selectedImg.src = src;
      selectedBox.hidden = false;
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
          const url = URL.createObjectURL(fileInput.files[0]);
          showSelected(url);
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
        setHint(status, 'Карточка будет без нового фото.', false);
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
              choose.textContent = 'Выбрать фото';
              choose.addEventListener('click', function () {
                clearFile();
                if (tokenInput) {
                  tokenInput.value = item.token || '';
                }
                showSelected(item.thumbnail_url);
                setHint(status, 'Фото выбрано. Оно сохранится вместе с карточкой.', false);
              });
              card.appendChild(choose);
              grid.appendChild(card);
            });
            if (searchBox) {
              searchBox.hidden = false;
            }
            setHint(status, data.warning || 'Проверьте, что на фото изображён именно ваш товар.', false);
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
