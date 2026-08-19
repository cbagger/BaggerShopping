(() => {
  "use strict";

  let latest = null;
  let activityFilter = "all";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const num = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const fmtInt = (value) => value === null || value === undefined ? "—" : new Intl.NumberFormat("da-DK", { maximumFractionDigits: 0 }).format(num(value));
  const fmtDkk = (value, digits = 2) => value === null || value === undefined ? "—" : new Intl.NumberFormat("da-DK", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(num(value));
  const fmtBytes = (bytes) => {
    if (bytes === null || bytes === undefined) return "—";
    let value = Number(bytes);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let unit = 0;
    while (Math.abs(value) >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return `${value.toLocaleString("da-DK", { maximumFractionDigits: unit >= 2 ? 2 : 0 })} ${units[unit]}`;
  };
  const fmtAge = (seconds) => {
    if (seconds === null || seconds === undefined) return "ukendt";
    const value = Math.max(0, Math.round(Number(seconds)));
    if (value < 60) return `${value} sek.`;
    if (value < 3600) return `${Math.round(value / 60)} min.`;
    if (value < 86400) return `${(value / 3600).toLocaleString("da-DK", { maximumFractionDigits: 1 })} t.`;
    return `${(value / 86400).toLocaleString("da-DK", { maximumFractionDigits: 1 })} dage`;
  };
  const fmtDateTime = (timestamp) => timestamp ? new Intl.DateTimeFormat("da-DK", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(Number(timestamp) * 1000)) : "—";
  const tone = (status) => {
    const value = String(status || "unknown");
    if (["healthy", "success", "online", "running", "available", "complete", "connected", "configured", "active", "none"].includes(value)) return "healthy";
    if (["error", "critical", "unavailable", "stale"].includes(value)) return "error";
    if (["attention", "warning", "degraded", "degraded-present", "pending", "detected"].includes(value)) return "attention";
    if (value === "cost") return "cost";
    return "info";
  };

  function injectLayout() {
    if ($("#operationsCockpit")) return;
    const currentWork = $("#overview .current-work-panel");
    if (currentWork) {
      currentWork.insertAdjacentHTML("afterend", `
        <section id="operationsCockpit" class="ops-section">
          <div class="ops-section-heading">
            <div><span class="panel-eyebrow">OPERATIONS</span><h3>Kurv virker — hele kæden</h3></div>
            <span class="quiet-label">read-only synthetic evidence</span>
          </div>
          <div class="ops-cockpit-grid">
            <article class="surface ops-panel ops-e2e"><div id="opsE2E"></div></article>
            <article class="surface ops-panel"><div class="ops-title-row"><div><span class="panel-eyebrow">FRESHNESS</span><h4>Dataliv</h4></div></div><div id="opsFreshness" class="ops-list"></div></article>
            <article class="surface ops-panel"><div class="ops-title-row"><div><span class="panel-eyebrow">JOBS & QUEUES</span><h4>Workers</h4></div></div><div id="opsJobs" class="ops-list"></div></article>
            <article class="surface ops-panel"><div class="ops-title-row"><div><span class="panel-eyebrow">RELEASE</span><h4>Deploy & recovery</h4></div></div><div id="opsRelease"></div></article>
          </div>
          <div class="ops-trend-grid" id="opsTrendGrid"></div>
        </section>
      `);
    }

    const lunaTop = $("#luna .luna-top-grid");
    if (lunaTop) {
      lunaTop.insertAdjacentHTML("afterend", `
        <div class="ops-luna-grid">
          <section class="panel surface"><div class="panel-header"><div><span class="panel-eyebrow">DEGRADED IMPACT</span><h3>Hvad betyder degraded?</h3></div></div><div id="opsDegraded"></div></section>
          <section class="panel surface"><div class="panel-header"><div><span class="panel-eyebrow">LUNA COST EVENTS</span><h3>Faktiske OpenAI-kald</h3></div></div><div id="opsOpenAIEvents" class="ops-list"></div></section>
        </div>
      `);
    }

    const integrationGrid = $("#integrationGrid");
    if (integrationGrid) {
      integrationGrid.insertAdjacentHTML("beforebegin", `<div id="opsIntegrationQuality" class="ops-quality-grid"></div>`);
    }

    const dataCards = $("#dataCards");
    if (dataCards) {
      dataCards.insertAdjacentHTML("afterend", `
        <div class="ops-data-grid">
          <section class="panel surface"><div class="panel-header"><div><span class="panel-eyebrow">DATA HEALTH</span><h3>Integritet</h3></div></div><div id="opsIntegrity" class="ops-list"></div></section>
          <section class="panel surface"><div class="panel-header"><div><span class="panel-eyebrow">SECURITY POSTURE</span><h3>Sikkerhed</h3></div></div><div id="opsSecurity" class="ops-list"></div></section>
          <section class="panel surface"><div class="panel-header"><div><span class="panel-eyebrow">CLIENT FLEET</span><h3>iPhones</h3></div></div><div id="opsClients" class="ops-list"></div></section>
        </div>
      `);
    }

    const activityHeading = $("#activity .section-heading");
    if (activityHeading) {
      const paragraph = $("p", activityHeading);
      if (paragraph) paragraph.textContent = "Kun meningsfulde hændelser: systemskift, avisstatus og faktiske Luna/OpenAI-kald. Heartbeat-polling skjules.";
      activityHeading.insertAdjacentHTML("afterend", `
        <div class="ops-activity-toolbar">
          <div class="segmented-control" id="opsActivityFilters">
            <button class="segment is-active" data-activity="all">Alle</button>
            <button class="segment" data-activity="luna">Luna / OpenAI</button>
            <button class="segment" data-activity="flyer">Aviser</button>
            <button class="segment" data-activity="system">System</button>
          </div>
          <div id="opsActivitySummary" class="quiet-label"></div>
        </div>
      `);
      $$("#opsActivityFilters .segment").forEach(button => button.addEventListener("click", () => {
        $$("#opsActivityFilters .segment").forEach(item => item.classList.remove("is-active"));
        button.classList.add("is-active");
        activityFilter = button.dataset.activity;
        if (latest) renderActivity(latest);
      }));
    }
  }

  function badge(status, label) {
    return `<span class="ops-badge ${tone(status)}"><i></i>${esc(label || status || "unknown")}</span>`;
  }

  function renderE2E(snapshot) {
    const e2e = snapshot.operations?.end_to_end || {};
    const target = $("#opsE2E");
    if (!target) return;
    target.innerHTML = `
      <div class="ops-title-row"><div><span class="panel-eyebrow">END-TO-END</span><h4>${e2e.operational_status === "healthy" ? "Kunde-read-path er sund" : "Kæden kræver opmærksomhed"}</h4></div>${badge(e2e.operational_status, e2e.operational_status)}</div>
      <p class="ops-copy">${esc(e2e.note || "")}</p>
      <div class="ops-stage-strip">${(e2e.stages || []).map(stage => `<div class="ops-stage ${tone(stage.status)}"><i></i><div><strong>${esc(stage.name)}</strong><span>${esc(stage.detail || stage.status)}</span></div></div>`).join("")}</div>
      <div class="ops-quality-line"><span>Kvalitetsstatus</span>${badge(e2e.quality_status, e2e.quality_status)}</div>
    `;
  }

  function renderFreshness(snapshot) {
    const target = $("#opsFreshness");
    if (!target) return;
    target.innerHTML = (snapshot.operations?.freshness || []).map(row => `
      <div class="ops-row"><span class="ops-state-dot ${tone(row.health)}"></span><div><strong>${esc(row.name)}</strong><small>${row.at ? `senest ${esc(fmtDateTime(row.at))}` : "ingen timestamp"}</small></div><b>${esc(fmtAge(row.age_seconds))}</b></div>
    `).join("");
  }

  function renderJobs(snapshot) {
    const target = $("#opsJobs");
    if (!target) return;
    target.innerHTML = (snapshot.operations?.jobs || []).map(job => {
      const focus = job.focus && typeof job.focus === "object" ? `${job.focus.retailer || ""} ${job.focus.title || ""}`.trim() : "";
      const next = job.next_run_in_hours != null ? `næste om ${Number(job.next_run_in_hours).toLocaleString("da-DK", { maximumFractionDigits: 1 })} t.` : "";
      return `<div class="ops-row"><span class="ops-state-dot ${tone(job.health)}"></span><div><strong>${esc(job.name)}</strong><small>${esc(focus || next || job.detail || job.state || "idle")}</small></div><b>${esc(job.state || "—")}</b></div>`;
    }).join("");
  }

  function renderRelease(snapshot) {
    const target = $("#opsRelease");
    if (!target) return;
    const release = snapshot.operations?.deployment || {};
    const backup = snapshot.operations?.backup || {};
    const driftTone = release.drift === "none" ? "healthy" : release.drift === "detected" ? "error" : "info";
    target.innerHTML = `
      <div class="ops-release-grid">
        <div><span>Control Center</span><strong>${esc(release.control_center || "—")}</strong></div>
        <div><span>Build commit</span><strong class="mono">${esc(String(release.build_commit || "unknown").slice(0, 10))}</strong></div>
        <div><span>Deploy marker</span><strong class="mono">${esc(String(release.marker_commit || "unknown").slice(0, 10))}</strong></div>
        <div><span>Drift</span>${badge(driftTone, release.drift || "unknown")}</div>
      </div>
      <div class="ops-backup"><span>Seneste backup</span><strong>${backup.last_backup_at ? esc(fmtDateTime(backup.last_backup_at)) : "Ikke registreret endnu"}</strong><small>${esc(backup.note || (backup.age_seconds != null ? `${fmtAge(backup.age_seconds)} gammel` : ""))}</small></div>
    `;
  }

  function sparkline(series) {
    const rows = (series || []).filter(row => row && row.value !== null && row.value !== undefined);
    if (rows.length < 2) return `<div class="ops-spark-empty">Samler 7-dages historik…</div>`;
    const values = rows.map(row => Number(row.value));
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const points = values.map((value, index) => `${(index / (values.length - 1) * 100).toFixed(2)},${(28 - ((value - min) / span * 24)).toFixed(2)}`).join(" ");
    return `<svg class="ops-spark" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true"><polyline points="${points}" vector-effect="non-scaling-stroke"></polyline></svg>`;
  }

  function renderTrends(snapshot) {
    const target = $("#opsTrendGrid");
    if (!target) return;
    const series = snapshot.operations?.trends?.series || {};
    const definitions = [
      ["core_ms", "Core latency", "ms"], ["mobile_ms", "Mobile latency", "ms"],
      ["luna_cost_dkk", "Luna spend", "kr."], ["coverage_degraded", "Degraded", "aviser"]
    ];
    target.innerHTML = definitions.map(([key, label, unit]) => {
      const rows = series[key] || [], latestRow = rows.at(-1), first = rows[0];
      const delta = latestRow && first ? Number(latestRow.value) - Number(first.value) : null;
      return `<article class="surface ops-trend-card"><div><span>${esc(label)}</span><strong>${latestRow ? `${Number(latestRow.value).toLocaleString("da-DK", { maximumFractionDigits: 2 })} ${unit}` : "—"}</strong><small>${delta == null ? "ingen trend endnu" : `${delta >= 0 ? "+" : ""}${delta.toLocaleString("da-DK", { maximumFractionDigits: 2 })} over historikken`}</small></div>${sparkline(rows)}</article>`;
    }).join("");
  }

  function renderDegraded(snapshot) {
    const target = $("#opsDegraded");
    if (!target) return;
    const d = snapshot.operations?.degraded_impact || {};
    target.innerHTML = `
      <div class="ops-impact-grid">
        <div><strong>${fmtInt(d.degraded_publications)}</strong><span>degraded</span></div>
        <div><strong>${fmtInt(d.customer_sensitive_publications)}</strong><span>potentielt pris/member-sensitive</span></div>
        <div><strong>${fmtInt(d.other_quality_publications)}</strong><span>øvrig kvalitet</span></div>
      </div>
      <p class="ops-copy">${esc(d.note || "")}</p>
      <div class="ops-reasons">${(d.top_reasons || []).length ? d.top_reasons.map(row => `<div><span>${esc(row.reason)}</span><strong>${fmtInt(row.count)}</strong></div>`).join("") : '<span class="empty-state compact">Ingen aktuelle quarantine-årsager.</span>'}</div>
    `;
  }

  function renderOpenAIEvents(snapshot) {
    const target = $("#opsOpenAIEvents");
    if (!target) return;
    const rows = (snapshot.telemetry?.timeline || []).filter(row => row.category === "luna" && row.type === "openai_usage").slice(0, 6);
    target.innerHTML = rows.length ? rows.map(row => `<div class="ops-row"><span class="ops-state-dot cost"></span><div><strong>${esc(row.title)}</strong><small>${esc(row.detail || "")}</small></div><b>${row.cost_dkk != null ? `+${fmtDkk(row.cost_dkk, 4)} kr.` : "—"}</b></div>`).join("") : '<div class="empty-state compact">Ingen nye OpenAI-kald registreret siden Operations v2 blev aktiveret.</div>';
  }

  function renderIntegrationQuality(snapshot) {
    const target = $("#opsIntegrationQuality");
    if (!target) return;
    target.innerHTML = (snapshot.operations?.integration_quality || []).map(row => `
      <article class="surface ops-quality-card"><div class="ops-title-row"><strong>${esc(row.name)}</strong>${badge(row.health, row.state)}</div><div class="ops-quality-metrics"><span>Sidste succes <b>${esc(fmtDateTime(row.last_success_at))}</b></span>${row.latency_ms != null ? `<span>Latency <b>${fmtInt(row.latency_ms)} ms</b></span>` : ""}</div><p>${esc(row.detail || "")}</p></article>
    `).join("");
  }

  function renderData(snapshot) {
    const target = $("#dataCards");
    if (!target) return;
    const storage = snapshot.data?.storage || {};
    const h = snapshot.data?.households || {}, m = snapshot.data?.offer_metadata || {}, i = snapshot.data?.product_identity || {};
    target.innerHTML = `
      <article class="data-stat-card surface ops-storage-primary"><span>Kurv persistent data</span><strong>${fmtBytes(storage.kurv_persistent_bytes)}</strong><small>Kun Kurv /data — ikke resten af QNAP</small></article>
      <article class="data-stat-card surface"><span>QNAP ledig plads</span><strong>${fmtBytes(storage.qnap_volume_free_bytes)}</strong><small>Hele QNAP-volume · total ${fmtBytes(storage.qnap_volume_total_bytes)} · host brugt ${fmtBytes(storage.qnap_volume_used_bytes)}</small></article>
      <article class="data-stat-card surface"><span>Familiedata</span><strong>${fmtInt(h.members)}</strong><small>${fmtInt(h.households)} familier · ${fmtInt(h.pending_invites)} aktive invites</small></article>
      <article class="data-stat-card surface"><span>Tilbudsmetadata</span><strong>${fmtInt(m.records)}</strong><small>${fmtInt(m.pinned)} pinned · ${fmtInt(m.with_offer_snapshot)} snapshots</small></article>
      <article class="data-stat-card surface"><span>Identity-læring</span><strong>${fmtInt(i.stored_rules)}</strong><small>persistent product identity state</small></article>
      <article class="data-stat-card surface"><span>Control Center historik</span><strong>${fmtBytes(storage.control_center_telemetry_bytes)}</strong><small>heartbeats, events, trends og alert-lifecycle</small></article>
    `;
  }

  function renderList(targetSelector, rows) {
    const target = $(targetSelector);
    if (!target) return;
    target.innerHTML = rows.map(row => `<div class="ops-row"><span class="ops-state-dot ${tone(row.status)}"></span><div><strong>${esc(row.name)}</strong><small>${esc(row.detail || "")}</small></div><b>${esc(row.status || "—")}</b></div>`).join("");
  }

  function renderIntegrity(snapshot) {
    renderList("#opsIntegrity", snapshot.data?.integrity?.checks || []);
  }

  function renderSecurity(snapshot) {
    renderList("#opsSecurity", snapshot.operations?.security || []);
  }

  function renderClients(snapshot) {
    const target = $("#opsClients");
    if (!target) return;
    const clients = snapshot.operations?.clients || {};
    const rows = clients.clients || [];
    target.innerHTML = `<div class="ops-client-summary"><strong>${fmtInt(clients.enabled)}/${fmtInt(clients.registered)}</strong><span>push-aktive klienter</span></div>${rows.length ? rows.map(row => `<div class="ops-row"><span class="ops-state-dot ${row.enabled ? "healthy" : "attention"}"></span><div><strong>${esc(row.label)}</strong><small>${esc([row.version && `v${row.version}`, row.build && `build ${row.build}`, row.environment].filter(Boolean).join(" · ") || "APNs registration")}</small></div><b>${esc(row.push_permission || (row.enabled ? "enabled" : "disabled"))}</b></div>`).join("") : '<div class="empty-state compact">Ingen APNs-klienter registreret.</div>'}<p class="ops-microcopy">${esc(clients.note || "")}</p>`;
  }

  function renderAlerts(snapshot) {
    const target = $("#alertsList");
    if (!target) return;
    const alerts = snapshot.alerts || [];
    if (!alerts.length) return;
    target.className = "stack-list";
    target.innerHTML = alerts.map(row => `<article class="alert-row ${esc(row.severity || "warning")}"><i class="alert-dot"></i><div><strong>${esc(row.title)}</strong><p>${esc(row.detail || "")}</p><div class="ops-alert-meta"><span>først set ${esc(fmtDateTime(row.first_seen))}</span><span>${esc(fmtAge(row.duration_seconds))}</span><span>${fmtInt(row.occurrences)} observationer</span></div></div></article>`).join("");
  }

  function renderDependencyHealth(snapshot) {
    const edgeHealth = new Map((snapshot.operations?.dependency_map?.edges || []).map(edge => [`${edge.from}>${edge.to}`, edge.health]));
    const nodes = $$("#dataflow .flow-node");
    nodes.forEach((node, index) => {
      const next = nodes[index + 1];
      const health = next ? edgeHealth.get(`${node.dataset.component}>${next.dataset.component}`) : null;
      node.classList.remove("ops-edge-healthy", "ops-edge-attention", "ops-edge-error");
      if (health) node.classList.add(`ops-edge-${tone(health)}`);
    });
  }

  function renderActivity(snapshot) {
    const target = $("#timeline");
    if (!target) return;
    const all = snapshot.telemetry?.timeline || [];
    const rows = activityFilter === "all" ? all : all.filter(row => row.category === activityFilter);
    const counts = snapshot.telemetry?.activity_categories || {};
    const summary = $("#opsActivitySummary");
    if (summary) summary.textContent = `${counts.luna || 0} Luna · ${counts.flyer || 0} avis · ${counts.system || 0} system`;
    target.innerHTML = rows.length ? rows.map(row => {
      const cost = row.cost_dkk != null ? `<span class="ops-event-cost">+${fmtDkk(row.cost_dkk, 4)} kr.</span>` : "";
      const req = row.requests != null ? `<span>${fmtInt(row.requests)} req.</span>` : "";
      return `<div class="ops-event-row"><span class="timeline-time">${esc(fmtDateTime(row.at))}</span><i class="ops-event-dot ${tone(row.severity || row.status)}"></i><div class="ops-event-main"><strong>${esc(row.title || row.detail || row.type)}</strong><span>${esc(row.detail || row.retailer || row.category || "")}</span></div><div class="ops-event-meta">${req}${cost}<span class="ops-category">${esc(row.category || "event")}</span></div></div>`;
    }).join("") : '<div class="empty-state">Ingen meningsfulde events i dette filter.</div>';
  }

  function render(snapshot) {
    latest = snapshot;
    renderE2E(snapshot); renderFreshness(snapshot); renderJobs(snapshot); renderRelease(snapshot); renderTrends(snapshot);
    renderDegraded(snapshot); renderOpenAIEvents(snapshot); renderIntegrationQuality(snapshot); renderData(snapshot);
    renderIntegrity(snapshot); renderSecurity(snapshot); renderClients(snapshot); renderAlerts(snapshot); renderDependencyHealth(snapshot); renderActivity(snapshot);
  }

  async function fetchSnapshot() {
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store", headers: { Accept: "application/json" } });
      if (response.ok) render(await response.json());
    } catch (_) { /* base Control Center owns connection/error UI */ }
  }

  function connect() {
    const source = new EventSource("/api/events");
    source.addEventListener("snapshot", event => {
      try { render(JSON.parse(event.data)); } catch (_) { /* keep base UI alive */ }
    });
    source.onerror = () => setTimeout(fetchSnapshot, 2500);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    injectLayout();
    await fetchSnapshot();
    connect();
    const observer = new MutationObserver(() => { if (latest) { renderAlerts(latest); renderDependencyHealth(latest); } });
    const alertsTarget = $("#alertsList");
    if (alertsTarget) observer.observe(alertsTarget, { childList: true });
  });
})();
