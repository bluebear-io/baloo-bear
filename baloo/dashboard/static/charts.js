/* Chart.js <-> design token bridge.
   Charts never name a color; they ask for a token. That is what keeps the
   theme toggle working without a second palette. */
(function () {
  var registry = [];

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function palette() {
    return ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"].map(token);
  }

  function severityColor(name) {
    var key = String(name || "").toLowerCase();
    var map = {
      critical: "--sev-critical",
      high: "--sev-high",
      medium: "--sev-medium",
      low: "--sev-low"
    };
    return token(map[key] || "--text-muted");
  }

  function applyDefaults() {
    if (!window.Chart) return;
    Chart.defaults.color = token("--text-muted");
    Chart.defaults.borderColor = token("--border");
    Chart.defaults.font.family = token("--font");
    Chart.defaults.font.size = 12;
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
  }

  /* A chart declares how its colors are derived, so retheme can recompute
     them. `colorize(config, api)` runs at build time and on every toggle. */
  function make(canvasId, config, colorize) {
    var el = document.getElementById(canvasId);
    if (!el || !window.Chart) return null;
    applyDefaults();
    var api = { token: token, palette: palette, severityColor: severityColor };
    if (colorize) colorize(config, api);
    var chart = new Chart(el, config);
    registry.push({ chart: chart, colorize: colorize, api: api });
    return chart;
  }

  function retheme() {
    applyDefaults();
    registry.forEach(function (entry) {
      if (entry.colorize) entry.colorize(entry.chart.config, entry.api);
      entry.chart.update("none");
    });
  }

  window.BalooCharts = {
    token: token,
    get PALETTE() { return palette(); },
    severityColor: severityColor,
    make: make,
    retheme: retheme
  };
})();
