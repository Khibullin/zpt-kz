(function (global) {
  'use strict';

  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function setText(el, text) {
    if (!el) {
      return;
    }
    el.textContent = text ?? '';
  }

  function clearElement(el) {
    if (!el) {
      return;
    }
    el.replaceChildren();
  }

  function appendOption(select, value, label) {
    const option = document.createElement('option');
    option.value = String(value ?? '');
    option.textContent = String(label ?? '');
    select.appendChild(option);
  }

  function fillSelect(select, items, placeholder, valueKey, labelKey) {
    if (!select) {
      return;
    }

    clearElement(select);

    if (placeholder !== undefined && placeholder !== null) {
      appendOption(select, '', placeholder);
    }

    (items || []).forEach(function (item) {
      appendOption(
        select,
        item[valueKey || 'id'],
        item[labelKey || 'name']
      );
    });
  }

  function fillSelectFromStrings(select, items, placeholder) {
    if (!select) {
      return;
    }

    clearElement(select);

    if (placeholder !== undefined && placeholder !== null) {
      appendOption(select, '', placeholder);
    }

    (items || []).forEach(function (item) {
      appendOption(select, item, item);
    });
  }

  function renderCheckboxLabels(container, items, options) {
    if (!container) {
      return;
    }

    const settings = options || {};
    const name = settings.name || 'selected_models';
    const excludeId = settings.excludeId;

    clearElement(container);

    (items || []).forEach(function (item) {
      if (
        excludeId !== undefined &&
        excludeId !== null &&
        String(item.id) === String(excludeId)
      ) {
        return;
      }

      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = String(item.id ?? '');

      if (settings.name) {
        input.name = settings.name;
      }

      if (settings.inputClass) {
        input.className = settings.inputClass;
      }

      label.appendChild(input);
      label.appendChild(
        document.createTextNode(' ' + String(item.name ?? ''))
      );
      container.appendChild(label);
    });
  }

  global.ZPTDom = {
    escapeHtml: escapeHtml,
    setText: setText,
    clearElement: clearElement,
    appendOption: appendOption,
    fillSelect: fillSelect,
    fillSelectFromStrings: fillSelectFromStrings,
    renderCheckboxLabels: renderCheckboxLabels,
  };
})(window);
