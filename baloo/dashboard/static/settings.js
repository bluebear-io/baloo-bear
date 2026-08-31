/* Settings page: search filter, dirty tracking, save bar.
   ponytail: plain DOM, no framework. The page is a form; this only decides
   what to submit and when to show the save bar. */
(function () {
  var form = document.getElementById("settings-form");
  if (!form) return;

  var bar = document.getElementById("save-bar");
  var count = document.getElementById("save-count");
  var search = document.getElementById("settings-search");
  var rows = Array.prototype.slice.call(document.querySelectorAll(".setting-row"));

  function valueFor(keyEl) {
    return keyEl.parentNode.querySelector('[name="value"]');
  }

  /* Toggles post through a hidden input so the value pairs stay aligned with
     their keys — an unchecked checkbox submits nothing at all. */
  function syncToggle(box) {
    var control = box.closest(".setting-control");
    var hidden = control.querySelector('input[name="value"]');
    var label = control.querySelector(".toggle-label");
    hidden.value = box.checked ? "true" : "false";
    if (label) label.textContent = box.checked ? "Enabled" : "Disabled";
  }

  var toggles = Array.prototype.slice.call(form.querySelectorAll('input[data-bool="1"]'));
  toggles.forEach(function (box) {
    box.addEventListener("change", function () {
      syncToggle(box);
      refresh();
    });
  });

  function snapshot() {
    var state = {};
    form.querySelectorAll('[name="key"]').forEach(function (keyEl) {
      var valueEl = valueFor(keyEl);
      if (valueEl) state[keyEl.value] = valueEl.value;
    });
    return state;
  }

  var initial = snapshot();

  function dirtyKeys() {
    var now = snapshot();
    return Object.keys(now).filter(function (k) {
      return now[k] !== initial[k];
    });
  }

  function refresh() {
    var n = dirtyKeys().length;
    count.textContent = n + (n === 1 ? " change" : " changes");
    bar.classList.toggle("is-visible", n > 0);
  }

  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);

  document.getElementById("discard").addEventListener("click", function () {
    form.reset();
    toggles.forEach(syncToggle);
    refresh();
  });

  /* Submit only what changed, so an unrelated field is never rewritten. */
  form.addEventListener("submit", function () {
    var changed = dirtyKeys();
    form.querySelectorAll('[name="key"]').forEach(function (keyEl) {
      if (changed.indexOf(keyEl.value) === -1) {
        var valueEl = valueFor(keyEl);
        keyEl.disabled = true;
        if (valueEl) valueEl.disabled = true;
      }
    });
  });

  /* Filtering. Advanced settings are hidden until asked for, but a search
     always looks through everything — otherwise the box appears broken when
     it can't find a setting the user knows exists. A category card whose rows
     are all hidden is hidden too, so filtering never leaves empty cards. */
  var showAdvanced = document.getElementById("show-advanced");
  var advancedCount = document.getElementById("advanced-count");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".settings-layout .card"));

  if (advancedCount) {
    advancedCount.textContent = rows.filter(function (row) {
      return row.getAttribute("data-tier") === "advanced";
    }).length;
  }

  function applyFilter() {
    var q = search ? search.value.trim().toLowerCase() : "";
    var searching = q !== "";
    var wantAdvanced = searching || (showAdvanced && showAdvanced.checked);

    rows.forEach(function (row) {
      var matches = !searching || row.getAttribute("data-search").indexOf(q) !== -1;
      var tierOk = wantAdvanced || row.getAttribute("data-tier") !== "advanced";
      row.hidden = !(matches && tierOk);
    });

    cards.forEach(function (card) {
      var visible = card.querySelectorAll(".setting-row:not([hidden])").length;
      // Cards without setting rows (e.g. "Models in use") are left alone.
      card.hidden = card.querySelectorAll(".setting-row").length > 0 && visible === 0;
    });
  }

  if (search) search.addEventListener("input", applyFilter);
  if (showAdvanced) showAdvanced.addEventListener("change", applyFilter);

  applyFilter();
  refresh();
})();
