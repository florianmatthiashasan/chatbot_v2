(function () {
  const root = document.getElementById("aicb-admin-root");
  if (!root || !window.AICBAdmin) return;

  const state = {
    tab: "training",
    settings: null,
    widget: null,
    faqs: [],
    memory: { items: [], total: 0, q: "" },
    stats: null,
    job: null,
    content: null,
    contentSearch: "",
    busy: false,
    notice: "",
    error: "",
  };

  const permissions = Object.assign(
    { canAccessAdmin: false, canManageSensitive: false },
    AICBAdmin.permissions || {}
  );
  const sensitiveTabs = ["content", "settings", "widget", "memory"];
  const allTabs = [
    ["training", "Training"],
    ["content", "Inhalte"],
    ["chat", "Test Chat"],
    ["settings", "Einstellungen"],
    ["widget", "Widget"],
    ["memory", "Memory"],
    ["faqs", "FAQs"],
    ["stats", "Statistiken"],
  ];
  const tabs = allTabs.filter(([id]) => permissions.canManageSensitive || !sensitiveTabs.includes(id));
  if (!tabs.some(([id]) => id === state.tab)) {
    state.tab = tabs.length ? tabs[0][0] : "";
  }

  let testSession = "";
  let testHistory = [];

  function api(path, options) {
    return fetch(AICBAdmin.restUrl + path.replace(/^\/+/, ""), {
      method: options && options.method ? options.method : "GET",
      headers: {
        "Content-Type": "application/json",
        "X-WP-Nonce": AICBAdmin.nonce,
      },
      body: options && options.body ? JSON.stringify(options.body) : undefined,
    }).then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || data.message || "Request failed");
      return data;
    });
  }

  function set(partial) {
    Object.assign(state, partial);
    render();
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function field(label, html, hint) {
    return `<label class="aicb-field"><span>${label}</span>${html}${hint ? `<small>${hint}</small>` : ""}</label>`;
  }

  function button(label, attrs) {
    const type = attrs && attrs.submit ? "submit" : "button";
    return `<button type="${type}" class="button ${attrs && attrs.primary ? "button-primary" : ""}" ${attrs && attrs.disabled ? "disabled" : ""} ${attrs && attrs.id ? `id="${attrs.id}"` : ""}>${label}</button>`;
  }

  async function loadInitial() {
    try {
      set({ busy: true, error: "" });
      const [faqs, stats, job] = await Promise.all([
        api("admin/faqs"),
        api("admin/stats"),
        api("admin/train/status"),
      ]);
      const nextState = { faqs: faqs.faqs || [], stats, job, busy: false };
      if (permissions.canManageSensitive) {
        const [settings, widget, memory] = await Promise.all([
          api("admin/settings"),
          api("admin/widget"),
          api("admin/memory?limit=20"),
        ]);
        Object.assign(nextState, { settings, widget, memory });
      }
      set(nextState);
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function saveSettings(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    const enabled = Array.from(form.querySelectorAll("[name='enabled_post_types']:checked")).map((el) => el.value);
    const payload = {
      openai_api_key: data.openai_api_key || "",
      chat_model: data.chat_model || "gpt-4o-mini",
      embedding_model: data.embedding_model || "text-embedding-3-large",
      retriever_k: Number(data.retriever_k || 8),
      max_context_chars: Number(data.max_context_chars || 14000),
      batch_size: Number(data.batch_size || 4),
      auto_index_on_save: Boolean(data.auto_index_on_save),
      widget_enabled: Boolean(data.widget_enabled),
      include_excerpts: Boolean(data.include_excerpts),
      include_taxonomies: Boolean(data.include_taxonomies),
      enabled_post_types: enabled,
      privacy_url: data.privacy_url || "",
      contact_url: data.contact_url || "",
      contact_email: data.contact_email || "",
      contact_phone: data.contact_phone || "",
      system_prompt: data.system_prompt || "",
    };
    set({ busy: true, error: "", notice: "" });
    try {
      const settings = await api("admin/settings", { method: "POST", body: payload });
      set({ settings, busy: false, notice: "Einstellungen gespeichert." });
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function saveWidget(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    const topics = [];
    form.querySelectorAll("[data-topic-row]").forEach((row) => {
      const label = row.querySelector("[name='topic_label']").value.trim();
      const question = row.querySelector("[name='topic_question']").value.trim();
      const url = row.querySelector("[name='topic_url']").value.trim();
      const highlight = row.querySelector("[name='topic_highlight']").checked;
      if (label && (question || url)) topics.push({ label, question, url, highlight });
    });
    const payload = {
      theme: {
        accent: data.accent,
        accentStrong: data.accentStrong,
        statusDot: data.statusDot,
        launcherBg: data.launcherBg,
        bg: data.bg,
        panel: data.panel,
        text: data.text,
        avatarBg: data.avatarBg,
        avatarFg: data.avatarFg,
        userBubble: data.userBubble,
        botBubble: data.botBubble,
        composerBg: data.composerBg,
        composerBorder: data.composerBorder,
        composerButtonBg: data.composerButtonBg,
        composerButtonText: data.composerButtonText,
      },
      copy: {
        icon: data.icon,
        title: data.title,
        status: data.status,
        intro: data.intro,
        topics_label: data.topics_label,
        placeholder: data.placeholder,
        disclaimer: data.disclaimer,
        privacy_label: data.privacy_label,
      },
      greeting: {
        enabled: Boolean(data.greeting_enabled),
        text: data.greeting_text,
        delay_ms: Number(data.greeting_delay_ms || 1200),
      },
      page_suggestions: {
        enabled: Boolean(data.page_suggestions_enabled),
        show_on_route_change: Boolean(data.page_suggestions_route_change),
      },
      hero: {
        hide_in_hero: Boolean(data.hide_in_hero),
        selector: (data.hero_selector || "").trim(),
      },
      analytics: {
        track_opens: Boolean(data.track_opens),
        track_outcomes: Boolean(data.track_outcomes),
        conversion_selector: (data.conversion_selector || "").trim(),
        form_selector: (data.form_selector || "").trim(),
      },
      topics,
    };
    set({ busy: true, error: "", notice: "" });
    try {
      const widget = await api("admin/widget", { method: "POST", body: payload });
      set({ widget, busy: false, notice: "Widget gespeichert." });
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function startTraining() {
    set({ busy: true, error: "", notice: "" });
    try {
      const job = await api("admin/train/start", { method: "POST", body: { clear: true } });
      set({ job, busy: false, notice: "Training gestartet." });
      runTrainingLoop(job.job_id);
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function runTrainingLoop(jobId) {
    let done = false;
    while (!done) {
      try {
        const job = await api("admin/train/step", { method: "POST", body: { job_id: jobId } });
        set({ job });
        done = job.status === "done";
        if (!done) await new Promise((resolve) => setTimeout(resolve, 350));
      } catch (err) {
        set({ error: err.message, busy: false });
        return;
      }
    }
    const [settings, stats, memory] = await Promise.all([
      api("admin/settings"),
      api("admin/stats"),
      api("admin/memory?limit=20"),
    ]);
    set({ settings, stats, memory, notice: "Training abgeschlossen." });
  }

  async function loadContent(search) {
    set({ busy: true, error: "" });
    try {
      const q = typeof search === "string" ? search : state.contentSearch;
      const content = await api(`admin/content?q=${encodeURIComponent(q || "")}`);
      set({ content, contentSearch: q || "", busy: false });
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function saveContent(retrain) {
    const rootEl = document.getElementById("aicb-content-form");
    const mode = rootEl && rootEl.querySelector("[name='index_mode']:checked")
      ? rootEl.querySelector("[name='index_mode']:checked").value
      : "all";
    const postIds = rootEl
      ? Array.from(rootEl.querySelectorAll("[name='content_post']:checked")).map((el) => Number(el.value))
      : [];
    const pdfIds = (state.content && state.content.pdfs ? state.content.pdfs : []).map((p) => Number(p.id));
    set({ busy: true, error: "", notice: "" });
    try {
      const content = await api("admin/content", { method: "POST", body: { mode, post_ids: postIds, pdf_ids: pdfIds } });
      set({ content, busy: false, notice: "Auswahl gespeichert." });
      if (retrain) {
        set({ tab: "training" });
        startTraining();
      }
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  // Mediathek-Dialog für PDFs (wp.media). Auswahl wird an state.content.pdfs angehängt.
  let pdfFrame = null;
  function openPdfPicker() {
    if (typeof wp === "undefined" || !wp.media) {
      set({ error: "Mediathek konnte nicht geladen werden. Seite neu laden." });
      return;
    }
    if (!pdfFrame) {
      pdfFrame = wp.media({
        title: "PDFs aus der Mediathek wählen",
        multiple: true,
        library: { type: "application/pdf" },
        button: { text: "Auswahl übernehmen" },
      });
      pdfFrame.on("select", () => {
        const selection = pdfFrame.state().get("selection").toJSON();
        const current = (state.content && state.content.pdfs) || [];
        const byId = {};
        current.forEach((p) => (byId[Number(p.id)] = p));
        selection.forEach((att) => {
          if (att.mime !== "application/pdf") return;
          byId[Number(att.id)] = { id: Number(att.id), title: att.filename || att.title || `PDF ${att.id}`, url: att.url || "" };
        });
        const pdfs = Object.values(byId);
        set({ content: Object.assign({}, state.content, { pdfs }) });
      });
    }
    pdfFrame.open();
  }

  function removePdf(id) {
    const pdfs = ((state.content && state.content.pdfs) || []).filter((p) => Number(p.id) !== Number(id));
    set({ content: Object.assign({}, state.content, { pdfs }) });
  }

  async function saveFaqs(form) {
    const faqs = [];
    form.querySelectorAll("[data-faq-row]").forEach((row) => {
      const question = row.querySelector("[name='faq_question']").value.trim();
      const answer = row.querySelector("[name='faq_answer']").value.trim();
      if (question || answer) faqs.push({ question, answer });
    });
    set({ busy: true, error: "", notice: "" });
    try {
      const data = await api("admin/faqs", { method: "POST", body: { faqs } });
      set({ faqs: data.faqs || [], busy: false, notice: "FAQs gespeichert. Starte danach Training neu, damit sie im Chat genutzt werden." });
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function loadMemory(q) {
    try {
      const memory = await api(`admin/memory?limit=80&q=${encodeURIComponent(q || "")}`);
      set({ memory: { ...memory, q: q || "" } });
    } catch (err) {
      set({ error: err.message });
    }
  }

  async function deleteMemory(id) {
    if (!confirm("Diesen Chunk wirklich löschen?")) return;
    try {
      await api("admin/memory", { method: "DELETE", body: { id } });
      await loadMemory(state.memory.q);
    } catch (err) {
      set({ error: err.message });
    }
  }

  async function saveMemory(row) {
    const id = Number(row.dataset.id);
    const title = row.querySelector("[name='memory_title']").value;
    const content = row.querySelector("[name='memory_content']").value;
    set({ busy: true, error: "", notice: "" });
    try {
      await api("admin/memory", { method: "POST", body: { id, title, content } });
      set({ busy: false, notice: "Memory-Eintrag aktualisiert." });
      await loadMemory(state.memory.q);
    } catch (err) {
      set({ error: err.message, busy: false });
    }
  }

  async function sendTestChat(form) {
    const input = form.querySelector("[name='test_question']");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    testHistory.push({ role: "user", content: question });
    render();
    try {
      const data = await api("chat", { method: "POST", body: { question, history: testHistory, session_token: testSession } });
      testSession = data.session_token || testSession;
      testHistory.push({ role: "assistant", content: data.answer || "" });
      render();
    } catch (err) {
      testHistory.push({ role: "assistant", content: "Fehler: " + err.message });
      render();
    }
  }

  function renderTraining() {
    const job = state.job || {};
    const total = Number(job.total || 0);
    const processed = Number(job.processed || 0);
    const pct = total ? Math.round((processed / total) * 100) : 0;
    const indexCount = state.settings
      ? Number(state.settings.index_count || 0)
      : Number(state.stats && state.stats.overview ? state.stats.overview.chunks || 0 : 0);
    const scopeText = permissions.canManageSensitive
      ? "Indexiert die im Tab <strong>Inhalte</strong> gewählten veröffentlichten Beiträge/Seiten und PDFs. Standardmäßig werden alle veröffentlichten Inhalte der aktivierten Post Types genommen. Keine Sitemap nötig."
      : "Indexiert die von einem Administrator freigegebenen veröffentlichten Beiträge/Seiten und PDFs. Keine Sitemap nötig.";
    return `
      <section class="aicb-panel">
        <div class="aicb-panel-head">
          <div><h2>Training aus WordPress-Inhalten</h2><p>${scopeText}</p>
          <p class="aicb-hint">Nach einem Plugin-Update lohnt sich ein neues Training: Tabellen, Listen und Überschriften bleiben jetzt erhalten und die Abschnitte überlappen sich, damit Details wie Preise, Zeiten und Bedingungen zuverlässig gefunden werden.</p></div>
          ${button("Jetzt komplett trainieren", { id: "aicb-start-training", primary: true, disabled: state.busy })}
        </div>
        <div class="aicb-metrics">
          <div><strong>${indexCount}</strong><span>Chunks im Index</span></div>
          <div><strong>${processed}/${total}</strong><span>Posts verarbeitet</span></div>
          <div><strong>${job.chunks || 0}</strong><span>Chunks im aktuellen Job</span></div>
          <div><strong>${job.status || "idle"}</strong><span>Status</span></div>
        </div>
        <div class="aicb-progress"><i style="width:${pct}%"></i></div>
        <div class="aicb-log">${(job.logs || ["Noch kein Training gestartet."]).map((line) => `<div>${escapeHtml(line)}</div>`).join("")}</div>
      </section>`;
  }

  function renderContent() {
    const c = state.content;
    if (!c) {
      return `<section class="aicb-panel"><h2>Inhalte auswählen</h2><p>Lade Inhalte ...</p></section>`;
    }
    const mode = c.mode === "selected" ? "selected" : "all";
    const groups = c.post_types || [];
    const pdfs = c.pdfs || [];
    const disabledAttr = mode === "all" ? "disabled" : "";

    const groupHtml = groups.length
      ? groups
          .map((g) => {
            const items = (g.items || [])
              .map(
                (it) => `
                <label class="aicb-content-item">
                  <input type="checkbox" name="content_post" class="aicb-post-${escapeHtml(g.name)}" value="${Number(it.id)}" ${it.selected ? "checked" : ""} ${disabledAttr}>
                  <span>${escapeHtml(it.title)}</span>
                  <a href="${escapeHtml(it.url || "#")}" target="_blank" rel="noreferrer">↗</a>
                </label>`
              )
              .join("");
            return `
              <div class="aicb-content-group">
                <h3>${escapeHtml(g.label)} <code>${escapeHtml(g.name)}</code> <span class="aicb-hint">(${Number(g.total)} veröffentlicht${g.truncated ? ", erste 300" : ""})</span></h3>
                <label class="aicb-content-toggle"><input type="checkbox" class="aicb-toggle-group" data-group="${escapeHtml(g.name)}" ${disabledAttr}> Alle in dieser Liste an-/abwählen</label>
                <div class="aicb-content-list">${items}</div>
              </div>`;
          })
          .join("")
      : "<p>Keine veröffentlichten Inhalte gefunden.</p>";

    const pdfHtml = pdfs.length
      ? pdfs
          .map(
            (p) => `<li data-id="${Number(p.id)}"><span>${escapeHtml(p.title)}</span> <a href="${escapeHtml(p.url || "#")}" target="_blank" rel="noreferrer">↗</a> <button type="button" class="button-link aicb-remove-pdf" data-id="${Number(p.id)}">entfernen</button></li>`
          )
          .join("")
      : "<li class='aicb-hint'>Noch keine PDFs ausgewählt.</li>";

    return `
      <section class="aicb-panel" id="aicb-content-form">
        <div class="aicb-panel-head">
          <div><h2>Inhalte für den Chatbot auswählen</h2>
          <p>Es werden ausschließlich <strong>veröffentlichte</strong> Inhalte gelistet. Entwürfe werden nie indexiert.</p></div>
        </div>

        <div class="aicb-checks">
          <label><input type="radio" name="index_mode" value="all" ${mode === "all" ? "checked" : ""}> Alle veröffentlichten Inhalte der aktivierten Post Types (Einstellungen)</label>
          <label><input type="radio" name="index_mode" value="selected" ${mode === "selected" ? "checked" : ""}> Nur die unten angekreuzten Inhalte</label>
        </div>

        <form id="aicb-content-search" class="aicb-inline">
          <input name="q" placeholder="Inhalte suchen" value="${escapeHtml(state.contentSearch || "")}">
          <button class="button" type="submit">Suchen</button>
        </form>

        ${groupHtml}

        <h2 style="margin-top:24px;">PDFs aus der Mediathek</h2>
        <p class="aicb-hint">Ausgewählte PDFs werden beim nächsten Training in die Wissensbasis aufgenommen. Gescannte Bild-PDFs ohne Textebene können nicht gelesen werden.</p>
        <p>${button("PDFs aus Mediathek wählen", { id: "aicb-pick-pdfs" })}</p>
        <ul class="aicb-pdf-list">${pdfHtml}</ul>

        <p style="margin-top:20px;">
          ${button("Auswahl speichern", { id: "aicb-save-content", primary: true })}
          ${button("Speichern & jetzt trainieren", { id: "aicb-save-train-content" })}
        </p>
      </section>`;
  }

  function renderSettings() {
    const s = state.settings || {};
    const postTypes = s.post_types || [];
    return `
      <form class="aicb-panel aicb-form" id="aicb-settings-form">
        <h2>Einstellungen</h2>
        <div class="aicb-grid two">
          ${field("OpenAI API Key", `<input name="openai_api_key" type="password" placeholder="${s.has_openai_api_key ? "Gespeichert. Leer lassen, um zu behalten." : "sk-..."}">`)}
          ${field("Chat Modell", `<input name="chat_model" value="${escapeHtml(s.chat_model || "gpt-4o-mini")}">`)}
          ${field("Embedding Modell", `<input name="embedding_model" value="${escapeHtml(s.embedding_model || "text-embedding-3-large")}">`)}
          ${field("Retriever K", `<input name="retriever_k" type="number" min="1" max="20" value="${Number(s.retriever_k || 8)}">`)}
          ${field("Max Context Chars", `<input name="max_context_chars" type="number" min="3000" value="${Number(s.max_context_chars || 14000)}">`)}
          ${field("Batch Size", `<input name="batch_size" type="number" min="1" max="20" value="${Number(s.batch_size || 4)}">`)}
          ${field("Kontakt URL", `<input name="contact_url" value="${escapeHtml(s.contact_url || "")}">`)}
          ${field("Kontakt E-Mail", `<input name="contact_email" value="${escapeHtml(s.contact_email || "")}">`)}
          ${field("Kontakt Telefon", `<input name="contact_phone" value="${escapeHtml(s.contact_phone || "")}">`)}
          ${field("Datenschutz URL", `<input name="privacy_url" value="${escapeHtml(s.privacy_url || "")}">`)}
        </div>
        <div class="aicb-checks">
          <label><input type="checkbox" name="auto_index_on_save" ${s.auto_index_on_save ? "checked" : ""}> Bei Speichern automatisch nachindexieren</label>
          <label><input type="checkbox" name="widget_enabled" ${s.widget_enabled ? "checked" : ""}> Floating Widget aktivieren</label>
          <label><input type="checkbox" name="include_excerpts" ${s.include_excerpts ? "checked" : ""}> Auszüge einbeziehen</label>
          <label><input type="checkbox" name="include_taxonomies" ${s.include_taxonomies ? "checked" : ""}> Taxonomien einbeziehen</label>
        </div>
        <div class="aicb-posttypes">
          <h3>Post Types für Training</h3>
          ${postTypes.map((pt) => `<label><input type="checkbox" name="enabled_post_types" value="${escapeHtml(pt.name)}" ${pt.selected ? "checked" : ""}> ${escapeHtml(pt.label)} <code>${escapeHtml(pt.name)}</code></label>`).join("")}
        </div>
        ${field("System Prompt", `<textarea name="system_prompt" rows="8">${escapeHtml(s.system_prompt || "")}</textarea>`)}
        <p>${button("Einstellungen speichern", { primary: true, submit: true })}</p>
      </form>`;
  }

  function renderWidget() {
    const w = state.widget || {};
    const t = w.theme || {};
    const c = w.copy || {};
    const g = w.greeting || {};
    const ps = w.page_suggestions || {};
    const hero = w.hero || {};
    const an = w.analytics || {};
    const topics = w.topics && w.topics.length ? w.topics : [{ label: "", question: "", url: "", highlight: false }];
    const colorFields = [
      ["accent", "Akzent"],
      ["accentStrong", "Akzent 2"],
      ["statusDot", "Status-Punkt"],
      ["launcherBg", "Chat-Button"],
      ["bg", "Hintergrund"],
      ["panel", "Panel"],
      ["text", "Text"],
      ["avatarBg", "Avatar-Kreis"],
      ["avatarFg", "Avatar-Symbol"],
      ["userBubble", "Nutzer-Bubble"],
      ["botBubble", "Bot-Bubble"],
      ["composerBg", "Eingabefeld"],
      ["composerBorder", "Eingabe-Rahmen"],
      ["composerButtonBg", "Sende-Button"],
      ["composerButtonText", "Sende-Symbol"],
    ];
    return `
      <div class="aicb-widget-layout">
      <form class="aicb-panel aicb-form" id="aicb-widget-form">
        <h2>Widget Design</h2>
        <p class="aicb-hint">Textfelder leer lassen: dann erscheinen sie automatisch in der Sprache der Website
        (Deutsch, Englisch, Französisch, Spanisch, Italienisch, Niederländisch, Portugiesisch, Türkisch,
        Polnisch, Russisch, Arabisch). Der Bot antwortet immer in der Sprache, in der gefragt wird.</p>
        <div class="aicb-grid two">
          ${field("Titel", `<input name="title" value="${escapeHtml(c.title || "")}">`)}
          ${field("Statuszeile", `<input name="status" value="${escapeHtml(c.status || "")}">`)}
          ${field("Intro", `<input name="intro" value="${escapeHtml(c.intro || "")}">`)}
          ${field("Placeholder", `<input name="placeholder" value="${escapeHtml(c.placeholder || "")}">`)}
          ${field("Themen-Überschrift", `<input name="topics_label" value="${escapeHtml(c.topics_label || "")}">`)}
          ${field("Datenschutz-Linktext", `<input name="privacy_label" value="${escapeHtml(c.privacy_label || "")}">`)}
        </div>
        ${field("Icon: Emoji oder SVG-Logo (leer = kein Avatar)", `<textarea name="icon" rows="3">${escapeHtml(c.icon || "")}</textarea>`)}
        ${field("Disclaimer", `<input name="disclaimer" value="${escapeHtml(c.disclaimer || "")}">`)}
        <div class="aicb-colors">${colorFields.map(([key, label]) => `<label><span>${label}</span><input type="color" name="${key}" value="${escapeHtml(t[key] || "#000000")}"></label>`).join("")}</div>
        <div class="aicb-checks">
          <label><input type="checkbox" name="greeting_enabled" ${g.enabled ? "checked" : ""}> Greeting aktivieren</label>
          <label><input type="checkbox" name="page_suggestions_enabled" ${ps.enabled !== false ? "checked" : ""}> Automatische Seitenfragen aktivieren</label>
          <label><input type="checkbox" name="page_suggestions_route_change" ${ps.show_on_route_change !== false ? "checked" : ""}> Bei Seitenwechsel automatisch anzeigen</label>
          <label><input type="checkbox" name="hide_in_hero" ${hero.hide_in_hero ? "checked" : ""}> Im Hero-Bereich ausblenden (erst danach einblenden)</label>
          <label><input type="checkbox" name="track_opens" ${an.track_opens !== false ? "checked" : ""}> Chat-Öffnungen zählen</label>
          <label><input type="checkbox" name="track_outcomes" ${an.track_outcomes !== false ? "checked" : ""}> Outcomes nach dem Chat zählen (Booking-Klick, Anfrageformular …)</label>
        </div>
        ${field("Hero-Bereich CSS-Selektor (optional, leer = erste Bildschirmhöhe)", `<input name="hero_selector" value="${escapeHtml(hero.selector || "")}" placeholder="z. B. .hero, #hero, section.hero">`)}
        <div class="aicb-grid two">
          ${field("Conversion-Links CSS-Selektor (optional, leer = Booking-Heuristik)", `<input name="conversion_selector" value="${escapeHtml(an.conversion_selector || "")}" placeholder="z. B. a.booking, .cta-termin">`)}
          ${field("Conversion-Formulare CSS-Selektor (optional, leer = Kontakt-Heuristik)", `<input name="form_selector" value="${escapeHtml(an.form_selector || "")}" placeholder="z. B. form.wpcf7-form, #kontakt form">`)}
        </div>
        <div class="aicb-grid two">
          ${field("Greeting Text", `<input name="greeting_text" value="${escapeHtml(g.text || "")}">`)}
          ${field("Greeting Delay", `<input type="number" name="greeting_delay_ms" value="${Number(g.delay_ms || 1200)}">`)}
        </div>
        <h3>Quick Topics</h3>
        <div id="aicb-topic-list">${topics.map((topic) => `
          <div class="aicb-row" data-topic-row>
            <input name="topic_label" placeholder="Label" value="${escapeHtml(topic.label || "")}">
            <input name="topic_question" placeholder="Frage" value="${escapeHtml(topic.question || "")}">
            <input name="topic_url" placeholder="URL statt Frage (optional)" value="${escapeHtml(topic.url || "")}">
            <label><input name="topic_highlight" type="checkbox" ${topic.highlight ? "checked" : ""}> Highlight</label>
            <button type="button" class="button" data-remove-row>Entfernen</button>
          </div>`).join("")}</div>
        <p>${button("Topic hinzufügen", { id: "aicb-add-topic" })} ${button("Widget speichern", { primary: true, submit: true })}</p>
      </form>
      <aside class="aicb-panel aicb-preview">
        <h2>Live-Vorschau</h2>
        <p class="aicb-hint">Änderungen erscheinen hier sofort. Gespeichert wird erst mit
        &bdquo;Widget speichern&ldquo;. Du kannst im Fenster auch echt chatten.</p>
        <div id="aicb-preview-host"></div>
        <p class="aicb-preview-actions">
          ${button("Beispielantwort zeigen", { id: "aicb-preview-demo" })}
          ${button("Vorschau leeren", { id: "aicb-preview-reset" })}
        </p>
      </aside>
      </div>`;
  }

  function renderFaqs() {
    const rows = state.faqs.length ? state.faqs : [{ question: "", answer: "" }];
    return `
      <form class="aicb-panel aicb-form" id="aicb-faq-form">
        <h2>FAQs</h2>
        <div id="aicb-faq-list">${rows.map((faq) => `
          <div class="aicb-faq-row" data-faq-row>
            <input name="faq_question" placeholder="Frage" value="${escapeHtml(faq.question || "")}">
            <textarea name="faq_answer" rows="4" placeholder="Antwort">${escapeHtml(faq.answer || "")}</textarea>
            <button type="button" class="button" data-remove-row>Entfernen</button>
          </div>`).join("")}</div>
        <p>${button("FAQ hinzufügen", { id: "aicb-add-faq" })} ${button("FAQs speichern", { primary: true, submit: true })}</p>
      </form>`;
  }

  function renderMemory() {
    const items = state.memory.items || [];
    return `
      <section class="aicb-panel">
        <div class="aicb-panel-head">
          <div><h2>Memory bearbeiten</h2><p>${Number(state.memory.total || 0)} Chunks im lokalen WordPress-Index.</p></div>
          <form id="aicb-memory-search" class="aicb-inline"><input name="q" placeholder="Suchen" value="${escapeHtml(state.memory.q || "")}"><button class="button">Suchen</button></form>
        </div>
        <div class="aicb-memory-list">${items.map((item) => `
          <div class="aicb-memory-item" data-memory-row data-id="${Number(item.id)}">
            <div class="aicb-memory-meta"><input name="memory_title" value="${escapeHtml(item.title || "")}"><a href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(item.source_type || "")}</a></div>
            <textarea name="memory_content" rows="5">${escapeHtml(item.content || "")}</textarea>
            <p><button type="button" class="button button-primary" data-save-memory>Speichern</button> <button type="button" class="button" data-delete-memory>Löschen</button></p>
          </div>`).join("") || "<p>Keine Chunks gefunden.</p>"}</div>
      </section>`;
  }

  function renderStats() {
    const s = state.stats || {};
    const o = s.overview || {};
    const fb = s.feedback || {};
    const en = s.engagement || {};
    const pct = (v) => (v === null || v === undefined ? "–" : `${v}%`);
    const num = (v) => Number(v || 0).toLocaleString("de-DE");
    const outcomeLabel = (l) => ({
      booking: "Booking-/Termin-Link", cta: "CTA-Link", form: "Anfrageformular",
      action: "Aktion (im Chat)", card: "Karte (im Chat)", source: "Quelle (im Chat)", link: "Link",
    }[l] || (l || "Sonstige"));

    const tile = (value, label, opts) => `
      <div class="aicb-stat${opts && opts.accent ? " aicb-stat-" + opts.accent : ""}">
        <span class="aicb-stat-value">${value}</span>
        <span class="aicb-stat-label">${label}</span>
        ${opts && opts.sub ? `<span class="aicb-stat-sub">${opts.sub}</span>` : ""}
      </div>`;
    const chartCard = (id, title, sub, wide) => `
      <div class="aicb-chart-card${wide ? " aicb-chart-wide" : ""}">
        <div class="aicb-chart-head"><h3>${title}</h3>${sub ? `<span>${sub}</span>` : ""}</div>
        <div class="aicb-chart-body"><canvas id="${id}"></canvas></div>
      </div>`;

    const topQ = s.top_questions || [];
    const maxTop = topQ.reduce((m, i) => Math.max(m, i.count), 0) || 1;
    const neg = s.negative || [];
    const outLabels = en.outcomes_by_label || [];
    const maxOut = outLabels.reduce((m, i) => Math.max(m, i.count), 0) || 1;
    const outUrls = en.top_outcome_urls || [];

    return `
      <section class="aicb-panel aicb-stats">
        <div class="aicb-stats-head">
          <div><h2>Statistiken</h2><p class="aicb-hint">Live-Auswertung der Chats, Antwortqualität und Nutzung.</p></div>
          <button type="button" class="button" id="aicb-stats-refresh">Aktualisieren</button>
        </div>

        <div class="aicb-stat-grid">
          ${tile(num(o.today_chats), "Chats heute")}
          ${tile(num(o.week_chats), "7 Tage")}
          ${tile(num(o.month_chats), "30 Tage")}
          ${tile(num(o.total_chats), "Gesamt")}
          ${tile(pct(o.answer_rate), "Antwortquote", { accent: "green" })}
          ${tile(pct(fb.satisfaction), "Zufriedenheit", { accent: "green", sub: `👍 ${num(fb.helpful)} · 👎 ${num(fb.not_helpful)}` })}
          ${tile(num(o.sessions), "Sessions", { sub: `${num(o.active_sessions)} aktiv` })}
          ${tile(num(o.avg_messages), "Ø Nachrichten / Session")}
          ${tile(num(en.opens_total), "Chat geöffnet", { sub: `heute ${num(en.opens_today)} · 7 T. ${num(en.opens_week)}` })}
          ${tile(num(en.outcomes_total), "Outcomes", { accent: "green", sub: en.conversion_rate === null || en.conversion_rate === undefined ? "nach dem Chat" : `${en.conversion_rate}% je Öffnung` })}
          ${tile(num(o.chunks), "Wissens-Chunks")}
        </div>

        <div class="aicb-chart-grid">
          ${chartCard("aicb-c-daily", "Chats pro Tag", "Letzte 30 Tage", true)}
          ${chartCard("aicb-c-answer", "Antwortquote", "Beantwortet vs. Fehler")}
          ${chartCard("aicb-c-feedback", "Feedback", "👍 / 👎 / offen")}
          ${chartCard("aicb-c-weekday", "Nach Wochentag", "Gesamt")}
          ${chartCard("aicb-c-hour", "Nach Tageszeit", "Stunde (lokal)", true)}
        </div>

        <div class="aicb-chart-card">
          <div class="aicb-chart-head"><h3>Top Fragen</h3><span>Häufigste Besucherfragen</span></div>
          <div class="aicb-topq">${
            topQ.length
              ? topQ
                  .map(
                    (i) => `
            <div class="aicb-topq-row">
              <span class="aicb-topq-label" title="${escapeHtml(i.question)}">${escapeHtml(i.question)}</span>
              <span class="aicb-topq-bar"><i style="width:${Math.max(6, Math.round((100 * i.count) / maxTop))}%"></i></span>
              <strong>${i.count}</strong>
            </div>`
                  )
                  .join("")
              : "<p>Noch keine Fragen.</p>"
          }</div>
        </div>

        <div class="aicb-chart-card">
          <div class="aicb-chart-head"><h3>👎 Negativ bewertete Gespräche</h3><span>${neg.length ? neg.length + (neg.length >= 50 ? "+" : "") + " zuletzt" : "keine"}</span></div>
          <p class="aicb-hint">Hier siehst du Antworten, die ein Besucher mit Daumen runter bewertet hat – so erkennst du, wo der Chatbot nachgebessert werden sollte (z. B. Inhalt fehlt, falsche Seite, unklare Antwort).</p>
          <div class="aicb-neg">${
            neg.length
              ? neg
                  .map(
                    (n) => `
            <div class="aicb-neg-item">
              <div class="aicb-neg-meta">👎 ${escapeHtml(n.created_at || "")}</div>
              <div class="aicb-neg-q">${escapeHtml(n.question || "")}</div>
              <div class="aicb-neg-a">${escapeHtml(n.answer || "")}</div>
            </div>`
                  )
                  .join("")
              : "<p>Noch keine negativen Bewertungen. 🎉</p>"
          }</div>
        </div>

        <div class="aicb-chart-card">
          <div class="aicb-chart-head"><h3>Outcomes nach dem Chat</h3><span>${num(en.outcomes_total)} gesamt${en.conversion_rate === null || en.conversion_rate === undefined ? "" : " · " + en.conversion_rate + "% je Öffnung"}</span></div>
          <p class="aicb-hint">Was Besucher nach der Chat-Nutzung getan haben – Klick auf einen Booking-/CTA-Link (im Chat oder auf der Seite) oder ein abgeschicktes Anfrageformular.</p>
          <div class="aicb-topq">${
            outLabels.length
              ? outLabels
                  .map(
                    (i) => `
            <div class="aicb-topq-row">
              <span class="aicb-topq-label">${escapeHtml(outcomeLabel(i.label))}</span>
              <span class="aicb-topq-bar"><i style="width:${Math.max(6, Math.round((100 * i.count) / maxOut))}%"></i></span>
              <strong>${i.count}</strong>
            </div>`
                  )
                  .join("")
              : "<p>Noch keine Outcomes erfasst.</p>"
          }</div>
          ${
            outUrls.length
              ? `<div class="aicb-neg" style="margin-top:12px">${outUrls
                  .map(
                    (u) => `
            <div class="aicb-neg-item">
              <div class="aicb-neg-q"><a href="${escapeHtml(u.url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(u.url)}</a></div>
              <div class="aicb-neg-meta">${u.count}× ausgelöst</div>
            </div>`
                  )
                  .join("")}</div>`
              : ""
          }
        </div>
      </section>`;
  }

  /* --- Statistik-Diagramme (Chart.js) ------------------------------------- */
  let statCharts = [];
  function destroyStatCharts() {
    statCharts.forEach((c) => { try { c.destroy(); } catch (e) {} });
    statCharts = [];
  }

  function mountStatsCharts() {
    if (typeof window.Chart === "undefined") return;
    destroyStatCharts();
    const s = state.stats;
    if (!s) return;
    const o = s.overview || {};
    const fb = s.feedback || {};

    const css = getComputedStyle(document.body);
    const accent = "#8c8875";
    const accentStrong = "#5f5748";
    const green = "#4f8a5b";
    const red = "#c0603f";
    const amber = "#c9a24b";
    const gridColor = "rgba(60, 53, 44, 0.08)";
    const textColor = "#7a7263";

    Chart.defaults.font.family = css.fontFamily || "sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = textColor;

    const axis = {
      x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, border: { display: false } },
      y: { beginAtZero: true, grid: { color: gridColor }, border: { display: false }, ticks: { precision: 0, maxTicksLimit: 5 } },
    };
    const barTip = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { padding: 10, cornerRadius: 8, displayColors: false } },
      scales: axis,
    };
    const doughnutOpts = {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "66%",
      plugins: {
        legend: { display: true, position: "bottom", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 14 } },
        tooltip: { padding: 10, cornerRadius: 8 },
      },
    };

    const make = (id, config) => {
      const el = document.getElementById(id);
      if (!el) return;
      statCharts.push(new Chart(el, config));
    };

    // Chats pro Tag (Fläche + beantwortet-Linie)
    const daily = s.daily || [];
    const dailyEl = document.getElementById("aicb-c-daily");
    if (dailyEl) {
      const g = dailyEl.getContext("2d").createLinearGradient(0, 0, 0, 240);
      g.addColorStop(0, "rgba(140,136,117,0.34)");
      g.addColorStop(1, "rgba(140,136,117,0.02)");
      statCharts.push(new Chart(dailyEl, {
        type: "line",
        data: {
          labels: daily.map((d) => d.label),
          datasets: [
            { label: "Chats", data: daily.map((d) => d.total), borderColor: accentStrong, backgroundColor: g, fill: true, tension: 0.35, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4 },
            { label: "Beantwortet", data: daily.map((d) => d.answered), borderColor: green, backgroundColor: "transparent", fill: false, tension: 0.35, borderWidth: 2, borderDash: [4, 3], pointRadius: 0, pointHoverRadius: 4 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { display: true, position: "bottom", labels: { boxWidth: 10, usePointStyle: true, padding: 14 } }, tooltip: { padding: 10, cornerRadius: 8 } },
          scales: axis,
        },
      }));
    }

    // Antwortquote (Donut)
    make("aicb-c-answer", {
      type: "doughnut",
      data: { labels: ["Beantwortet", "Fehler"], datasets: [{ data: [o.answered || 0, o.errors || 0], backgroundColor: [green, red], borderWidth: 0, hoverOffset: 4 }] },
      options: doughnutOpts,
    });

    // Feedback (Donut)
    make("aicb-c-feedback", {
      type: "doughnut",
      data: { labels: ["Hilfreich", "Nicht hilfreich", "Offen"], datasets: [{ data: [fb.helpful || 0, fb.not_helpful || 0, fb.unrated || 0], backgroundColor: [green, red, "#d9d2c6"], borderWidth: 0, hoverOffset: 4 }] },
      options: doughnutOpts,
    });

    // Wochentag (Balken)
    const wd = s.by_weekday || [];
    make("aicb-c-weekday", {
      type: "bar",
      data: { labels: wd.map((d) => d.label), datasets: [{ data: wd.map((d) => d.count), backgroundColor: accent, borderRadius: 6, maxBarThickness: 26 }] },
      options: barTip,
    });

    // Tageszeit (Balken)
    const hr = s.by_hour || [];
    make("aicb-c-hour", {
      type: "bar",
      data: { labels: hr.map((d) => d.label), datasets: [{ data: hr.map((d) => d.count), backgroundColor: amber, borderRadius: 4, maxBarThickness: 18 }] },
      options: barTip,
    });
  }

  async function reloadStats() {
    try {
      const stats = await api("admin/stats");
      set({ stats });
    } catch (err) {
      set({ error: err.message });
    }
  }

  function renderChat() {
    return `
      <section class="aicb-panel">
        <h2>Test Chat</h2>
        <div class="aicb-test-chat">
          <div class="aicb-test-messages">${testHistory.map((m) => `<div class="${m.role === "assistant" ? "bot" : "user"}">${escapeHtml(m.content)}</div>`).join("") || "<p>Noch keine Nachricht.</p>"}</div>
          <form id="aicb-test-form" class="aicb-inline"><input name="test_question" placeholder="Frage testen"><button class="button button-primary">Senden</button></form>
        </div>
      </section>`;
  }

  function renderActiveTab() {
    if (sensitiveTabs.includes(state.tab) && !permissions.canManageSensitive) {
      return renderTraining();
    }
    if (state.tab === "content") return renderContent();
    if (state.tab === "settings") return renderSettings();
    if (state.tab === "widget") return renderWidget();
    if (state.tab === "faqs") return renderFaqs();
    if (state.tab === "memory") return renderMemory();
    if (state.tab === "stats") return renderStats();
    if (state.tab === "chat") return renderChat();
    return renderTraining();
  }

  /* --- Live-Vorschau im Widget-Tab ---------------------------------------- */
  const THEME_KEYS = [
    "accent", "accentStrong", "statusDot", "launcherBg", "bg", "panel", "text",
    "avatarBg", "avatarFg", "userBubble", "botBubble", "composerBg",
    "composerBorder", "composerButtonBg", "composerButtonText",
  ];
  const COPY_KEYS = ["title", "status", "intro", "topics_label", "placeholder", "disclaimer", "privacy_label"];
  let previewTimer = null;

  // Wie in PHP: accentStrong -> --aicb-accent-strong
  function cssVarName(key) {
    return "--aicb-" + key.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
  }

  // Konfiguration aus dem Formular - leere Felder fallen auf das Sprachpaket zurück.
  function previewConfig() {
    const base = AICBAdmin.widgetConfig || {};
    const defaults = AICBAdmin.copyDefaults || {};
    const form = document.getElementById("aicb-widget-form");
    const cfg = JSON.parse(JSON.stringify(base));
    if (!form) return cfg;

    const data = Object.fromEntries(new FormData(form).entries());
    cfg.theme = cfg.theme || {};
    THEME_KEYS.forEach((key) => {
      if (data[key]) cfg.theme[key] = data[key];
    });
    cfg.copy = cfg.copy || {};
    cfg.copy.icon = data.icon !== undefined ? data.icon : cfg.copy.icon;
    COPY_KEYS.forEach((key) => {
      const value = (data[key] || "").trim();
      cfg.copy[key] = value || defaults[key] || "";
    });
    cfg.greeting = {
      enabled: Boolean(data.greeting_enabled),
      text: (data.greeting_text || "").trim() || defaults.greeting || "",
      delay_ms: Number(data.greeting_delay_ms || 1200),
    };
    cfg.page_suggestions = {
      enabled: Boolean(data.page_suggestions_enabled),
      show_on_route_change: Boolean(data.page_suggestions_route_change),
    };
    cfg.hero = {
      hide_in_hero: Boolean(data.hide_in_hero),
      selector: (data.hero_selector || "").trim(),
    };
    cfg.analytics = {
      track_opens: Boolean(data.track_opens),
      track_outcomes: Boolean(data.track_outcomes),
      conversion_selector: (data.conversion_selector || "").trim(),
      form_selector: (data.form_selector || "").trim(),
    };
    cfg.topics = [];
    form.querySelectorAll("[data-topic-row]").forEach((row) => {
      const label = row.querySelector("[name='topic_label']").value.trim();
      const question = row.querySelector("[name='topic_question']").value.trim();
      const url = row.querySelector("[name='topic_url']").value.trim();
      if (label && (question || url)) {
        cfg.topics.push({
          label,
          question,
          url,
          highlight: row.querySelector("[name='topic_highlight']").checked,
        });
      }
    });
    return cfg;
  }

  function mountPreview() {
    const host = document.getElementById("aicb-preview-host");
    if (!host || !window.AICBWidget || typeof window.AICBWidget.mount !== "function") return;
    const cfg = previewConfig();

    host.innerHTML = AICBAdmin.previewHtml || "";
    const shell = host.querySelector("[data-aicb-widget]");
    if (!shell) return;

    // Farben als CSS-Variablen, genau wie das Plugin sie im Frontend setzt
    THEME_KEYS.forEach((key) => {
      const value = cfg.theme && cfg.theme[key];
      if (value) shell.style.setProperty(cssVarName(key), value);
    });

    // Serverseitig gerenderte Texte an die Formularwerte anpassen
    const title = shell.querySelector(".aicb-title");
    if (title) title.textContent = cfg.copy.title;
    const statusRow = shell.querySelector(".aicb-status");
    if (statusRow) {
      const statusText = (cfg.copy.status || "").trim();
      statusRow.style.display = statusText ? "" : "none";
      const dot = statusRow.querySelector(".aicb-status-dot");
      statusRow.textContent = statusText;
      if (dot) statusRow.insertBefore(dot, statusRow.firstChild);
    }
    const intro = shell.querySelector("[data-aicb-intro] .aicb-bubble");
    if (intro) intro.textContent = cfg.copy.intro;
    const input = shell.querySelector("[data-aicb-input]");
    if (input) input.placeholder = cfg.copy.placeholder;
    const disclaimer = shell.querySelector(".aicb-disclaimer");
    if (disclaimer) {
      disclaimer.textContent = cfg.copy.disclaimer;
      disclaimer.style.display = cfg.copy.disclaimer ? "" : "none";
    }
    const privacy = shell.querySelector(".aicb-privacy");
    if (privacy) privacy.textContent = cfg.copy.privacy_label;

    window.AICBWidget.mount(shell, cfg);
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(mountPreview, 180);
  }

  function previewDemo() {
    const shell = document.querySelector("#aicb-preview-host [data-aicb-widget]");
    if (!shell || !shell.aicbPreview) return;
    const url = AICBAdmin.siteUrl || "https://example.com/";
    shell.aicbPreview.reset();
    shell.aicbPreview.demo({
      question: "Wie kann ich euch erreichen?",
      answer: "Unsere Rezeption ist **täglich von 8 bis 20 Uhr** erreichbar, telefonisch unter Tel. +43 5223 5855.\n\nQuellen:\n" + url,
      rich: {
        version: 1,
        cards: [{
          title: "Kontakt & Anfahrt",
          description: "Öffnungszeiten, Anreise und Parkplätze auf einen Blick.",
          details: ["Täglich 8-20 Uhr"],
          url: url,
        }],
        actions: [
          { label: "Anrufen", type: "link", url: "tel:+4352235855" },
          { label: "Kontaktseite", type: "link", url: url },
          { label: "Wie reise ich an", type: "question", question: "Wie komme ich am besten zu euch?" },
        ],
      },
    });
  }

  function render() {
    root.innerHTML = `
      <div class="aicb-shell">
        <header class="aicb-titlebar">
          <div><h1>AI Content Chatbot</h1><p>Standalone WordPress Plugin mit Training aus Pages, Posts und Custom Post Types.</p></div>
          <code>${escapeHtml(AICBAdmin.siteUrl)}</code>
        </header>
        <nav class="aicb-tabs">${tabs.map(([id, label]) => `<button type="button" class="${state.tab === id ? "active" : ""}" data-tab="${id}">${label}</button>`).join("")}</nav>
        ${state.notice ? `<div class="notice notice-success"><p>${escapeHtml(state.notice)}</p></div>` : ""}
        ${state.error ? `<div class="notice notice-error"><p>${escapeHtml(state.error)}</p></div>` : ""}
        ${state.busy ? `<div class="aicb-busy">Bitte warten...</div>` : ""}
        ${renderActiveTab()}
      </div>`;
    if (state.tab === "widget") mountPreview();
    if (state.tab === "stats") mountStatsCharts();
    else destroyStatCharts();
  }

  root.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (event.target.id === "aicb-stats-refresh") {
      event.preventDefault();
      reloadStats();
      return;
    }
    if (tab) {
      const target = tab.dataset.tab;
      if (sensitiveTabs.includes(target) && !permissions.canManageSensitive) return;
      set({ tab: target, notice: "", error: "" });
      if (target === "content" && !state.content) loadContent();
      return;
    }
    if (event.target.id === "aicb-start-training") startTraining();
    if (event.target.id === "aicb-pick-pdfs") {
      event.preventDefault();
      openPdfPicker();
    }
    if (event.target.id === "aicb-save-content") {
      event.preventDefault();
      saveContent(false);
    }
    if (event.target.id === "aicb-save-train-content") {
      event.preventDefault();
      saveContent(true);
    }
    if (event.target.matches(".aicb-remove-pdf")) {
      event.preventDefault();
      removePdf(event.target.dataset.id);
    }
    if (event.target.id === "aicb-add-topic") {
      event.preventDefault();
      document.getElementById("aicb-topic-list").insertAdjacentHTML("beforeend", `<div class="aicb-row" data-topic-row><input name="topic_label" placeholder="Label"><input name="topic_question" placeholder="Frage"><input name="topic_url" placeholder="URL statt Frage (optional)"><label><input name="topic_highlight" type="checkbox"> Highlight</label><button type="button" class="button" data-remove-row>Entfernen</button></div>`);
      schedulePreview();
    }
    if (event.target.id === "aicb-add-faq") {
      event.preventDefault();
      document.getElementById("aicb-faq-list").insertAdjacentHTML("beforeend", `<div class="aicb-faq-row" data-faq-row><input name="faq_question" placeholder="Frage"><textarea name="faq_answer" rows="4" placeholder="Antwort"></textarea><button type="button" class="button" data-remove-row>Entfernen</button></div>`);
    }
    if (event.target.id === "aicb-preview-demo") {
      event.preventDefault();
      previewDemo();
    }
    if (event.target.id === "aicb-preview-reset") {
      event.preventDefault();
      const shell = document.querySelector("#aicb-preview-host [data-aicb-widget]");
      if (shell && shell.aicbPreview) shell.aicbPreview.reset();
    }
    if (event.target.matches("[data-remove-row]")) {
      event.target.closest("[data-topic-row], [data-faq-row]").remove();
      schedulePreview();
    }
    if (event.target.matches("[data-delete-memory]")) deleteMemory(Number(event.target.closest("[data-memory-row]").dataset.id));
    if (event.target.matches("[data-save-memory]")) saveMemory(event.target.closest("[data-memory-row]"));
  });

  // Live-Vorschau: jede Eingabe im Widget-Formular sofort spiegeln
  root.addEventListener("input", (event) => {
    if (event.target.closest("#aicb-widget-form")) schedulePreview();
  });
  root.addEventListener("change", (event) => {
    if (event.target.closest("#aicb-widget-form")) schedulePreview();

    // Inhalte-Tab: Modusumschaltung aktiviert/deaktiviert die Einzelauswahl.
    if (event.target.matches("[name='index_mode']")) {
      const selected = event.target.value === "selected";
      root.querySelectorAll("[name='content_post'], .aicb-toggle-group").forEach((el) => {
        el.disabled = !selected;
      });
    }
    // "Alle in dieser Liste" schaltet die Checkboxen der Gruppe um.
    if (event.target.matches(".aicb-toggle-group")) {
      const group = event.target.closest(".aicb-content-group");
      if (group) {
        group.querySelectorAll("[name='content_post']").forEach((el) => {
          el.checked = event.target.checked;
        });
      }
    }
  });

  root.addEventListener("submit", (event) => {
    if (event.target.id === "aicb-settings-form") {
      event.preventDefault();
      saveSettings(event.target);
    }
    if (event.target.id === "aicb-widget-form") {
      event.preventDefault();
      saveWidget(event.target);
    }
    if (event.target.id === "aicb-faq-form") {
      event.preventDefault();
      saveFaqs(event.target);
    }
    if (event.target.id === "aicb-memory-search") {
      event.preventDefault();
      loadMemory(new FormData(event.target).get("q") || "");
    }
    if (event.target.id === "aicb-content-search") {
      event.preventDefault();
      loadContent(new FormData(event.target).get("q") || "");
    }
    if (event.target.id === "aicb-test-form") {
      event.preventDefault();
      sendTestChat(event.target);
    }
  });

  loadInitial();
})();
