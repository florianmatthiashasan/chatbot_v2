/*
 * AI Content Chatbot - Widget-Logik
 * Gleiche Bedienung wie das florianmatthias-Chat-Widget: Themenliste in der
 * Startansicht, Markdown und Quellenblock in den Antworten, Karten mit
 * Aktionsbuttons, Anker-Scrolling und Begrüßungs-Popup.
 */
(function () {
  if (!window.AICBWidget) return;

  var SESSION_KEY = "aicb_session_token";
  var HISTORY_KEY = "aicb_history";
  var TEASER_KEY = "aicb_teaser_seen";
  var SUGGESTIONS_KEY = "aicb_page_suggestions_seen";
  var HISTORY_LIMIT = 16;
  var OFFERED_LIMIT = 10;
  var SCROLL_TOP_OFFSET = 8;

  var cfg = window.AICBWidget.config || {};
  var copy = cfg.copy || {};
  var contact = cfg.contact || {};
  // Alle Systemtexte kommen sprachaufgelöst vom Server (Sprachpakete im Plugin).
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

  // Sprachcode der Seite - vollständig, damit das Backend jede Sprache kennt.
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

  // Eigenes SVG des Betreibers wird bereinigt übernommen, sonst Emoji/Text.
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
      // Monochrome-Logos in der Theme-Farbe rendern: Schwarz -> currentColor,
      // fehlende Fuellung (Default-Schwarz) korrekt setzen, Outlines erhalten.
      var isBlack = function (value) {
        var v = (value || "").trim().toLowerCase().replace(/\s+/g, "");
        return v === "#000" || v === "#000000" || v === "black" || v === "rgb(0,0,0)" || v === "rgba(0,0,0,1)" || v === "#000000ff";
      };
      var isColor = function (value) {
        var v = (value || "").trim().toLowerCase();
        return v !== "" && v !== "none" && v !== "transparent" && v !== "currentcolor" && v.indexOf("url(") !== 0;
      };
      var DRAW = ["path", "circle", "rect", "ellipse", "line", "polyline", "polygon"];
      [svg].concat(Array.prototype.slice.call(doc.querySelectorAll("*"))).forEach(function (el) {
        // Explizite Farben in Attributen: Schwarz -> currentColor.
        ["fill", "stroke"].forEach(function (attrName) {
          if (isBlack(el.getAttribute(attrName))) el.setAttribute(attrName, "currentColor");
        });
        // Inline-Styles ebenfalls entschaerfen (fill/stroke).
        var style = el.getAttribute("style");
        if (style && /(fill|stroke)\s*:/i.test(style)) {
          style = style.replace(/(fill|stroke)\s*:\s*([^;]+)/gi, function (m, prop, val) {
            return isBlack(val) ? prop + ":currentColor" : (isColor(val) ? m : prop + ":" + val);
          });
          el.setAttribute("style", style);
        }
        // Zeichenelemente ohne Fuellung: Default waere Schwarz.
        if (DRAW.indexOf(el.nodeName.toLowerCase()) !== -1) {
          var hasFill = el.hasAttribute("fill") || /fill\s*:/i.test(style || "");
          var hasStroke = el.hasAttribute("stroke") || /stroke\s*:/i.test(style || "");
          if (!hasFill) {
            // Mit Kontur => Outline (fill=none), sonst gefuellt in Theme-Farbe.
            el.setAttribute("fill", hasStroke ? "none" : "currentColor");
          }
        }
      });
      // Wurzel ohne fill/stroke: als gefuellt in Theme-Farbe annehmen.
      if (!svg.hasAttribute("fill") && !/fill\s*:/i.test(svg.getAttribute("style") || "")) {
        svg.setAttribute("fill", "currentColor");
      }
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
    target.classList.remove("aicb-icon-svg", "aicb-icon-text");
    if (!trimmed) {
      target.style.display = "none";
      return;
    }
    var safeSvg = sanitizeSvg(trimmed);
    if (safeSvg) {
      target.appendChild(safeSvg);
      target.classList.add("aicb-icon-svg");
    } else {
      target.textContent = trimmed;
      target.classList.add("aicb-icon-text");
    }
    target.style.display = "grid";
  }

  function defaultLauncherIcon() {
    var svg = svgNode([
      "M12 3.75c-4.56 0-8.25 3.08-8.25 6.88 0 2.03 1.06 3.86 2.75 5.12l-.5 3.07 3.18-1.67c.88.23 1.83.36 2.82.36 4.56 0 8.25-3.08 8.25-6.88S16.56 3.75 12 3.75z",
      "M8.6 10.9h.01M12 10.9h.01M15.4 10.9h.01",
    ], "");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.55");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    if (svg.childNodes[1]) svg.childNodes[1].setAttribute("stroke-width", "2.1");
    var ns = "http://www.w3.org/2000/svg";
    var sparkle = document.createElementNS(ns, "path");
    sparkle.setAttribute("d", "M17.9 5.15l.45-1.15.45 1.15L20 5.6l-1.2.45-.45 1.15-.45-1.15-1.2-.45 1.2-.45z");
    sparkle.setAttribute("fill", "currentColor");
    sparkle.setAttribute("stroke", "none");
    svg.appendChild(sparkle);
    return svg;
  }

  function renderLauncherIcon(target, value) {
    if (!target) return;
    var trimmed = (value || "").trim();
    target.innerHTML = "";
    target.classList.remove("aicb-icon-svg", "aicb-icon-text");
    if (!trimmed) {
      target.appendChild(defaultLauncherIcon());
      target.classList.add("aicb-icon-svg");
      target.style.display = "grid";
      return;
    }
    renderIcon(target, trimmed);
  }

  function greetingKey(greeting) {
    var text = (greeting && greeting.text ? greeting.text : "").trim();
    var delay = Number(greeting && greeting.delay_ms ? greeting.delay_ms : 1200);
    return [text, delay].join("|");
  }

  function pageSignal() {
    var bits = [];
    var meta = document.querySelector('meta[name="description"]');
    var canonical = document.querySelector('link[rel="canonical"]');
    if (meta && meta.content) bits.push(meta.content);
    Array.prototype.slice.call(document.querySelectorAll("h1, h2"), 0, 5).forEach(function (el) {
      var value = (el.textContent || "").trim();
      if (value) bits.push(value);
    });
    return {
      url: (canonical && canonical.href ? canonical.href : window.location.href).split("#")[0],
      title: document.title || "",
      page_text: bits.join("\n").slice(0, 1400),
      lang: lang(),
    };
  }

  function suggestionsKey(items, page) {
    var questions = items.map(function (item) { return (item.question || "").trim(); }).join("|");
    return [page.url, questions].join("|");
  }

  // Kurze Ueberschrift ueber den Fragen im Teaser (falls kein Greeting-Text gesetzt).
  function suggestLeadLabel() {
    var map = {
      de: "Fragen zu dieser Seite", en: "Questions about this page",
      fr: "Questions sur cette page", es: "Preguntas sobre esta página",
      it: "Domande su questa pagina", nl: "Vragen over deze pagina",
      pt: "Perguntas sobre esta página", tr: "Bu sayfa hakkında sorular",
      pl: "Pytania o tę stronę", ru: "Вопросы об этой странице", ar: "أسئلة حول هذه الصفحة",
    };
    return map[lang()] || map.en;
  }

  // Erkennt Seitenwechsel ohne kompletten Reload (History-API-Themes/Page-Builder,
  // AJAX-Navigation) und ruft cb auf. Ein Poll-Fallback faengt Faelle ohne History-API.
  var routeEventsInstalled = false;
  function installRouteEvents() {
    if (routeEventsInstalled) return;
    routeEventsInstalled = true;
    var fire = function () {
      try { window.dispatchEvent(new Event("aicb:locationchange")); } catch (err) { /* alte Browser */ }
    };
    ["pushState", "replaceState"].forEach(function (method) {
      var original = window.history && window.history[method];
      if (typeof original !== "function") return;
      window.history[method] = function () {
        var result = original.apply(this, arguments);
        fire();
        return result;
      };
    });
    window.addEventListener("popstate", fire);
  }

  function onLocationChange(cb) {
    installRouteEvents();
    window.addEventListener("aicb:locationchange", cb);
    var lastHref = window.location.href;
    window.setInterval(function () {
      if (window.location.href !== lastHref) {
        lastHref = window.location.href;
        cb();
      }
    }, 1200);
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

  /* --- Feedback (War das hilfreich?) -------------------------------------- */
  function fbLabel(key) {
    var fb = (strings && strings.feedback) || {};
    var fallback = {
      question: "War das hilfreich?",
      yes: "Hilfreich",
      no: "Nicht hilfreich",
      thanks: "Danke für dein Feedback!",
    };
    return (fb[key] || "").toString().trim() || fallback[key];
  }

  function thumbIcon(up) {
    var d = up
      ? "M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"
      : "M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17";
    return svgNode([d], "aicb-feedback-icon");
  }

  // Feedback-Leiste unter einer Antwort. Ein Klick sendet die Bewertung einmalig.
  function feedbackRow(eventId) {
    var wrap = document.createElement("div");
    wrap.className = "aicb-feedback";

    var question = document.createElement("span");
    question.className = "aicb-feedback-q";
    question.textContent = fbLabel("question");
    wrap.appendChild(question);

    var voted = false;
    function makeButton(up) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "aicb-feedback-btn aicb-feedback-" + (up ? "up" : "down");
      btn.setAttribute("aria-label", up ? fbLabel("yes") : fbLabel("no"));
      btn.appendChild(thumbIcon(up));
      btn.addEventListener("click", function () {
        if (voted) return;
        voted = true;
        wrap.classList.add("aicb-voted");
        btn.classList.add("aicb-chosen");
        var token = readStore("localStorage", SESSION_KEY, "");
        api("feedback", { event_id: eventId, value: up ? 1 : -1, session_token: token }).catch(function () {});
        var thanks = document.createElement("span");
        thanks.className = "aicb-feedback-thanks";
        thanks.textContent = fbLabel("thanks");
        wrap.appendChild(thanks);
      });
      return btn;
    }
    wrap.appendChild(makeButton(true));
    wrap.appendChild(makeButton(false));
    return wrap;
  }

  /* --- Text: Links, Markdown, Quellenblock -------------------------------- */
  function urlRegex() {
    return /\b(?:https?:\/\/|www\.)[^\s<>()]+/gi;
  }

  function inlineMarkdownRegex() {
    return /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*(\S(?:[^\n]*?\S)?)\*\*|__(\S(?:[^\n]*?\S)?)__|`([^`\n]+)`|\*(\S(?:[^*\n]*?\S)?)\*/g;
  }

  // Quellen-Überschrift in allen Sprachen des Plugins erkennen.
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

  // Trennt einen abschließenden "Quellen:"-Block vom Antworttext ab.
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
    // Bereits gezeigte Buttons: gehen mit, damit der Bot nicht dieselbe
    // Empfehlung zweimal hintereinander anbietet.
    var offeredActions = [];
    var anchorRow = null;
    var busy = false;
    var timers = [];
    var teaserStorageKey = TEASER_KEY;
    var teaserMemoryValue = "1";
    var teaserStore = "localStorage"; // Greeting: dauerhaft, Fragen: pro Session
    var pageQuestions = [];        // KI-generierte Fragen zur aktuellen Seite
    var lastSuggestUrl = "";       // URL, fuer die zuletzt Fragen geladen wurden
    var routeTimer = null;

    Array.prototype.forEach.call(shell.querySelectorAll("[data-aicb-avatar]"), function (el) {
      renderIcon(el, icon);
    });
    renderLauncherIcon(shell.querySelector("[data-aicb-launcher-icon], .aicb-launcher-icon"), icon);

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
      // dir=auto: eine arabische Antwort läuft rechtsbündig, auch wenn die
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

    // Link-Aktionen sind echte <a>: nur so übergibt der Browser tel:/mailto:
    // zuverlässig an Telefon- bzw. Mail-App.
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
          var label = (action.label || "").toString().trim();
          if (label && offeredActions.indexOf(label) === -1) offeredActions.push(label);
          row.appendChild(createAction(action, idx === 0));
        });
        while (offeredActions.length > OFFERED_LIMIT) offeredActions.shift();
        nodes.push(row);
      }
      return nodes;
    }

    function addMessage(value, sender, rich, eventId) {
      var extra = richNodes(rich);
      if (sender === "bot" && eventId) extra.push(feedbackRow(eventId));
      var row = appendRow(createRow(sender, [createBubble(value, sender)].concat(extra)));
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

    function finishTyping(entry, value, rich, eventId) {
      timers.forEach(clearTimeout);
      timers = [];
      if (!entry || !entry.bubble.isConnected) {
        addMessage(value, "bot", rich, eventId);
        return;
      }
      entry.bubble.classList.remove("aicb-typing");
      setBubbleContent(entry.bubble, value, false);
      var stack = entry.row.querySelector(".aicb-stack");
      richNodes(rich).forEach(function (node) { stack.appendChild(node); });
      if (eventId) stack.appendChild(feedbackRow(eventId));
      if (anchorRow) keepAnchorInView(false);
      else scrollToBottom();
    }

    /* --- Startansicht ----------------------------------------------------- */
    function chevron() {
      return svgNode(["M9 6l6 6-6 6"], "aicb-chevron");
    }

    function renderTopics() {
      if (!topicsEl || !topicsListEl) return;
      // KI-Fragen zur aktuellen Seite zuerst, danach die im Admin gepflegten Themen.
      var aiRows = (pageQuestions || [])
        .map(function (item) {
          var q = (item && item.question ? item.question : "").toString().trim();
          return q ? { label: "", question: q, url: "", highlight: false, ai: true } : null;
        })
        .filter(Boolean);
      var topics = aiRows.concat(Array.isArray(cfg.topics) ? cfg.topics : []);
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
        row.className = "aicb-topic-row" + (topic.highlight ? " aicb-highlight" : "") + (topic.ai ? " aicb-topic-ai" : "");
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
            offered: offeredActions.slice(-OFFERED_LIMIT),
            session_token: token,
            lang: lang(),
          });
        })
        .then(function (data) {
          if (data.session_token) writeStore("localStorage", SESSION_KEY, data.session_token);
          var answer = data.answer || "";
          finishTyping(typing, answer, data.rich, data.event_id);
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

    /* --- Panel öffnen und schließen ------------------------------------- */
    function hideTeaser(remember) {
      if (teaserEl) teaserEl.classList.remove("aicb-teaser-visible");
      if (remember) writeStore(teaserStore, teaserStorageKey, teaserMemoryValue);
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
        offeredActions = [];
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

    function showTeaserAfter(delay) {
      setTimeout(function () {
        if (!shell.classList.contains("aicb-open")) teaserEl.classList.add("aicb-teaser-visible");
      }, Number(delay || 1200));
    }

    function clearTeaserContent() {
      if (!teaserTextEl) return;
      teaserTextEl.innerHTML = "";
      teaserEl.classList.remove("aicb-teaser-questions");
    }

    function showGreetingTeaser() {
      var greeting = cfg.greeting || {};
      if (inline || !teaserEl || !greeting.enabled || !(greeting.text || "").trim()) return false;
      var currentGreetingKey = greetingKey(greeting);
      teaserStore = "localStorage";
      teaserStorageKey = TEASER_KEY;
      teaserMemoryValue = currentGreetingKey;
      if (readStore("localStorage", TEASER_KEY, "") === currentGreetingKey) return true;
      clearTeaserContent();
      if (teaserTextEl) teaserTextEl.textContent = greeting.text;
      showTeaserAfter(Number(greeting.delay_ms || 1200));
      return true;
    }

    function showQuestionTeaser(items, page) {
      if (inline || !teaserEl || !teaserTextEl || !Array.isArray(items) || !items.length) return false;
      var currentKey = suggestionsKey(items, page);
      // Fragen-Popup pro Browser-Session einmal je Seite -> zuverlaessig sichtbar,
      // aber nicht bei jedem Klick erneut aufdringlich.
      teaserStore = "sessionStorage";
      teaserStorageKey = SUGGESTIONS_KEY;
      teaserMemoryValue = currentKey;
      if (readStore("sessionStorage", SUGGESTIONS_KEY, "") === currentKey) return true;

      clearTeaserContent();
      teaserEl.classList.add("aicb-teaser-questions");
      // Kurze Lead-Zeile: Greeting-Text, sonst lokalisierte Ueberschrift.
      var lead = ((cfg.greeting && cfg.greeting.text) || "").toString().trim() || suggestLeadLabel();
      if (lead) {
        var leadEl = document.createElement("div");
        leadEl.className = "aicb-teaser-lead";
        leadEl.textContent = lead;
        teaserTextEl.appendChild(leadEl);
      }
      items.slice(0, 3).forEach(function (item) {
        var question = (item.question || "").toString().trim();
        if (!question) return;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "aicb-teaser-question";
        btn.textContent = question;
        btn.addEventListener("click", function (event) {
          event.preventDefault();
          event.stopPropagation();
          hideTeaser(true);
          open();
          setTimeout(function () { send(question); }, 80);
        });
        teaserTextEl.appendChild(btn);
      });
      if (!teaserTextEl.children.length) return false;
      showTeaserAfter(Number((cfg.greeting && cfg.greeting.delay_ms) || 1200));
      return true;
    }

    var greetingOn = !cfg.greeting || cfg.greeting.enabled;

    // Ergebnis der KI-Fragen anwenden: immer in die Topics-Liste; und (wenn Chat
    // zu) ins Popup. Das Fragen-Popup erscheint zuverlaessig, auch wenn das
    // klassische Greeting deaktiviert ist - das Greeting ist nur der Fallback.
    function applySuggestions(items, page) {
      pageQuestions = (Array.isArray(items) ? items : []).slice(0, 3);
      renderTopics();
      if (inline || !teaserEl || shell.classList.contains("aicb-open")) return;
      if (showQuestionTeaser(pageQuestions, page)) return;
      if (greetingOn) showGreetingTeaser();
    }

    function currentPath() {
      return window.location.pathname + window.location.search;
    }

    function loadSuggestions() {
      lastSuggestUrl = currentPath();
      var page = pageSignal();
      api("suggestions", page)
        .then(function (data) {
          applySuggestions(Array.isArray(data.questions) ? data.questions : [], page);
        })
        .catch(function () {
          if (!inline && teaserEl && greetingOn && !shell.classList.contains("aicb-open")) showGreetingTeaser();
        });
    }

    // Bei Seitenwechsel ohne Full-Reload (SPA/AJAX-Themes): Fragen neu laden.
    function onRouteChange() {
      if (currentPath() === lastSuggestUrl) return;
      pageQuestions = [];
      renderTopics();
      if (teaserEl) teaserEl.classList.remove("aicb-teaser-visible");
      loadSuggestions();
    }

    if (!inline && teaserEl) {
      teaserEl.addEventListener("click", open);
      if (teaserCloseEl) {
        teaserCloseEl.addEventListener("click", function (event) {
          event.stopPropagation();
          hideTeaser(true);
        });
      }
    }

    // Nicht in der Admin-Live-Vorschau feuern (spart OpenAI-Aufrufe).
    // Topics bekommen KI-Fragen auch im Inline-Modus; Teaser nur floating.
    if (typeof window.AICBAdmin === "undefined") {
      loadSuggestions();
      onLocationChange(function () {
        clearTimeout(routeTimer);
        routeTimer = setTimeout(onRouteChange, 450);
      });
    }

    if (inline) {
      panel.hidden = false;
      shell.classList.add("aicb-open");
    }

    // Kleine API für die Live-Vorschau im Admin: Beispielnachrichten anzeigen,
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
