/* ALIBI Live Desk (CLAUDE.md section 14, step 3). Plain JS, no build step, works offline.
 *
 * - /api/live/feed (SSE): a new investigation (Telegram, upload, demo, re-run) opens automatically.
 * - /api/investigations/{id}/events (SSE): everything on screen comes from these events, drawn the
 *   moment each one arrives, in the order the pipeline wrote them. No timers, no staged delays:
 *   the only motion is a short CSS fade when an element appears.
 * - Open a specific case with /live?id=inv_..., press I (or Esc) for the idle screen.
 */
(function () {
  "use strict";

  var WITNESSES = [
    ["metadata", "Metadata"], ["weather", "Weather"], ["prior_appearance", "Prior appearance"],
    ["lipsync", "Lip-sync"], ["enf", "Power-grid hum"], ["ela", "ELA"]
  ];
  var LABEL = {};
  WITNESSES.forEach(function (w) { LABEL[w[0]] = w[1]; });

  var STATUS = {   // section 9 vocabulary + the bot's icons
    contradiction:       { icon: "⚠️", text: "Contradiction" },
    consistent:          { icon: "✅", text: "Consistent" },
    inconclusive:        { icon: "❔", text: "Inconclusive" },
    insufficient_signal: { icon: "❔", text: "Insufficient signal" },
    not_applicable:      { icon: "➖", text: "Not applicable" }
  };
  var RANK = { contradiction: 4, consistent: 3, inconclusive: 2, insufficient_signal: 1, not_applicable: 0 };
  var RELATION = { contradiction: "contradicts", consistent: "supports", inconclusive: "inconclusive",
                   insufficient_signal: "inconclusive" };
  var CLAIM_RANK = { contradiction: 3, inconclusive: 2, insufficient_signal: 2, consistent: 1 };
  var CLAIM_TYPE = { location: "Place", time: "Time", weather_condition: "Event", event: "Event",
                     speaker: "Speaker", other: "Claim" };
  var COUNT_ORDER = [["contradiction", "contradiction", "contradictions"], ["consistent", "consistent", "consistent"],
                     ["inconclusive", "inconclusive", "inconclusive"],
                     ["insufficient_signal", "insufficient signal", "insufficient signal"],
                     ["not_applicable", "not applicable", "not applicable"]];
  var RED = "#ff5a5a", GREEN = "#3ddc84", GREY = "#a3acbb", MUTED = "#5d6676", ACCENT = "#39c5f2";

  var $ = function (id) { return document.getElementById(id); };
  var current = null;   // the case on screen: {id, es, cy, claims, findings, inputs, parentId}

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function fileUrl(invId, path) {
    if (!path) return "";
    if (/^(https?:)?\//.test(path)) return path;
    return "/api/files/" + encodeURIComponent(invId) + "/" + path.split("/").map(encodeURIComponent).join("/");
  }
  function worstStatus(findings) {
    var best = null;
    findings.forEach(function (f) { if (best === null || RANK[f.status] > RANK[best]) best = f.status; });
    return best || "inconclusive";
  }
  function headlineClass(h) {
    if (h === "context_not_supported" || h === "signals_inconsistent" || h === "partially_supported") return "h-red";
    if (h === "consistent_so_far") return "h-green";
    return "h-grey";
  }
  function showIdle(on) { $("idle").hidden = !on; $("case").hidden = on || !current; }

  // ------------------------------------------------------------------ idle screen
  function loadIdle() {
    fetch("/api/live/info").then(function (r) { return r.json(); }).then(function (info) {
      if (!info.bot_username) return;
      $("bot-name").textContent = "@" + info.bot_username;
      if (window.qrcode && info.bot_url) {
        var qr = window.qrcode(0, "M");
        qr.addData(info.bot_url);
        qr.make();
        $("qr").innerHTML = qr.createSvgTag(8, 2);
        $("qr").hidden = false;
      }
    }).catch(function () { /* offline: generic text stays */ });
  }

  // ------------------------------------------------------------------ graph
  function makeGraph() {
    return cytoscape({
      container: $("graph"),
      elements: [],
      layout: { name: "preset" },
      userZoomingEnabled: false, userPanningEnabled: false, boxSelectionEnabled: false, autoungrabify: true,
      style: [
        { selector: "node", style: {
            "label": "data(label)", "color": "#eef2f7", "font-size": 15, "font-weight": 700,
            "text-valign": "center", "text-halign": "center", "text-wrap": "wrap", "text-max-width": 150,
            "width": 170, "height": 46, "shape": "round-rectangle", "background-color": "#161b24",
            "border-width": 2, "border-color": "#2a3140" } },
        { selector: "node.input", style: { "background-color": "#1d2430", "border-color": ACCENT, "width": 150 } },
        { selector: "node.claim", style: { "border-color": ACCENT } },
        { selector: "node.claim.contradiction", style: { "border-color": RED, "background-color": "#2a1416" } },
        { selector: "node.claim.consistent", style: { "border-color": GREEN, "background-color": "#11261a" } },
        { selector: "node.claim.inconclusive", style: { "border-color": GREY } },
        { selector: "node.witness", style: { "shape": "ellipse", "width": 175, "height": 54 } },
        { selector: "node.witness.contradiction", style: { "border-color": RED, "background-color": "#2a1416", "border-width": 3 } },
        { selector: "node.witness.consistent", style: { "border-color": GREEN, "background-color": "#11261a", "border-width": 3 } },
        { selector: "node.witness.inconclusive", style: { "border-color": GREY } },
        { selector: "node.witness.insufficient_signal", style: { "border-color": GREY, "border-style": "dashed" } },
        { selector: "node.witness.not_applicable", style: { "border-color": MUTED, "color": MUTED } },
        { selector: "edge", style: { "width": 3, "curve-style": "bezier", "target-arrow-shape": "triangle",
            "line-color": GREY, "target-arrow-color": GREY, "arrow-scale": 1.2 } },
        { selector: "edge.contradicts", style: { "line-color": RED, "target-arrow-color": RED, "width": 4 } },
        { selector: "edge.supports", style: { "line-color": GREEN, "target-arrow-color": GREEN, "width": 4 } },
        { selector: "edge.inconclusive", style: { "line-style": "dashed", "line-color": GREY, "target-arrow-color": GREY } },
        { selector: "edge.derived_from", style: { "line-color": "#3a4252", "target-arrow-color": "#3a4252", "width": 2 } }
      ]
    });
  }

  function place(cy) {   // three columns: inputs | claims | witnesses
    var cols = { input: 0, claim: 330, witness: 660 };
    ["input", "claim", "witness"].forEach(function (type) {
      var nodes = cy.nodes("." + type);
      var gap = type === "witness" ? 70 : 90;
      nodes.forEach(function (n, i) {
        n.position({ x: cols[type], y: (i - (nodes.length - 1) / 2) * gap });
      });
    });
    cy.fit(undefined, 25);
  }

  function addNode(cy, id, type, label, status) {
    if (cy.getElementById(id).length) return;
    cy.add({ group: "nodes", data: { id: id, label: label }, classes: type + (status ? " " + status : "") });
  }
  function addEdge(cy, source, target, relation) {
    var id = source + "->" + target;
    if (cy.getElementById(id).length || !cy.getElementById(source).length || !cy.getElementById(target).length) return;
    cy.add({ group: "edges", data: { id: id, source: source, target: target }, classes: relation });
  }

  function claimLabel(c) { return c.value.charAt(0).toUpperCase() + c.value.slice(1); }

  function refreshClaimColours() {   // claim colour = worst status pointing at it (as the backend graph does)
    var worst = {};
    current.findings.forEach(function (f) {
      if (f.status === "not_applicable") return;
      (f.claim_ids || []).forEach(function (cid) {
        if (!worst[cid] || CLAIM_RANK[f.status] > CLAIM_RANK[worst[cid]]) worst[cid] = f.status;
      });
    });
    current.claims.forEach(function (c) {
      var s = worst[c.id] === "insufficient_signal" ? "inconclusive" : worst[c.id];
      var node = current.cy.getElementById(c.id);
      node.removeClass("contradiction consistent inconclusive");
      if (s) node.addClass(s);
      var mark = document.querySelector('mark[data-claim="' + c.id + '"]');
      if (mark) mark.className = s ? "c-" + s : "";
    });
  }

  function addFindingsToGraph(findings) {
    var cy = current.cy;
    findings.forEach(function (f) {
      addNode(cy, f.id, "witness", LABEL[f.witness] || f.witness, f.status);
      var rel = RELATION[f.status];
      if (!rel) return;   // not applicable: node only
      if (f.claim_ids && f.claim_ids.length) {
        f.claim_ids.forEach(function (cid) { addEdge(cy, f.id, cid, rel); });
      } else {
        var target = (f.locations && f.locations[0] && f.locations[0].file_id) ||
                     (current.inputs[0] && current.inputs[0].file_id);
        if (target) addEdge(cy, f.id, target, rel);
      }
    });
    refreshClaimColours();
    place(cy);
  }

  function refit() {   // the result row changes height (re-run / archive panels): keep the whole graph in view
    if (!current || !current.cy) return;
    var cy = current.cy;
    requestAnimationFrame(function () { cy.resize(); cy.fit(undefined, 25); });
  }

  function rebuildFromGraph(graph) {   // the backend's final graph is the reference: replace ours with it
    var cy = current.cy;
    cy.elements().remove();
    (graph.nodes || []).forEach(function (n) { addNode(cy, n.id, n.type, n.label, n.status); });
    (graph.edges || []).forEach(function (e) { addEdge(cy, e.source, e.target, e.relation); });
    place(cy);
  }

  // ------------------------------------------------------------------ panels
  function buildTiles() {
    var box = $("tiles");
    box.innerHTML = "";
    WITNESSES.forEach(function (w) {
      var t = el("div", "tile waiting");
      t.id = "tile-" + w[0];
      var head = el("div", "t-head");
      head.appendChild(el("span", "t-icon", "·"));
      head.appendChild(el("span", "t-name", w[1]));
      t.appendChild(head);
      t.appendChild(el("div", "t-state", "queued"));
      t.appendChild(el("div", "t-title", ""));
      t.appendChild(el("div", "t-meta", ""));
      box.appendChild(t);
    });
  }

  function tileRunning(name) {
    var t = $("tile-" + name);
    if (!t) return;
    t.className = "tile running";
    t.querySelector(".t-icon").textContent = "🔎";
    t.querySelector(".t-state").textContent = "investigating…";
  }

  function tileDone(name, findings, elapsedMs, failed, reusedFrom) {
    var t = $("tile-" + name);
    if (!t) return;
    var status = failed ? "inconclusive" : worstStatus(findings);
    var main = findings.filter(function (f) { return f.status === status; })[0] || findings[0] || {};
    t.className = "tile done s-" + status;
    t.querySelector(".t-icon").textContent = STATUS[status].icon;
    t.querySelector(".t-state").textContent = STATUS[status].text + (main.reliability ? " · " + main.reliability + " reliability" : "");
    t.querySelector(".t-title").textContent = (main.title || "") + (findings.length > 1 ? "  (+" + (findings.length - 1) + " more)" : "");
    t.querySelector(".t-meta").textContent = reusedFrom ? "re-used from the earlier run" : (elapsedMs != null ? (elapsedMs / 1000).toFixed(1) + " s" : "");
  }

  function showCaption(caption, claims) {
    var p = $("caption");
    p.innerHTML = "";
    var pos = 0;
    claims.slice().sort(function (a, b) { return a.source.start - b.source.start; }).forEach(function (c) {
      if (c.source.start < pos || c.source.end > caption.length) return;   // no caption text (e.g. reopened mid-run)
      p.appendChild(document.createTextNode(caption.slice(pos, c.source.start)));
      var m = el("mark", "", caption.slice(c.source.start, c.source.end));
      m.setAttribute("data-claim", c.id);
      p.appendChild(m);
      pos = c.source.end;
    });
    p.appendChild(document.createTextNode(caption.slice(pos)));
    var box = $("claims");
    box.innerHTML = "";
    if (!claims.length) box.appendChild(el("span", "chip", "No checkable claim found in the caption"));
    claims.forEach(function (c) {
      var chip = el("span", "chip");
      chip.appendChild(el("b", "", CLAIM_TYPE[c.type] || "Claim"));
      chip.appendChild(document.createTextNode(c.source.text));
      box.appendChild(chip);
    });
  }

  function showAssessment(a) {
    var r = $("result");
    r.className = "result " + headlineClass(a.headline);
    $("headline").textContent = a.headline_text;
    var counts = $("counts");
    counts.innerHTML = "";
    COUNT_ORDER.forEach(function (c) {
      var n = (a.counts || {})[c[0]];
      if (n) counts.appendChild(el("span", "pill " + c[0], n + " " + (n === 1 ? c[1] : c[2])));
    });
    refit();
  }

  function showTimeMachine(invId, findings) {   // prior_appearance with frame matches: the archive moment
    var f = findings.filter(function (x) { return x.details && x.details.frame_matches && x.details.frame_matches.length; })[0];
    if (!f) return;
    var d = f.details;
    var best = d.frame_matches.slice().sort(function (a, b) { return (a.hamming || 0) - (b.hamming || 0); })[0];
    $("tm-upload").src = fileUrl(invId, best.upload_frame);
    $("tm-archive").src = fileUrl(invId, best.archive_frame);
    $("tm-upload-cap").textContent = "This upload, at " + Number(best.upload_s || 0).toFixed(1) + " s";
    $("tm-archive-cap").textContent = "Verified " + (d.verified_date || "?") + " · " + (d.verified_location || "?") +
      (d.verified_by ? " · " + d.verified_by : "");
    $("tm-title").textContent = f.title || "";
    $("timemachine").hidden = false;
    refit();
  }

  function showCompare(parentId, newCaption, newAssessment) {
    fetch("/api/investigations/" + encodeURIComponent(parentId)).then(function (r) { return r.json(); }).then(function (old) {
      if (!old || !old.assessment) return;
      $("cmp-old-caption").textContent = "“" + old.caption + "”";
      $("cmp-old").textContent = old.assessment.headline_text;
      $("cmp-old").className = "cmp-headline " + headlineClass(old.assessment.headline);
      $("cmp-new-caption").textContent = "“" + newCaption + "”";
      $("cmp-new").textContent = newAssessment.headline_text;
      $("cmp-new").className = "cmp-headline " + headlineClass(newAssessment.headline);
      $("compare").hidden = false;
      refit();
    }).catch(function () { /* parent not available: the normal result panel still shows */ });
  }

  function setStatus(text, cls) {
    var s = $("case-status");
    s.textContent = text;
    s.className = "case-status" + (cls ? " " + cls : "");
  }

  // ------------------------------------------------------------------ a case
  function openCase(id, meta) {
    if (current && current.id === id) return;
    if (current && current.es) current.es.close();
    meta = meta || {};
    current = { id: id, claims: [], findings: [], inputs: [], parentId: meta.parent_id || null,
                caption: meta.caption || "", assessment: null, es: null, cy: null };
    $("idle").hidden = true;
    $("case").hidden = false;
    $("case-id").textContent = id;
    $("caption").textContent = current.caption;
    $("claims").innerHTML = "";
    $("file-name").textContent = (meta.inputs || []).join(", ");
    $("rerun-tag").hidden = !current.parentId;
    $("result").className = "result pending";
    $("headline").textContent = "Waiting for the witnesses…";
    $("counts").innerHTML = "";
    $("compare").hidden = true;
    $("timemachine").hidden = true;
    setStatus("investigating…");
    buildTiles();
    if (window.cytoscape) { $("graph").innerHTML = ""; current.cy = makeGraph(); }
    if (window.history && history.replaceState) history.replaceState(null, "", "/live?id=" + encodeURIComponent(id));

    var es = new EventSource("/api/investigations/" + encodeURIComponent(id) + "/events");
    current.es = es;
    var mine = current;
    var on = function (name, fn) {
      es.addEventListener(name, function (ev) {
        if (current !== mine) return;              // a newer case took over the screen
        var msg = JSON.parse(ev.data);
        fn(msg.data || {}, msg);
      });
    };

    on("file_received", function (d) {
      if (!d.input) return;
      current.inputs.push(d.input);
      if (d.copied_from) { current.parentId = current.parentId || d.copied_from; $("rerun-tag").hidden = false; }
      $("file-name").textContent = current.inputs.map(function (i) { return i.name; }).join(", ");
      if (current.cy) { addNode(current.cy, d.input.file_id, "input", d.input.name); place(current.cy); }
    });
    on("claims_extracted", function (d) {
      current.claims = d.claims || [];
      showCaption(current.caption, current.claims);
      if (current.cy) {
        current.claims.forEach(function (c) { addNode(current.cy, c.id, "claim", claimLabel(c)); });
        current.inputs.forEach(function (i) {
          current.claims.forEach(function (c) { addEdge(current.cy, i.file_id, c.id, "derived_from"); });
        });
        place(current.cy);
      }
    });
    on("witness_started", function (d) { tileRunning(d.witness); });
    var completed = function (failed) {
      return function (d) {
        var findings = d.findings || [];
        current.findings = current.findings.concat(findings);
        tileDone(d.witness, findings, d.elapsed_ms, failed, d.reused_from);
        if (current.cy) addFindingsToGraph(findings);
        if (d.witness === "prior_appearance") showTimeMachine(current.id, findings);
      };
    };
    on("witness_completed", completed(false));
    on("witness_failed", completed(true));
    on("assessment_computed", function (d) { if (d.assessment) { current.assessment = d.assessment; showAssessment(d.assessment); } });
    on("graph_ready", function (d) { if (d.graph && current.cy) rebuildFromGraph(d.graph); });
    on("done", function (d) {
      setStatus("investigation complete", "done");
      es.close();
      var parent = d.parent_id || current.parentId;
      if (parent && current.assessment) showCompare(parent, current.caption, current.assessment);
    });
    on("failed", function (d) {
      setStatus("stopped with an error", "failed");
      $("headline").textContent = "The investigation stopped with an error: " + (d.error || "unknown");
      es.close();
    });
  }

  function openFromUrl() {   // /live?id=inv_... (e.g. reopening a case after a page reload)
    var m = /[?&]id=(inv_[0-9a-z_]+)/.exec(location.search);
    if (!m) return;
    fetch("/api/investigations/" + m[1]).then(function (r) {
      return r.status === 200 || r.status === 202 ? r.json() : null;
    }).then(function (inv) {
      if (!inv) return;
      openCase(m[1], { caption: inv.caption || "", parent_id: inv.parent_id || null,
                       inputs: (inv.inputs || []).map(function (i) { return i.name; }) });
    }).catch(function () {});
  }

  // ------------------------------------------------------------------ live feed
  function listen() {
    var feed = new EventSource("/api/live/feed");
    feed.onopen = function () { $("conn-dot").className = "conn-dot ok"; $("conn-text").textContent = "listening for new cases"; };
    feed.onerror = function () { $("conn-dot").className = "conn-dot"; $("conn-text").textContent = "reconnecting…"; };
    feed.addEventListener("investigation_started", function (ev) {
      var d = JSON.parse(ev.data);
      openCase(d.id, d);
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "i" || e.key === "I" || e.key === "Escape") showIdle($("idle").hidden);
  });
  window.addEventListener("resize", function () { if (current && current.cy) { current.cy.resize(); place(current.cy); } });

  loadIdle();
  listen();
  openFromUrl();
})();
