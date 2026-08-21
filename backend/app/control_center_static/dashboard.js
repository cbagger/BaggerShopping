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

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const fmtInt = (value) => new Intl.NumberFormat("da-DK", { maximumFractionDigits: 0 }).format(number(value));
  const fmtDkk = (value, digits = 2) => new Intl.NumberFormat("da-DK", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(number(value));

  function statusTone(status) {
    const value = String(status || "unknown");
    if (["healthy", "success", "none", "complete", "online", "running", "available", "configured", "active"].includes(value)) return "healthy";
    if (["critical", "error", "stale", "unavailable"].includes(value)) return "error";
    if (["attention", "warning", "pending", "detected"].includes(value)) return "attention";
    if (["degraded", "quality-filtered", "filtered"].includes(value)) return "healthy";
    return "info";
  }

  function cleanCopy(value) {
    return String(value || "")
      .replaceAll("aktuelle avis-generationer er degraded", "aktuelle tilbudsaviser er klar med forbehold")
      .replaceAll("aktuelle avis-generationer", "aktuelle tilbudsaviser")
      .replaceAll("quarantine-årsager", "automatisk kvalitetsfiltrering")
      .replaceAll("degraded", "kvalitetsfiltreret")
      .replaceAll("pending", "i kø")
      .replaceAll("requests", "kald")
      .replaceAll("configured", "konfigureret")
      .replaceAll("available", "tilgængelig");
  }

  function setCard(id, { value, caption, status }) {
    const card = `#${id}`;
    const root = $(card);
    if (!root) return;
    const valueTarget = $("[data-card-value]", root);
    const captionTarget = $("[data-card-caption]", root);
    const statusTarget = $("[data-card-status]", root);
    if (valueTarget) valueTarget.textContent = value;
    if (captionTarget) captionTarget.textContent = caption;
    if (statusTarget) {
      const tone = statusTone(status);
      statusTarget.className = `dashboard-state ${tone}`;
      statusTarget.textContent = tone === "healthy" ? "Sund" : tone === "error" ? "Fejl" : tone === "attention" ? "Tjek" : "Info";
    }
  }

  function deploymentAction(snapshot) {
    const deployment = snapshot.operations?.deployment || {};
    if (deployment.drift !== "detected") return null;
    return {
      severity: "warning",
      title: "Control Center kører ikke samme version som den installerede release",
      detail: `Aktiv build ${String(deployment.build_commit || "ukendt").slice(0, 9)} · installeret ${String(deployment.marker_commit || "ukendt").slice(0, 9)}.`,
      href: "#operations",
    };
  }

  function actions(snapshot) {
    const result = [];
    const deployment = deploymentAction(snapshot);
    if (deployment) result.push(deployment);
    for (const alert of snapshot.alerts || []) {
      const key = `${alert.title}|${alert.detail}`;
      if (result.some((row) => `${row.title}|${row.detail}` === key)) continue;
      result.push({
        severity: alert.severity || "warning",
        title: cleanCopy(alert.title),
        detail: cleanCopy(alert.detail),
        href: String(alert.title || "").toLowerCase().includes("avis") ? "#luna" : "#operations",
      });
    }
    return result;
  }

  function renderHero(snapshot, activeActions) {
    const critical = snapshot.overall?.status === "critical" || activeActions.some((row) => row.severity === "critical");
    const attention = !critical && activeActions.length > 0;
    const tone = critical ? "critical" : attention ? "attention" : "healthy";
    const orb = $("#overallOrb");
    if (orb) orb.className = `status-orb ${tone}`;
    $("#heroStatus")?.setAttribute("data-status", tone);

    const kicker = $("#overallKicker");
    const title = $("#overallTitle");
    const subtitle = $("#overallSubtitle");
    if (critical) {
      kicker.textContent = "Handling nødvendig";
      title.textContent = "Kurv har en kritisk driftsfejl";
      subtitle.textContent = activeActions[0]?.detail || "Åbn Drift & system for at se den konkrete fejl.";
    } else if (attention) {
      kicker.textContent = "Kurv kører";
      title.textContent = `${activeActions.length} ting kræver din opmærksomhed`;
      subtitle.textContent = activeActions[0]?.detail || "Kernesystemet er online, men noget bør kontrolleres.";
    } else {
      kicker.textContent = "Alt ser godt ud";
      title.textContent = "Kurv kører som det skal";
      subtitle.textContent = "QNAP, aviser, Luna og integrationer er kontrolleret og sunde.";
    }

    const deployment = snapshot.operations?.deployment || {};
    const releaseCommit = $("#releaseCommit");
    const releaseIos = $("#releaseIos");
    if (releaseCommit) releaseCommit.textContent = String(deployment.marker_commit || snapshot.release?.commit || "ukendt").slice(0, 9);
    if (releaseIos) releaseIos.textContent = `iOS ${snapshot.release?.ios?.version || "—"} · build ${snapshot.release?.ios?.build || "—"}`;
  }

  function renderFlyers(snapshot) {
    const coverage = snapshot.luna?.current_coverage || {};
    const complete = number(coverage.complete);
    const pending = number(coverage.pending);
    const degraded = number(coverage.degraded);
    const notTracked = number(coverage.not_tracked);
    const total = complete + pending + degraded + notTracked;
    const ready = complete + degraded;
    setCard("dashboardFlyers", {
      value: `${ready} af ${total || 0} klar`,
      caption: `${complete} uden frasortering · ${degraded} med automatisk kvalitetsfiltrering · ${pending} analyseres`,
      status: pending > 0 || notTracked > 0 ? "attention" : "healthy",
    });
    const widths = total ? [complete, pending + notTracked, degraded].map((value) => `${value / total * 100}%`) : ["0%", "0%", "0%"];
    const completeBar = $("#dashboardCoverageComplete");
    const pendingBar = $("#dashboardCoveragePending");
    const degradedBar = $("#dashboardCoverageDegraded");
    if (completeBar) completeBar.style.width = widths[0];
    if (pendingBar) pendingBar.style.width = widths[1];
    if (degradedBar) degradedBar.style.width = widths[2];
  }

  function renderLuna(snapshot) {
    const usage = snapshot.luna?.usage || {};
    const spent = number(usage.estimated_cost_dkk);
    const budget = number(usage.budget_dkk);
    const percent = budget ? Math.min(100, Math.max(0, spent / budget * 100)) : 0;
    const focus = snapshot.runtime?.["luna-worker"]?.payload?.focus;
    setCard("dashboardLuna", {
      value: `${fmtDkk(spent)} kr.`,
      caption: `af ${fmtDkk(budget, 0)} kr. · ${fmtInt(usage.requests)} kald · ${fmtDkk(usage.remaining_dkk)} kr. tilbage`,
      status: budget && spent / budget >= .8 ? "attention" : "healthy",
    });
    const bar = $("#dashboardBudgetBar");
    if (bar) {
      bar.style.width = `${percent}%`;
      bar.className = percent >= 90 ? "critical" : percent >= 75 ? "attention" : "healthy";
    }
    const workline = $("#dashboardWorkline");
    if (workline) workline.textContent = focus
      ? `${focus.retailer || "Avis"}: ${focus.pages_remaining ?? "?"} sider tilbage`
      : "Ingen Luna-analyse kører lige nu";
  }

  function renderSystem(snapshot) {
    const overall = snapshot.overall || {};
    const health = overall.health_counts || {};
    const integrations = snapshot.operations?.integration_quality || [];
    const healthyIntegrations = integrations.filter((row) => row.health === "healthy").length;
    const runtimeRows = Object.values(snapshot.runtime || {});
    const runtimeHealthy = runtimeRows.filter((row) => row?.health === "healthy").length;
    const runtimeErrors = runtimeRows.filter((row) => row?.health === "error").length;
    const systemStatus = runtimeErrors
      ? "error"
      : integrations.length && healthyIntegrations !== integrations.length ? "attention" : "healthy";
    setCard("dashboardSystem", {
      value: `${runtimeHealthy} af ${runtimeRows.length} services online`,
      caption: `${health.healthy || 0}/${overall.components || 0} komponenter sunde · ${healthyIntegrations}/${integrations.length || 0} forbindelser sunde`,
      status: systemStatus,
    });
    const list = $("#dashboardIntegrationList");
    if (list) {
      list.innerHTML = integrations.slice(0, 4).map((row) => `
        <span><i class="${statusTone(row.health)}"></i>${esc(row.name)}<b>${row.health === "healthy" ? "OK" : "Tjek"}</b></span>
      `).join("");
    }
  }

  function renderActions(activeActions, snapshot) {
    const title = $("#dashboardActionTitle");
    const count = $("#alertCount");
    const list = $("#alertsList");
    if (!title || !count || !list) return;
    count.textContent = activeActions.length;
    if (!activeActions.length) {
      title.textContent = "Ingen handling nødvendig";
      list.className = "stack-list dashboard-clear-state";
      const filtered = number(snapshot?.luna?.current_coverage?.degraded);
      const detail = filtered
        ? `${fmtInt(filtered)} aktuelle aviser har automatisk kvalitetsfiltrering. Det kræver ingen handling.`
        : "Der er ingen aktive driftsalarmer.";
      list.innerHTML = `<div class="dashboard-clear-icon">✓</div><div><strong>Alt er under kontrol</strong><span>${esc(detail)}</span></div>`;
      return;
    }
    title.textContent = "Kræver opmærksomhed";
    list.className = "stack-list";
    const visible = activeActions.slice(0, 3);
    list.innerHTML = visible.map((row) => `
      <a class="alert-row ${statusTone(row.severity)}" href="${esc(row.href)}">
        <i class="alert-dot"></i>
        <div><strong>${esc(row.title)}</strong><p>${esc(row.detail || "Åbn detaljerne for at se mere.")}</p></div>
        <span>→</span>
      </a>
    `).join("") + (activeActions.length > visible.length ? `<div class="dashboard-more-actions">+ ${activeActions.length - visible.length} yderligere</div>` : "");
  }

  function renderFamily(snapshot) {
    const households = snapshot.data?.households || {};
    const metadata = snapshot.data?.offer_metadata || {};
    const members = $("#dashboardMembers");
    const families = $("#dashboardFamilies");
    const bindings = $("#dashboardBindings");
    const ios = $("#dashboardIosVersion");
    if (members) members.textContent = fmtInt(households.members);
    if (families) families.textContent = fmtInt(households.households);
    if (bindings) bindings.textContent = fmtInt(metadata.records);
    if (ios) ios.textContent = `iOS ${snapshot.release?.ios?.version || "—"} · build ${snapshot.release?.ios?.build || "—"}`;
  }

  function renderDashboard(snapshot) {
    const activeActions = actions(snapshot);
    renderHero(snapshot, activeActions);
    renderFlyers(snapshot);
    renderLuna(snapshot);
    renderSystem(snapshot);
    renderActions(activeActions, snapshot);
    renderFamily(snapshot);
  }

  function activatePage(page, { updateHash = false } = {}) {
    const resolved = PAGE_ORDER.includes(page) && $(`#${page}`) ? page : "overview";
    $$(".section-block").forEach((section) => {
      const active = section.id === resolved;
      section.classList.toggle("is-page-active", active);
      section.setAttribute("aria-hidden", active ? "false" : "true");
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
    if (updateHash && window.location.hash !== `#${resolved}`) history.pushState(null, "", `#${resolved}`);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function bindNavigation() {
    document.addEventListener("click", (event) => {
      const link = event.target.closest('a[href^="#"]');
      if (!link) return;
      const page = String(link.getAttribute("href") || "").slice(1);
      if (!PAGE_ORDER.includes(page)) return;
      event.preventDefault();
      activatePage(page, { updateHash: true });
    });
    window.addEventListener("popstate", () => activatePage(window.location.hash.slice(1) || "overview"));
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindNavigation();
    window.addEventListener("kurv:snapshot", (event) => renderDashboard(event.detail || {}));
    document.body.classList.add("page-navigation-ready");
    activatePage(window.location.hash.slice(1) || "overview");
  });
})();
