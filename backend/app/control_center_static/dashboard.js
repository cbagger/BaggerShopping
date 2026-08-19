(() => {
  "use strict";

  const PAGE_TITLES = {
    overview: "Dashboard",
    operations: "Drift & system",
    luna: "Luna & tilbudsaviser",
    integrations: "Integrationer",
    data: "Data & familie",
    architecture: "Arkitektur",
    activity: "Aktivitet",
  };

  const PAGE_ORDER = ["overview", "operations", "luna", "integrations", "data", "architecture", "activity"];
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let activePage = "overview";
  let latestSnapshot = null;
  let refreshTimer = null;

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const fmtInt = (value) => new Intl.NumberFormat("da-DK", { maximumFractionDigits: 0 }).format(number(value));
  const fmtDkk = (value, digits = 2) => new Intl.NumberFormat("da-DK", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(number(value));
  const fmtEventDateTime = (timestamp) => timestamp
    ? new Intl.DateTimeFormat("da-DK", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(Number(timestamp) * 1000))
    : "Dato ukendt";

  const fmtAge = (seconds) => {
    if (seconds === null || seconds === undefined) return "ukendt";
    const value = Math.max(0, Number(seconds));
    if (value < 60) return `${Math.round(value)} sek.`;
    if (value < 3600) return `${Math.round(value / 60)} min.`;
    if (value < 86400) return `${(value / 3600).toLocaleString("da-DK", { maximumFractionDigits: 1 })} t.`;
    return `${(value / 86400).toLocaleString("da-DK", { maximumFractionDigits: 1 })} dage`;
  };

  const fmtBytes = (bytes) => {
    if (bytes === null || bytes === undefined) return "—";
    let value = Number(bytes);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let unit = 0;
    while (Math.abs(value) >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toLocaleString("da-DK", { maximumFractionDigits: unit >= 2 ? 2 : 0 })} ${units[unit]}`;
  };

  const statusTone = (status) => {
    const value = String(status || "unknown");
    if (["healthy", "success", "none", "complete", "online", "running", "available"].includes(value)) return "healthy";
    if (["critical", "error", "stale", "unavailable", "detected"].includes(value)) return "error";
    if (["attention", "warning", "degraded", "pending"].includes(value)) return "attention";
    if (value === "cost") return "cost";
    return "info";
  };

  function ensureOperationsPage() {
    if ($("#operations")) return;
    const architecture = $("#architecture");
    if (!architecture) return;

    const section = document.createElement("section");
    section.id = "operations";
    section.className = "section-block section-anchor";
    section.innerHTML = `
      <div class="section-heading">
        <div>
          <span class="eyebrow">OPERATIONS</span>
          <h2>Drift & system</h2>
          <p>Runtime, end-to-end sundhed, dataliv, workers, alarmer, deploy og recovery — samlet uden at blande det ind i Dashboard.</p>
        </div>
      </div>
    `;
    architecture.parentNode.insertBefore(section, architecture);

    const cockpit = $("#operationsCockpit");
    if (cockpit) section.appendChild(cockpit);

    const currentWork = $("#overview .current-work-panel");
    if (currentWork) {
      currentWork.classList.add("operations-current-work");
      section.appendChild(currentWork);
    }

    const overviewGrid = $("#overview .overview-grid");
    const runtimePanel = overviewGrid ? $(".panel:not(.attention-panel)", overviewGrid) : null;
    if (runtimePanel) {
      runtimePanel.classList.add("runtime-panel");
      section.appendChild(runtimePanel);
    }
    if (overviewGrid) overviewGrid.classList.add("dashboard-alerts-grid");
  }

  function ensureNavigation() {
    const overviewLink = $('.nav-item[data-section="overview"]');
    if (overviewLink) {
      const label = $("span:last-child", overviewLink);
      if (label) label.textContent = "Dashboard";
      overviewLink.title = "Dashboard";
    }

    if (!$('.nav-item[data-section="operations"]')) {
      const reference = $('.nav-item[data-section="architecture"]');
      if (reference) {
        const link = document.createElement("a");
        link.href = "#operations";
        link.className = "nav-item";
        link.dataset.section = "operations";
        link.title = "Drift & system";
        link.innerHTML = '<span class="nav-icon">◫</span><span>Drift & system</span>';
        reference.parentNode.insertBefore(link, reference);
      }
    }

    $$(".nav-item").forEach((link) => {
      const label = $("span:last-child", link)?.textContent?.trim();
      if (label && !link.title) link.title = label;
    });
  }

  function ensureDashboardCards() {
    const overview = $("#overview");
    const metricGrid = $("#metricGrid");
    if (!overview || !metricGrid || $("#dashboardSnapshotGrid")) return;

    metricGrid.hidden = true;
    metricGrid.insertAdjacentHTML("afterend", `
      <div class="dashboard-snapshot-grid" id="dashboardSnapshotGrid">
        ${dashboardCard("dashboardSystem", "System", "#operations")}
        ${dashboardCard("dashboardLuna", "Luna", "#luna")}
        ${dashboardCard("dashboardFlyers", "Aviser", "#luna")}
        ${dashboardCard("dashboardWork", "Aktuelt arbejde", "#operations")}
        ${dashboardCard("dashboardIntegrations", "Integrationer", "#integrations")}
        ${dashboardCard("dashboardOpenAI", "Seneste OpenAI", "#luna")}
        ${dashboardCard("dashboardStorage", "Kurv data", "#data")}
        ${dashboardCard("dashboardDeploy", "Deploy & backup", "#operations")}
      </div>
    `);

    const alertsList = $("#alertsList");
    if (alertsList && !$("#dashboardAlertsMore")) {
      alertsList.insertAdjacentHTML("afterend", '<a id="dashboardAlertsMore" href="#operations" class="text-link">Se al drift & alarmhistorik →</a>');
    }
  }

  function dashboardCard(id, label, href) {
    return `
      <a class="dashboard-snapshot-card surface" id="${id}" href="${href}">
        <div class="dashboard-card-head">
          <span class="dashboard-card-label">${esc(label)}</span>
          <span class="dashboard-card-status" data-card-status></span>
        </div>
        <div>
          <strong class="dashboard-card-value" data-card-value>—</strong>
          <span class="dashboard-card-caption" data-card-caption>Afventer data</span>
        </div>
      </a>
    `;
  }

  function ensureOpenAIHistory() {
    const oldTarget = $("#opsOpenAIEvents");
    if (!oldTarget) return;
    const panel = oldTarget.closest("section.panel");
    if (!panel) return;
    panel.innerHTML = `
      <div class="panel-header">
        <div>
          <span class="panel-eyebrow">LUNA COST EVENTS</span>
          <h3>Faktiske OpenAI-kald</h3>
        </div>
        <span class="quiet-label">dato · klokkeslæt · pris</span>
      </div>
      <div id="dashboardOpenAIEvents" class="dashboard-openai-list"></div>
    `;
  }

  function setCard(id, { value, caption, status = "info" }) {
    const card = $(`#${id}`);
    if (!card) return;
    const valueTarget = $("[data-card-value]", card);
    const captionTarget = $("[data-card-caption]", card);
    const statusTarget = $("[data-card-status]", card);
    if (valueTarget) valueTarget.textContent = value;
    if (captionTarget) captionTarget.textContent = caption;
    if (statusTarget) statusTarget.className = `dashboard-card-status ${statusTone(status)}`;
  }

  function renderDashboard(snapshot) {
    latestSnapshot = snapshot;
    const overall = snapshot.overall || {};
    const health = overall.health_counts || {};
    const alerts = snapshot.alerts || [];
    const luna = snapshot.luna || {};
    const usage = luna.usage || {};
    const coverage = luna.current_coverage || {};
    const totalCoverage = Object.values(coverage).reduce((sum, value) => sum + number(value), 0);
    const runtime = snapshot.runtime || {};
    const operations = snapshot.operations || {};
    const integrations = operations.integration_quality || [];
    const healthyIntegrations = integrations.filter((row) => row.health === "healthy").length;
    const focus = runtime["luna-worker"]?.payload?.focus;
    const storage = snapshot.data?.storage || {};
    const deployment = operations.deployment || {};
    const backup = operations.backup || {};
    const latestOpenAI = (snapshot.telemetry?.timeline || []).find((row) => row.type === "openai_usage");

    const overallLabel = overall.status === "healthy" ? "Sund" : overall.status === "critical" ? "Kritisk" : "Opmærksomhed";
    setCard("dashboardSystem", {
      value: overallLabel,
      caption: `${alerts.length} aktive alarmer · ${health.healthy || 0}/${overall.components || 0} komponenter sunde`,
      status: overall.status,
    });

    setCard("dashboardLuna", {
      value: `${fmtDkk(usage.estimated_cost_dkk)} kr.`,
      caption: `${fmtInt(usage.requests)} requests · ${fmtDkk(usage.remaining_dkk)} kr. tilbage`,
      status: number(coverage.pending) > 0 ? "attention" : "healthy",
    });

    setCard("dashboardFlyers", {
      value: `${coverage.complete || 0}/${totalCoverage || 0}`,
      caption: `${coverage.pending || 0} pending · ${coverage.degraded || 0} degraded`,
      status: number(coverage.pending) > 0 || number(coverage.degraded) > 0 ? "attention" : "healthy",
    });

    setCard("dashboardWork", {
      value: focus ? `${focus.retailer || "Avis"}` : "Idle",
      caption: focus ? `${focus.title || "Aktiv analyse"} · ${focus.pages_remaining ?? "?"} sider tilbage` : "Ingen obligatorisk Luna-coverage i kø",
      status: focus ? "attention" : "healthy",
    });

    setCard("dashboardIntegrations", {
      value: `${healthyIntegrations}/${integrations.length || 0}`,
      caption: integrations.length ? integrations.map((row) => `${row.name}: ${row.state || row.health}`).join(" · ") : "Afventer integrationsstatus",
      status: integrations.length && healthyIntegrations === integrations.length ? "healthy" : "attention",
    });

    setCard("dashboardOpenAI", {
      value: latestOpenAI?.cost_dkk != null ? `+${fmtDkk(latestOpenAI.cost_dkk, 4)} kr.` : "Ingen nye kald",
      caption: latestOpenAI ? `${fmtEventDateTime(latestOpenAI.at)} · ${latestOpenAI.requests || 0} req.` : "Ingen cost-events registreret endnu",
      status: latestOpenAI ? "cost" : "healthy",
    });

    setCard("dashboardStorage", {
      value: fmtBytes(storage.kurv_persistent_bytes),
      caption: `Kun Kurv /data · ${fmtBytes(storage.qnap_volume_free_bytes)} ledig på QNAP`,
      status: "healthy",
    });

    const drift = deployment.drift || "unknown";
    setCard("dashboardDeploy", {
      value: drift === "none" ? "Synkron" : drift === "detected" ? "Drift fundet" : "Ukendt",
      caption: `${String(deployment.build_commit || snapshot.release?.commit || "unknown").slice(0, 9)} · backup ${backup.age_seconds != null ? `${fmtAge(backup.age_seconds)} siden` : "ikke registreret"}`,
      status: drift === "none" ? backup.status === "attention" ? "attention" : "healthy" : drift === "detected" ? "error" : "info",
    });

    renderOpenAIEvents(snapshot);
  }

  function renderOpenAIEvents(snapshot) {
    const target = $("#dashboardOpenAIEvents");
    if (!target) return;
    const rows = (snapshot.telemetry?.timeline || [])
      .filter((row) => row.category === "luna" && row.type === "openai_usage")
      .slice(0, 12);

    target.innerHTML = rows.length
      ? rows.map((row) => `
          <div class="dashboard-openai-event">
            <span class="ops-state-dot cost"></span>
            <div class="dashboard-openai-event-main">
              <strong>${esc(row.title || "OpenAI-kald")}</strong>
              <time datetime="${row.at ? new Date(Number(row.at) * 1000).toISOString() : ""}">${esc(fmtEventDateTime(row.at))}</time>
              <span>${esc(row.detail || row.retailer || "Luna enrichment")}</span>
            </div>
            <div class="dashboard-openai-event-cost">
              <strong>${row.cost_dkk != null ? `+${fmtDkk(row.cost_dkk, 4)} kr.` : "—"}</strong>
              <span>${row.requests != null ? `${fmtInt(row.requests)} req.` : ""}</span>
            </div>
          </div>
        `).join("")
      : '<div class="empty-state compact">Ingen OpenAI cost-events registreret endnu.</div>';
  }

  function activatePage(page, { updateHash = false } = {}) {
    const resolved = PAGE_ORDER.includes(page) && $(`#${page}`) ? page : "overview";
    activePage = resolved;

    $$(".section-block").forEach((section) => {
      section.classList.toggle("is-page-active", section.id === resolved);
      section.setAttribute("aria-hidden", section.id === resolved ? "false" : "true");
    });

    $$(".nav-item").forEach((link) => {
      const active = link.dataset.section === resolved;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });

    const title = $("#pageTitle");
    if (title) title.textContent = PAGE_TITLES[resolved] || "Kurv Control Center";
    document.title = `${PAGE_TITLES[resolved] || "Kurv"} · Kurv Control Center`;

    if (updateHash && window.location.hash !== `#${resolved}`) {
      history.pushState(null, "", `#${resolved}`);
    }

    window.scrollTo({ top: 0, behavior: "auto" });
    if ((resolved === "overview" || resolved === "luna") && latestSnapshot) renderDashboard(latestSnapshot);
    if (resolved === "overview" || resolved === "luna") refreshSnapshot();
  }

  function bindPageNavigation() {
    document.addEventListener("click", (event) => {
      const link = event.target.closest('a[href^="#"]');
      if (!link) return;
      const page = String(link.getAttribute("href") || "").slice(1);
      if (!PAGE_ORDER.includes(page)) return;
      event.preventDefault();
      activatePage(page, { updateHash: true });
    });

    window.addEventListener("popstate", () => {
      const page = window.location.hash.slice(1) || "overview";
      activatePage(page);
    });
  }

  async function refreshSnapshot() {
    if (document.hidden) return;
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) return;
      renderDashboard(await response.json());
    } catch (_) {
      // Base Control Center owns global connectivity feedback.
    }
  }

  function startRefreshLoop() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = window.setInterval(() => {
      if (activePage === "overview" || activePage === "luna") refreshSnapshot();
    }, 15000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && (activePage === "overview" || activePage === "luna")) refreshSnapshot();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Operations v2 injects its panels on DOMContentLoaded immediately before us.
    // Queue one microtask so every target exists before we reorganise the shell.
    queueMicrotask(() => {
      ensureOperationsPage();
      ensureNavigation();
      ensureDashboardCards();
      ensureOpenAIHistory();
      bindPageNavigation();
      document.body.classList.add("page-navigation-ready");
      const requested = window.location.hash.slice(1) || "overview";
      activatePage(requested);
      refreshSnapshot();
      startRefreshLoop();
    });
  });
})();
