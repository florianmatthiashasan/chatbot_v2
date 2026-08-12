/*
 * AI Content Chatbot - Widget-Logik
 * Gleiche Bedienung wie das florianmatthias-Chat-Widget: Themenliste in der
 * Startansicht, Markdown und Quellenblock in den Antworten, Karten mit
 * Aktionsbuttons, Anker-Scrolling und Begruessungs-Popup.
 */
(function () {
  if (!window.AICBWidget) return;

  var SESSION_KEY = "aicb_session_token";
  var HISTORY_KEY = "aicb_history";
  var TEASER_KEY = "aicb_teaser_seen";
  var HISTORY_LIMIT = 16;
  var SCROLL_TOP_OFFSET = 8;

  var cfg = window.AICBWidget.config || {};
  var copy = cfg.copy || {};
  var contact = cfg.contact || {};
  // Alle Systemtexte kommen sprachaufgeloest vom Server (Sprachpakete im Plugin).
  var strings = cfg.strings || {};

  var FALLBACK_STRINGS = {
    steps: ["Thinking ...", "Searching ...", "Drafting answer ..."],
    error: "Something went wrong: ",
    sources: "Sources",
    sources_labels: ["Sources", "Source", "Quellen", "Quelle"],
  };

  function str(key) {
    var value = strings[key];
    if (Array.isArray(value)) return value.length ? value : FALLBACK_STRINGS[key];
    return (value || "").toString().trim() || FALLBACK_STRINGS[key];
  }

  // Sprachcode der Seite - vollstaendig, damit das Backend jede Sprache kennt.
  function lang() {
    var value = (cfg.lang || document.documentElement.getAttribute("lang") || navigator.language || "en")
      .toString()
      .toLowerCase();
    return value.split("-")[0];
  }

  function text(key) {
    return (copy[key] || "").toString().trim();
  }

  /* --- REST -------------------------------------------------------------- */
  function api(path, body) {
    return fetch(window.AICBWidget.restUrl + path.replace(/^\/+/, ""), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || data.message || "Request failed");
        return data;
      });
    });
  }

  function readStore(store, key, fallback) {
    try {
      var raw = window[store].getItem(key);
      return raw === null ? fallback : raw;
    } catch (err) {
      return fallback;
    }
  }

  function writeStore(store, key, value) {
    try {
      window[store].setItem(key, value);
    } catch (err) { /* Privater Modus: dann eben ohne Persistenz */ }
  }

  function getHistory() {
    try {
      var value = JSON.parse(readStore("sessionStorage", HISTORY_KEY, "[]"));
      return Array.isArray(value) ? value.slice(-HISTORY_LIMIT) : [];
    } catch (err) {
      return [];
    }
  }

  function setHistory(history) {
    writeStore("sessionStorage", HISTORY_KEY, JSON.stringify(history.slice(-HISTORY_LIMIT)));
  }

  function ensureSession() {
    var current = readStore("localStorage", SESSION_KEY, "");
    if (current) return Promise.resolve(current);
    return api("session", {}).then(function (data) {
      if (data.token) writeStore("localStorage", SESSION_KEY, data.token);
      return data.token || "";
    });
  }

  /* --- Icons -------------------------------------------------------------- */
  function svgNode(paths, className, extra) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    if (className) svg.setAttribute("class", className);
    if (extra) {
      Object.keys(extra).forEach(function (key) { svg.setAttribute(key, extra[key]); });
    }
    paths.forEach(function (d) {
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }

  // Eigenes SVG des Betreibers wird bereinigt uebernommen, sonst Emoji/Text.
  function sanitizeSvg(markup) {
    var trimmed = (markup || "").trim();
    if (trimmed.toLowerCase().indexOf("<svg") !== 0) return null;
    try {
      var doc = new DOMParser().parseFromString(trimmed, "image/svg+xml");
      if (doc.querySelector("parsererror")) return null;
      var svg = doc.documentElement;
      if (!svg || svg.nodeName.toLowerCase() !== "svg") return null;
      ["script", "foreignObject", "iframe", "object", "embed", "link", "style"].forEach(function (tag) {
        Array.prototype.forEach.call(doc.querySelectorAll(tag), function (el) { el.remove(); });
      });
      Array.prototype.forEach.call(doc.querySelectorAll("*"), function (el) {
        Array.prototype.slice.call(el.attributes).forEach(function (attr) {
          var name = attr.name.toLowerCase();
          var value = (attr.value || "").trim();
          if (name.indexOf("on") === 0) el.removeAttribute(attr.name);
          if ((name === "href" || name === "xlink:href") && value && value.charAt(0) !== "#") {
            el.removeAttribute(attr.name);
          }
        });
      });
      svg.removeAttribute("width");
      svg.removeAttribute("height");
      svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
      return svg;
    } catch (err) {
      return null;
    }
  }

  function renderIcon(target, value) {
    if (!target) return;
    var trimmed = (value || "").trim();
    target.innerHTML = "";
    if (!trimmed) {
      target.style.display = "none";
      return;
    }
    var safeSvg = sanitizeSvg(trimmed);
    if (safeSvg) {
      target.appendChild(safeSvg);
    } else {
      target.textContent = trimmed;
    }
    target.style.display = "grid";
  }

  function actionIcon(url) {
    var lowered = (url || "").toLowerCase();
    if (lowered.indexOf("tel:") === 0) {
      return svgNode([
        "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.1 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z",
      ], "aicb-action-icon");
    }
    if (lowered.indexOf("mailto:") === 0) {
      return svgNode([
        "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
        "M3 7l9 6 9-6",
      ], "aicb-action-icon");
    }
    return svgNode([
      "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
      "M15 3h6v6",
      "M10 14L21 3",
    ], "aicb-action-icon");
  }

  /* --- Text: Links, Markdown, Quellenblock -------------------------------- */
  function urlRegex() {
    return /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;
  }

  function inlineMarkdownRegex() {
    return /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*(\S(?:[^\n]*?\S)?)\*\*|__(\S(?:[^\n]*?\S)?)__|`([^`\n]+)`|\*(\S(?:[^*\n]*?\S)?)\*/g;
  }

  // Quellen-Ueberschrift in allen Sprachen des Plugins erkennen.
  function sourcesHeadingRegex() {
    var labels = str("sources_labels").map(function (label) {
      return String(label).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    });
    return new RegExp("^\\s*(" + labels.join("|") + ")\\s*:\\s*(.*)$", "i");
  }

  var SOURCES_HEADING = sourcesHeadingRegex();

  function externalLink(label, href) {
    var anchor = document.createElement("a");
    anchor.href = href;
    anchor.textContent = label;
    anchor.target = "_blank";
    anchor.rel = "noreferrer noopener";
    return anchor;
  }

  function linkifyFragment(input) {
    var value = (input || "").toString();
    var fragment = document.createDocumentFragment();
    var pattern = urlRegex();
    var lastIndex = 0;
    var match;
    while ((match = pattern.exec(value)) !== null) {
      if (match.index > lastIndex) {
        fragment.appendChild(document.createTextNode(value.slice(lastIndex, match.index)));
      }
      var url = match[0];
      fragment.appendChild(externalLink(url, url.indexOf("http") === 0 ? url : "https://" + url));
      lastIndex = pattern.lastIndex;
    }
    if (lastIndex < value.length) {
      fragment.appendChild(document.createTextNode(value.slice(lastIndex)));
    }
    return fragment;
  }

  // Rendert **fett**, __fett__, *kursiv*, `code` und Markdown-Links als Elemente.
  function appendInlineMarkdown(target, input, depth) {
    var level = depth || 0;
    var value = (input || "").toString();
    if (level > 3) {
      target.appendChild(linkifyFragment(value));
      return;
    }
    var pattern = inlineMarkdownRegex();
    var lastIndex = 0;
    var match;
    while ((match = pattern.exec(value)) !== null) {
      if (match.index > lastIndex) {
        target.appendChild(linkifyFragment(value.slice(lastIndex, match.index)));
      }
      if (match[2]) {
        target.appendChild(externalLink(match[1], match[2]));
      } else if (match[3] || match[4]) {
        var strong = document.createElement("strong");
        appendInlineMarkdown(strong, match[3] || match[4], level + 1);
        target.appendChild(strong);
      } else if (match[5]) {
        var code = document.createElement("code");
        code.textContent = match[5];
        target.appendChild(code);
      } else if (match[6]) {
        var em = document.createElement("em");
        appendInlineMarkdown(em, match[6], level + 1);
        target.appendChild(em);
      }
      lastIndex = pattern.lastIndex;
    }
    if (lastIndex < value.length) {
      target.appendChild(linkifyFragment(value.slice(lastIndex)));
    }
  }

  function appendRichText(target, input) {
    var lines = (input || "").toString().split("\n");
    lines.forEach(function (line, idx) {
      if (idx > 0) target.appendChild(document.createTextNode("\n"));
      var heading = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
      if (heading) {
        var strong = document.createElement("strong");
        appendInlineMarkdown(strong, heading[2], 0);
        target.appendChild(strong);
        return;
      }
      appendInlineMarkdown(target, line, 0);
    });
  }

  // Trennt einen abschliessenden "Quellen:"-Block vom Antworttext ab.
  function splitSources(input) {
    var value = (input || "").toString();
    var lines = value.split("\n");
    for (var i = lines.length - 1; i >= 0; i--) {
      var match = lines[i].match(SOURCES_HEADING);
      if (!match) continue;
      var rest = [match[2]].concat(lines.slice(i + 1)).join("\n").trim();
      if (!/https?:\/\/|www\./i.test(rest)) break;
      return {
        body: lines.slice(0, i).join("\n").replace(/\s+$/, ""),
        label: match[1],
        sources: rest,
      };
    }
    return { body: value, label: "", sources: "" };
  }

  function setBubbleContent(bubble, value, plain) {
    bubble.innerHTML = "";
    if (plain) {
      bubble.appendChild(linkifyFragment(value));
      return;
    }
    var parts = splitSources(value);
    if (parts.body.trim()) {
      var body = document.createElement("div");
      appendRichText(body, parts.body);
      bubble.appendChild(body);
    }
    if (parts.sources) {
      var wrap = document.createElement("div");
      wrap.className = "aicb-sources";
      var label = document.createElement("div");
      label.className = "aicb-sources-label";
      label.textContent = (parts.label || str("sources")) + ":";
      wrap.appendChild(label);
      var list = document.createElement("div");
      appendRichText(list, parts.sources);
      wrap.appendChild(list);
      bubble.appendChild(wrap);
    }
  }

  function safeUrl(value) {
    var url = (value || "").toString().trim();
    return /^(https?:\/\/|mailto:|tel:|\/)/i.test(url) ? url : "";
  }

  /* --- Widget-Instanz ------------------------------------------------------ */
  function initShell(shell) {
    var launcher = shell.querySelector("[data-aicb-launcher]");
    var panel = shell.querySelector("[data-aicb-panel]");
    var messagesEl = shell.querySelector("[data-aicb-messages]");
    var listEl = shell.querySelector("[data-aicb-list]");
    var spacerEl = shell.querySelector("[data-aicb-spacer]");
    var introEl = shell.querySelector("[data-aicb-intro]");
    var topicsEl = shell.querySelector("[data-aicb-topics]");
    var topicsLabelEl = shell.querySelector("[data-aicb-topics-label]");
    var topicsListEl = shell.querySelector("[data-aicb-topics-list]");
    var formEl = shell.querySelector("[data-aicb-form]");
    var inputEl = shell.querySelector("[data-aicb-input]");
    var sendBtn = shell.querySelector("[data-aicb-send]");
    var minimizeBtn = shell.querySelector("[data-aicb-minimize]");
    var closeBtn = shell.querySelector("[data-aicb-close]");
    var teaserEl = shell.querySelector("[data-aicb-teaser]");
    var teaserTextEl = shell.querySelector("[data-aicb-teaser-text]");
    var teaserCloseEl = shell.querySelector("[data-aicb-teaser-close]");
    var inline = shell.classList.contains("aicb-mode-inline");

    if (!panel || !messagesEl || !listEl || !formEl || !inputEl) return;

    var icon = (copy.icon || "").trim();
    var anchorRow = null;
    var busy = false;
    var timers = [];

    Array.prototype.forEach.call(shell.querySelectorAll("[data-aicb-avatar]"), function (el) {
      renderIcon(el, icon);
    });

    /* --- Scrollen: die aktuelle Frage bleibt oben stehen ------------------ */
    function updateSpacer() {
      if (!spacerEl) return;
      spacerEl.style.height = "0px";
      if (!anchorRow || !anchorRow.isConnected) return;
      var below = messagesEl.scrollHeight - anchorRow.offsetTop;
      var missing = messagesEl.clientHeight - below - SCROLL_TOP_OFFSET;
      spacerEl.style.height = Math.max(0, Math.round(missing)) + "px";
    }

    function scrollAnchorToTop(smooth) {
      if (!anchorRow || !anchorRow.isConnected) return;
      var top = Math.max(0, anchorRow.offsetTop - SCROLL_TOP_OFFSET);
      if (typeof messagesEl.scrollTo === "function") {
        messagesEl.scrollTo({ top: top, behavior: smooth ? "smooth" : "auto" });
      } else {
        messagesEl.scrollTop = top;
      }
    }

    function keepAnchorInView(smooth) {
      updateSpacer();
      scrollAnchorToTop(smooth);
    }

    function scrollToBottom() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    /* --- Nachrichten ------------------------------------------------------ */
    function createRow(sender, nodes) {
      var row = document.createElement("div");
      row.className = "aicb-row aicb-" + sender;
      if (sender === "bot" && icon) {
        var avatar = document.createElement("div");
        avatar.className = "aicb-row-avatar";
        avatar.setAttribute("aria-hidden", "true");
        renderIcon(avatar, icon);
        row.appendChild(avatar);
      }
      var stack = document.createElement("div");
      stack.className = "aicb-stack";
      nodes.filter(Boolean).forEach(function (node) { stack.appendChild(node); });
      row.appendChild(stack);
      return row;
    }

    function appendRow(row) {
      var previous = listEl.lastElementChild;
      if (previous && previous.classList.contains("aicb-bot") && row.classList.contains("aicb-bot")) {
        row.classList.add("aicb-stacked");
      }
      listEl.appendChild(row);
      return row;
    }

    function createBubble(value, sender) {
      var bubble = document.createElement("div");
      bubble.className = "aicb-bubble";
      // dir=auto: eine arabische Antwort laeuft rechtsbuendig, auch wenn die
      // Seite links-nach-rechts ist.
      bubble.setAttribute("dir", "auto");
      setBubbleContent(bubble, value, sender === "user");
      return bubble;
    }

    function createCard(card) {
      var wrap = document.createElement("div");
      wrap.className = "aicb-card";

      var thumb = document.createElement("div");
      thumb.className = "aicb-card-thumb";
      var imageUrl = safeUrl(card.image_url);
      if (imageUrl) {
        var img = document.createElement("img");
        img.src = imageUrl;
        img.alt = card.title || "";
        img.loading = "lazy";
        thumb.appendChild(img);
      }
      wrap.appendChild(thumb);

      var body = document.createElement("div");
      body.className = "aicb-card-body";
      var cardUrl = safeUrl(card.url);
      var title = document.createElement(cardUrl ? "a" : "div");
      title.className = "aicb-card-title";
      title.setAttribute("dir", "auto");
      title.textContent = card.title || "";
      if (cardUrl) {
        title.href = cardUrl;
        title.target = "_blank";
        title.rel = "noreferrer noopener";
      }
      body.appendChild(title);

      if (card.description) {
        var desc = document.createElement("p");
        desc.className = "aicb-card-desc";
        desc.textContent = card.description;
        body.appendChild(desc);
      }

      if (Array.isArray(card.details) && card.details.length) {
        var details = document.createElement("div");
        details.className = "aicb-details";
        card.details.slice(0, 3).forEach(function (detail) {
          var item = document.createElement("span");
          item.className = "aicb-detail";
          item.textContent = detail;
          details.appendChild(item);
        });
        body.appendChild(details);
      }

      wrap.appendChild(body);
      return wrap;
    }

    // Link-Aktionen sind echte <a>: nur so uebergibt der Browser tel:/mailto:
    // zuverlaessig an Telefon- bzw. Mail-App.
    function createAction(action, isPrimary) {
      var url = safeUrl(action.url);
      var isLink = !!url && action.type !== "question";
      var el = document.createElement(isLink ? "a" : "button");
      el.className = "aicb-action" + (isPrimary ? " aicb-primary" : "");
      if (isLink) {
        el.href = url;
        el.rel = "noreferrer noopener";
        if (/^https?:/i.test(url)) el.target = "_blank";
        el.appendChild(actionIcon(url));
      } else {
        el.type = "button";
        el.addEventListener("click", function () {
          var next = (action.question || action.label || "").toString().trim();
          if (next) send(next);
        });
      }
      var label = document.createElement("span");
      label.textContent = action.label || action.question || "";
      el.appendChild(label);
      return el;
    }

    function richNodes(rich) {
      var nodes = [];
      if (!rich) return nodes;
      (rich.cards || []).slice(0, 2).forEach(function (card) {
        if (card && (card.title || card.description)) nodes.push(createCard(card));
      });
      var actions = (rich.actions || []).slice(0, 3);
      if (actions.length) {
        var row = document.createElement("div");
        row.className = "aicb-actions-row";
        actions.forEach(function (action, idx) {
          if (!action || (!action.label && !action.question)) return;
          row.appendChild(createAction(action, idx === 0));
        });
        nodes.push(row);
      }
      return nodes;
    }

    function addMessage(value, sender, rich) {
      var row = appendRow(createRow(sender, [createBubble(value, sender)].concat(richNodes(rich))));
      if (sender === "user") {
        anchorRow = row;
        keepAnchorInView(true);
      } else if (anchorRow) {
        keepAnchorInView(false);
      } else {
        scrollToBottom();
      }
      return row;
    }

    function setTyping(bubble, value) {
      bubble.innerHTML = "";
      var dots = document.createElement("span");
      dots.className = "aicb-typing-dots";
      for (var i = 0; i < 3; i++) dots.appendChild(document.createElement("i"));
      var label = document.createElement("span");
      label.textContent = value;
      bubble.appendChild(dots);
      bubble.appendChild(label);
    }

    function startTyping() {
      var steps = str("steps");
      var bubble = document.createElement("div");
      bubble.className = "aicb-bubble aicb-typing";
      bubble.setAttribute("dir", "auto");
      setTyping(bubble, steps[0]);
      var row = appendRow(createRow("bot", [bubble]));
      if (anchorRow) keepAnchorInView(true);
      else scrollToBottom();
      steps.slice(1).forEach(function (step, idx) {
        timers.push(setTimeout(function () {
          if (bubble.isConnected && bubble.classList.contains("aicb-typing")) setTyping(bubble, step);
        }, (idx + 1) * 1200));
      });
      return { row: row, bubble: bubble };
    }

    function finishTyping(entry, value, rich) {
      timers.forEach(clearTimeout);
      timers = [];
      if (!entry || !entry.bubble.isConnected) {
        addMessage(value, "bot", rich);
        return;
      }
      entry.bubble.classList.remove("aicb-typing");
      setBubbleContent(entry.bubble, value, false);
      var stack = entry.row.querySelector(".aicb-stack");
      richNodes(rich).forEach(function (node) { stack.appendChild(node); });
      if (anchorRow) keepAnchorInView(false);
      else scrollToBottom();
    }

    /* --- Startansicht ----------------------------------------------------- */
    function chevron() {
      return svgNode(["M9 6l6 6-6 6"], "aicb-chevron");
    }

    function renderTopics() {
      if (!topicsEl || !topicsListEl) return;
      var topics = Array.isArray(cfg.topics) ? cfg.topics : [];
      topicsListEl.innerHTML = "";
      if (!topics.length) {
        topicsEl.classList.add("aicb-hidden");
        return;
      }
      if (topicsLabelEl) {
        var label = text("topics_label");
        topicsLabelEl.textContent = label;
        topicsLabelEl.classList.toggle("aicb-hidden", !label);
      }
      topics.slice(0, 8).forEach(function (topic) {
        var label = (topic.label || "").toString().trim();
        var question = (topic.question || "").toString().trim();
        var url = safeUrl(topic.url);
        if (!label && !question) return;
        var row = document.createElement(url ? "a" : "button");
        row.className = "aicb-topic-row" + (topic.highlight ? " aicb-highlight" : "");
        if (url) {
          row.href = url;
          row.target = "_blank";
          row.rel = "noreferrer noopener";
        } else {
          row.type = "button";
          row.addEventListener("click", function () { send(question || label); });
        }
        var span = document.createElement("span");
        span.setAttribute("dir", "auto");
        span.textContent = label || question;
        row.appendChild(span);
        row.appendChild(chevron());
        topicsListEl.appendChild(row);
      });
      topicsEl.classList.remove("aicb-hidden");
    }

    function hideIntro() {
      if (introEl) introEl.classList.add("aicb-hidden");
    }

    function showIntro() {
      if (introEl) introEl.classList.remove("aicb-hidden");
    }

    /* --- Eingabe ---------------------------------------------------------- */
    function syncComposer() {
      var hasText = !!inputEl.value.trim();
      if (sendBtn) {
        sendBtn.classList.toggle("aicb-idle", !hasText);
        sendBtn.disabled = busy;
      }
      inputEl.style.height = "auto";
      inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + "px";
    }

    function setBusy(value) {
      busy = value;
      shell.classList.toggle("aicb-loading", value);
      if (sendBtn) sendBtn.disabled = value;
    }

    function send(value) {
      var question = (value || "").toString().trim();
      if (!question || busy) return;

      hideIntro();
      inputEl.value = "";
      syncComposer();
      addMessage(question, "user");
      var typing = startTyping();
      setBusy(true);

      var history = getHistory();
      ensureSession()
        .then(function (token) {
          return api("chat", {
            question: question,
            history: history,
            session_token: token,
            lang: lang(),
          });
        })
        .then(function (data) {
          if (data.session_token) writeStore("localStorage", SESSION_KEY, data.session_token);
          var answer = data.answer || "";
          finishTyping(typing, answer, data.rich);
          history.push({ role: "user", content: question });
          history.push({ role: "assistant", content: answer });
          setHistory(history);
        })
        .catch(function (err) {
          finishTyping(typing, str("error") + (err && err.message ? err.message : ""), null);
        })
        .then(function () {
          setBusy(false);
          syncComposer();
        });
    }

    /* --- Panel oeffnen und schliessen ------------------------------------- */
    function hideTeaser(remember) {
      if (teaserEl) teaserEl.classList.remove("aicb-teaser-visible");
      if (remember) writeStore("localStorage", TEASER_KEY, "1");
    }

    function open() {
      panel.hidden = false;
      shell.classList.add("aicb-open");
      hideTeaser(true);
      setTimeout(function () { inputEl.focus(); }, 50);
    }

    function close(reset) {
      if (inline) return;
      panel.hidden = true;
      shell.classList.remove("aicb-open");
      if (reset) {
        listEl.innerHTML = "";
        anchorRow = null;
        if (spacerEl) spacerEl.style.height = "0px";
        setHistory([]);
        showIntro();
      }
    }

    renderTopics();
    syncComposer();

    if (launcher) launcher.addEventListener("click", open);
    if (minimizeBtn) minimizeBtn.addEventListener("click", function () { close(false); });
    if (closeBtn) closeBtn.addEventListener("click", function () { close(true); });

    formEl.addEventListener("submit", function (event) {
      event.preventDefault();
      send(inputEl.value);
    });
    inputEl.addEventListener("input", syncComposer);
    inputEl.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send(inputEl.value);
      }
    });
    window.addEventListener("resize", updateSpacer);

    // Begruessungs-Popup: einmal pro Besucher.
    var greeting = cfg.greeting || {};
    if (!inline && teaserEl && greeting.enabled && (greeting.text || "").trim()) {
      if (readStore("localStorage", TEASER_KEY, "") !== "1") {
        if (teaserTextEl) teaserTextEl.textContent = greeting.text;
        setTimeout(function () {
          if (!shell.classList.contains("aicb-open")) teaserEl.classList.add("aicb-teaser-visible");
        }, Number(greeting.delay_ms || 1200));
      }
      teaserEl.addEventListener("click", open);
      if (teaserCloseEl) {
        teaserCloseEl.addEventListener("click", function (event) {
          event.stopPropagation();
          hideTeaser(true);
        });
      }
    }

    if (inline) {
      panel.hidden = false;
      shell.classList.add("aicb-open");
    }

    // Kleine API fuer die Live-Vorschau im Admin: Beispielnachrichten anzeigen,
    // ohne die Chat-API zu belasten.
    shell.aicbPreview = {
      demo: function (sample) {
        hideIntro();
        addMessage(sample.question, "user");
        addMessage(sample.answer, "bot", sample.rich);
      },
      reset: function () {
        listEl.innerHTML = "";
        anchorRow = null;
        if (spacerEl) spacerEl.style.height = "0px";
        messagesEl.scrollTop = 0;
        showIntro();
      },
    };
  }

  /**
   * Widget an einem Element starten. Mit overrideConfig laufen Vorschauen mit
   * ungespeicherten Werten - genutzt vom Widget-Tab im Admin.
   */
  window.AICBWidget.mount = function (element, overrideConfig) {
    if (!element) return;
    if (overrideConfig) {
      cfg = overrideConfig;
      copy = cfg.copy || {};
      contact = cfg.contact || {};
      strings = cfg.strings || {};
      SOURCES_HEADING = sourcesHeadingRegex();
    }
    initShell(element);
  };

  Array.prototype.forEach.call(document.querySelectorAll("[data-aicb-widget]"), initShell);
})();
