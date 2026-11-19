/* ALIBI Live Desk (CLAUDE.md section 14, step 3). Plain JS, no build step, works offline.
 *
 * - /api/live/feed (SSE): a new investigation (Telegram, upload, demo, re-run) opens automatically.
 * - /api/investigations/{id}/events (SSE): everything on screen comes from these events, drawn the
 *   moment each one arrives, in the order the pipeline wrote them. No timers, no staged delays:
 *   the only motion is a short fade when an element appears.
 * - Graph: fixed three columns (Media -> Claims -> Witnesses), ONE node per witness (its findings
 *   merged, most significant status wins), edges only from the findings' claim_ids. Witness nodes
 *   are ordered by the average height of the claims they point at, so edges cross as little as possible.
 * - Selection links everything: a witness (tile or node) lights its claims and edges, dims the rest and
 *   opens its detail panel; a claim (node, caption span or tag) lights every witness edge touching it.
 *   Esc or a click on empty space clears it. D opens the case details drawer (SHA-256, custody log).
 * - /api/health tells which witnesses exist in this build; the others are shown as "Not available in
 *   this build", never as an investigation result.
 * - /live?id=inv_... reopens a case. I toggles the idle screen, Esc returns to it.
 * - Keyboard-only demo triggers for the pitch: 1 runs Case A, 2 runs Case B (POST /api/demo/...);
 *   ? shows the shortcut help. Nothing on screen advertises them.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ vocabulary (section 9)
  var WITNESSES = [
    ["metadata", "Metadata", "file-search"], ["weather", "Weather", "cloud-rain"],
    ["prior_appearance", "Prior appearance", "history"], ["lipsync", "Lip-sync", "audio-waveform"],
    ["enf", "Power-grid hum", "zap"], ["ela", "ELA", "scan-eye"]
  ];
  var LABEL = {}, ICON = {}, SLOT = {};
  WITNESSES.forEach(function (w, i) { LABEL[w[0]] = w[1]; ICON[w[0]] = w[2]; SLOT[w[0]] = i; });

  // What each witness examines (detail panel "What was checked") and what it measured (sentences).
  var CHECKS = {
    metadata: { what: "The capture date, GPS position, camera and software tags stored inside the file, compared with the caption.",
                measure: "The file's embedded metadata" },
    weather: { what: "Recorded rainfall at the stated place and at 4 points around it, in the stated time window and the 72 hours before.",
               measure: "The recorded rainfall" },
    prior_appearance: { what: "Whether frames of this media match clips in the verified archive, and whether their verified date and place agree with the caption.",
                        measure: "The archive match" },
    lipsync: { what: "Whether mouth movement and the speech audio move together over time (a synchrony indicator).",
               measure: "The lip-sync timing" },
    enf: { what: "The mains-power hum (50 or 60 Hz) in the audio, compared with the grid frequency of the stated country.",
           measure: "The power-grid hum" },
    ela: { what: "How evenly a keyframe re-compresses as JPEG (an error-level heatmap). A visual aid only, never a verdict.",
           measure: "The error-level heatmap" }
  };

  var STATUS = {
    contradiction:       { text: "Contradiction", icon: "triangle-alert" },
    consistent:          { text: "Consistent", icon: "circle-check" },
    inconclusive:        { text: "Inconclusive", icon: "circle-help" },
    insufficient_signal: { text: "Insufficient signal", icon: "circle-help" },
    not_applicable:      { text: "Not applicable", icon: "circle-minus" },
    unavailable:         { text: "Not available in this build", icon: "circle-minus" }
  };
  var RANK = { contradiction: 4, consistent: 3, inconclusive: 2, insufficient_signal: 1, not_applicable: 0 };
  var RELATION = { contradiction: "contradicts", consistent: "supports", inconclusive: "inconclusive",
                   insufficient_signal: "inconclusive" };
  var REL_RANK = { contradicts: 3, supports: 2, inconclusive: 1 };
  var REL_TO_CLAIM = { contradicts: "contradiction", inconclusive: "inconclusive", supports: "consistent" };
  var CLAIM_RANK = { contradiction: 3, inconclusive: 2, consistent: 1 };
  var CLAIM_TYPE = { location: "Location", time: "Time", weather_condition: "Event", event: "Event",
                     speaker: "Speaker", other: "Claim" };
  var SCOPE = { time: "time", location: "location", weather_condition: "event", event: "event", speaker: "speaker", other: "claim" };
  var SCOPE_ORDER = ["time", "location", "event", "speaker", "claim"];
  var CONDITION = { flood: "flood", heavy_rain: "heavy rain", rain: "rain", storm: "storm", dry: "dry weather" };
  var C = { bg: "#0D1012", surface: "#15191D", elevated: "#1C2227", border: "#303840", text: "#E8ECEF",
            text2: "#929DA7", contra: "#D96B68", supports: "#69B88A", inconc: "#929CAA", accent: "#83B6D1" };
  var COLX = { input: 0, claim: 290, witness: 580 };
  var PITCH = 80, NODE_W = 196, NODE_H = 52;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var $ = function (id) { return document.getElementById(id); };
  var current = null;
  var UNAVAILABLE = {};          // from /api/health: witnesses this build does not implement

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function icon(name, cls) {
    var inner = (window.LUCIDE || {})[name] || "";
    return '<svg class="ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
           'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + "</svg>";
  }
  function fileUrl(invId, path) {
    if (!path) return "";
    if (/^\//.test(path)) return path;
    return "/api/files/" + encodeURIComponent(invId) + "/" + path.split("/").map(encodeURIComponent).join("/");
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear() + ", " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function fmtClock(iso) {   // custody log: 18:40:56.271 (local time of the pipeline's own timestamp)
    var m = /T(\d\d:\d\d:\d\d)(\.\d{1,3})?/.exec(iso || "");
    return m ? m[1] + (m[2] || ".000").padEnd(4, "0") : "";
  }
  function fmtSize(bytes) {
    if (bytes == null) return "";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
    return bytes + " B";
  }
  function worstStatus(findings) {
    var best = null;
    (findings || []).forEach(function (f) { if (best === null || RANK[f.status] > RANK[best]) best = f.status; });
    return best || "inconclusive";
  }
  function bySignificance(findings) {
    return (findings || []).slice().sort(function (a, b) { return RANK[b.status] - RANK[a.status]; });
  }
  function headlineClass(h) {
    if (h === "context_not_supported" || h === "signals_inconsistent" || h === "partially_supported") return "h-red";
    if (h === "consistent_so_far") return "h-green";
    return "h-grey";
  }
  function badge(status) {
    var s = STATUS[status] || { text: status, icon: "circle-help" };
    var b = el("span", "badge s-" + status);
    b.innerHTML = icon(s.icon) + "<span></span>";
    b.lastChild.textContent = s.text;
    b.title = s.text;
    return b;
  }
  function joinWith(names, word) {
    if (names.length < 2) return names.join("");
    return names.slice(0, -1).join(", ") + " " + word + " " + names[names.length - 1];
  }
  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function showIdle(on) { $("idle").hidden = !on; $("case").hidden = on || !current; }
  function isUnavailable(w) { return !!UNAVAILABLE[w]; }

  // ------------------------------------------------------------------ claim scope (what a finding is about)
  function claimById(id) {
    for (var i = 0; i < current.claims.length; i++) if (current.claims[i].id === id) return current.claims[i];
    return null;
  }
  function sortScopes(list) {
    return list.filter(function (s, i) { return list.indexOf(s) === i; })
               .sort(function (a, b) { return SCOPE_ORDER.indexOf(a) - SCOPE_ORDER.indexOf(b); });
  }
  function scopesOf(findings) {   // ["time", "location", ...] covered by these findings' claim links
    var out = [];
    findings.forEach(function (f) {
      (f.claim_ids || []).forEach(function (id) { var c = claimById(id); if (c) out.push(SCOPE[c.type] || "claim"); });
    });
    return sortScopes(out);
  }
  function captionScopes() { return sortScopes(current.claims.map(function (c) { return SCOPE[c.type] || "claim"; })); }
  function theScopes(list, word) { return joinWith(list.map(function (s) { return "the " + s; }), word || "and"); }
  function eventWord() {
    var c = current.claims.filter(function (x) { return x.type === "weather_condition" || x.type === "event"; })[0];
    if (!c) return "conditions";
    var cond = c.normalized && c.normalized.condition;
    return CONDITION[cond] || c.value;
  }
  function metaWhat(scopes) {
    var t = scopes.indexOf("time") >= 0, l = scopes.indexOf("location") >= 0;
    return t && l ? "date and GPS position" : (l ? "GPS position" : "time");
  }

  // One clause per witness, from its findings' scope (template only, no LLM).
  function witnessClause(w, status, scopes) {
    var stated = scopes.length ? "the stated " + joinWith(scopes, "and") : "the caption";
    var contra = {
      weather: "Recorded rainfall does not support the claimed " + eventWord(),
      metadata: "The file's capture " + metaWhat(scopes) + " contradicts " + stated,
      prior_appearance: "Frames match an archive clip verified with a different " + (scopes.length ? joinWith(scopes, "and") : "context"),
      lipsync: "Lip movement and speech audio are out of step",
      enf: "The power-grid hum does not match the grid of the stated location"
    };
    var cons = {
      weather: "Recorded rainfall is consistent with the claimed " + eventWord(),
      metadata: "The capture " + metaWhat(scopes) + " matches " + stated,
      prior_appearance: "Frames match an archive clip verified with the same " + (scopes.length ? joinWith(scopes, "and") : "context"),
      lipsync: "Lip movement and speech audio move together",
      enf: "The power-grid hum matches the grid of the stated location"
    };
    if (status === "contradiction") return contra[w] || (LABEL[w] + " contradicts " + stated);
    return cons[w] || (LABEL[w] + " is consistent with " + stated);
  }

  // ------------------------------------------------------------------ idle screen + build info
  function loadIdle() {
    fetch("/api/live/info").then(function (r) { return r.json(); }).then(function (info) {
      if (!info.bot_username) return;
      $("bot-name").textContent = "@" + info.bot_username;
      if (window.qrcode && info.bot_url) {
        var qr = window.qrcode(0, "M");
        qr.addData(info.bot_url);
        qr.make();
        $("qr").innerHTML = qr.createSvgTag(8, 0);
        $("qr").hidden = false;
      }
    }).catch(function () { /* offline: generic text stays */ });
  }

  function loadHealth() {
    return fetch("/api/health").then(function (r) { return r.json(); }).then(function (h) {
      UNAVAILABLE = {};
      (h.witnesses || []).forEach(function (w) { if (w.implemented === false) UNAVAILABLE[w.name] = true; });
      if (current) applyAvailability();
    }).catch(function () { /* unknown: every witness is shown normally */ });
  }

  function applyAvailability() {
    WITNESSES.forEach(function (w) {
      if (!isUnavailable(w[0])) return;
      markUnavailable(w[0]);
      if (current.byWitness[w[0]]) drawWitness(w[0]);
    });
    renderTimeline();
    if (current.assessment) showAssessment(current.assessment);
  }

  // ------------------------------------------------------------------ graph
  function makeGraph() {
    var cy = cytoscape({
      container: $("graph"), elements: [], layout: { name: "preset" },
      userZoomingEnabled: false, userPanningEnabled: false, boxSelectionEnabled: false, autoungrabify: true,
      style: [
        { selector: "node", style: {
            "label": "data(label)", "font-family": "Inter, system-ui, sans-serif", "font-size": 17, "font-weight": 500,
            "color": C.text, "text-valign": "center", "text-halign": "center", "text-wrap": "wrap", "text-max-width": NODE_W - 16,
            "shape": "round-rectangle", "width": NODE_W, "height": NODE_H, "background-color": C.elevated,
            "border-width": 1, "border-color": C.border,
            "transition-property": "opacity, border-color, border-width", "transition-duration": "0.35s" } },
        { selector: "node.colhead", style: { "background-opacity": 0, "border-width": 0, "color": C.text2,
            "font-size": 12, "font-weight": 600, "text-transform": "uppercase", "height": 20, "events": "no" } },
        { selector: "node.anchor", style: { "opacity": 0, "width": 1, "height": 1, "events": "no" } },
        { selector: "node.input", style: { "border-color": C.accent } },
        { selector: "node.claim.contradiction", style: { "border-color": C.contra } },
        { selector: "node.claim.consistent", style: { "border-color": C.supports } },
        { selector: "node.claim.inconclusive", style: { "border-color": C.inconc } },
        { selector: "node.witness", style: { "font-size": 16 } },
        { selector: "node.witness.contradiction", style: { "border-color": C.contra, "border-width": 1.5 } },
        { selector: "node.witness.consistent", style: { "border-color": C.supports, "border-width": 1.5 } },
        { selector: "node.witness.inconclusive", style: { "border-color": C.inconc } },
        { selector: "node.witness.insufficient_signal", style: { "border-color": C.inconc, "border-style": "dashed" } },
        { selector: "node.witness.not_applicable", style: { "color": C.text2 } },
        { selector: "node.witness.unavailable", style: { "color": C.text2, "border-style": "dashed", "background-color": C.surface } },
        { selector: "edge", style: { "width": 1.25, "curve-style": "bezier", "line-color": C.inconc,
            "target-arrow-shape": "triangle", "target-arrow-color": C.inconc, "arrow-scale": 0.75,
            "font-family": "Inter, system-ui, sans-serif", "font-size": 12, "color": C.text,
            "text-background-color": C.bg, "text-background-opacity": 1, "text-background-padding": 3,
            "transition-property": "opacity, width", "transition-duration": "0.35s" } },
        { selector: "edge.contradicts", style: { "line-color": C.contra, "target-arrow-color": C.contra, "width": 1.75 } },
        { selector: "edge.supports", style: { "line-color": C.supports, "target-arrow-color": C.supports, "width": 1.75 } },
        { selector: "edge.inconclusive", style: { "line-style": "dashed", "line-dash-pattern": [5, 4] } },
        { selector: "edge.derived_from", style: { "line-color": C.border, "target-arrow-color": C.border, "width": 1 } },
        { selector: "edge.hover", style: { "label": "data(label)", "width": 2.5 } },
        // selection: the picked node, what it touches, and everything else dimmed
        { selector: ".dim", style: { "opacity": 0.16 } },
        { selector: "edge.focus", style: { "width": 2.25 } },
        { selector: "edge.derived_from.focus", style: { "width": 1.5, "line-color": C.text2, "target-arrow-color": C.text2 } },
        { selector: "node.focus", style: { "background-color": C.elevated } },
        { selector: "node.picked", style: { "border-color": C.accent, "border-width": 2.5, "background-color": "#22303A" } },
        { selector: ".fresh", style: { "opacity": 0 } }
      ]
    });
    // Column headers + invisible corner anchors: the view is fitted once to the full grid, so it never
    // zooms or jumps while nodes arrive.
    [["h_media", "Media", COLX.input], ["h_claims", "Claims", COLX.claim], ["h_witnesses", "Witnesses", COLX.witness]]
      .forEach(function (h) { cy.add({ group: "nodes", data: { id: h[0], label: h[1], col: h[2] }, classes: "colhead" }); });
    cy.add({ group: "nodes", data: { id: "a1", label: "" }, classes: "anchor" });
    cy.add({ group: "nodes", data: { id: "a2", label: "" }, classes: "anchor" });
    frame(cy, 6);
    cy.on("mouseover", "edge", function (e) { e.target.addClass("hover"); });
    cy.on("mouseout", "edge", function (e) { e.target.removeClass("hover"); });
    cy.on("tap", "node.witness", function (e) { toggleSelect("witness", e.target.data("witness")); });
    cy.on("tap", "node.claim", function (e) { toggleSelect("claim", e.target.id()); });
    cy.on("tap", function (e) { if (e.target === cy) clearSelection(); });
    cy.on("mouseover", "node.witness, node.claim", function () { $("graph").style.cursor = "pointer"; });
    cy.on("mouseout", "node.witness, node.claim", function () { $("graph").style.cursor = ""; });
    return cy;
  }

  function frame(cy, rows) {   // headers and anchors for a grid of `rows` evenly spaced rows; fit once
    var half = (rows - 1) / 2 * PITCH;
    cy.nodes(".colhead").forEach(function (h) { h.position({ x: h.data("col"), y: -half - NODE_H / 2 - 30 }); });
    cy.getElementById("a1").position({ x: COLX.input - NODE_W / 2 - 4, y: -half - NODE_H / 2 - 42 });
    cy.getElementById("a2").position({ x: COLX.witness + NODE_W / 2 + 4, y: half + NODE_H / 2 + 4 });
    cy.scratch("rows", rows);
    cy.fit(cy.elements(), 16);
  }

  function rowY(i, n) { return (i - (n - 1) / 2) * PITCH; }

  function moveTo(n, pos) {
    if (!n.scratch("placed")) { n.position(pos); n.scratch("placed", true); n.scratch("target", pos); return; }
    var t = n.scratch("target") || n.position();
    if (Math.abs(t.x - pos.x) < 0.5 && Math.abs(t.y - pos.y) < 0.5) return;
    n.scratch("target", pos);
    n.stop(true);                                      // a newer target replaces any move in flight
    n.animate({ position: pos }, { duration: 320 });
  }

  function place(cy) {
    var inputs = cy.nodes(".input"), claims = cy.nodes(".claim"), witnesses = cy.nodes(".witness");
    var rows = Math.max(6, inputs.length, claims.length);
    if (rows !== cy.scratch("rows")) frame(cy, rows);
    // media and claims: evenly spaced, centred; claims keep caption order
    inputs.forEach(function (n, i) { moveTo(n, { x: COLX.input, y: rowY(i, inputs.length) }); });
    var claimY = {};
    claims.forEach(function (n, i) { claimY[n.id()] = rowY(i, claims.length); moveTo(n, { x: COLX.claim, y: claimY[n.id()] }); });
    // witnesses: sorted by the average height of the claims they point at (fewest crossings);
    // witnesses without claim edges go to the bottom, in tile order
    var keyed = witnesses.map(function (n) {
      var ys = n.outgoers("edge").targets().map(function (t) { return claimY[t.id()]; })
                .filter(function (y) { return y !== undefined; });
      var bary = ys.length ? ys.reduce(function (a, b) { return a + b; }, 0) / ys.length : Infinity;
      return { n: n, bary: bary, slot: SLOT[n.data("witness")] };
    });
    keyed.sort(function (a, b) { return a.bary === b.bary ? a.slot - b.slot : (a.bary < b.bary ? -1 : 1); });
    keyed.forEach(function (k, i) { moveTo(k.n, { x: COLX.witness, y: rowY(i, WITNESSES.length) }); });
  }

  function fadeIn(ele) {   // stylesheet transition (opacity), so position moves never fight it
    ele.addClass("fresh");
    requestAnimationFrame(function () { requestAnimationFrame(function () { ele.removeClass("fresh"); }); });
  }

  function ensureNode(cy, id, cls, label, data) {
    var n = cy.getElementById(id);
    if (n.length) return n;
    var d = { id: id, label: label };
    for (var k in (data || {})) d[k] = data[k];
    n = cy.add({ group: "nodes", data: d, classes: cls });
    place(cy);
    fadeIn(n);
    return n;
  }

  function ensureEdge(cy, source, target, relation) {
    var id = source + "->" + target;
    var e = cy.getElementById(id);
    if (e.length) {
      e.classes(relation);
      e.data("label", relation.replace("_", " "));
      return;
    }
    if (!cy.getElementById(source).length || !cy.getElementById(target).length) return;
    e = cy.add({ group: "edges", data: { id: id, source: source, target: target, label: relation.replace("_", " ") }, classes: relation });
    fadeIn(e);
  }

  function claimLabel(c) { return cap(c.value); }

  function witnessEdges(witness, findings) {   // claim id -> strongest relation among this witness's findings
    var out = {};
    if (isUnavailable(witness)) return out;           // placeholder, not an analysis: no edges
    findings.forEach(function (f) {
      var rel = RELATION[f.status];
      if (!rel) return;                                // not applicable: node only, no edges
      (f.claim_ids || []).forEach(function (t) {       // strictly the finding's own claim links
        if (!out[t] || REL_RANK[rel] > REL_RANK[out[t]]) out[t] = rel;
      });
    });
    return out;
  }

  function witnessState(witness) {
    return isUnavailable(witness) ? "unavailable" : worstStatus(current.byWitness[witness]);
  }

  function drawWitness(witness) {
    if (!current.cy) return;
    var cy = current.cy, findings = current.byWitness[witness] || [];
    var state = witnessState(witness);
    var label = LABEL[witness] + "\n" + (state === "unavailable" ? "Not available" : STATUS[state].text);
    var n = ensureNode(cy, "w_" + witness, "witness", label, { witness: witness });
    n.classes("witness " + state);
    n.data("label", label);
    var edges = witnessEdges(witness, findings);
    n.outgoers("edge").forEach(function (e) { if (!edges[e.target().id()]) cy.remove(e); });
    Object.keys(edges).forEach(function (t) { ensureEdge(cy, "w_" + witness, t, edges[t]); });
    place(cy);
    colourClaims();
    applySelection();
  }

  function colourClaims() {   // claim colour = strongest relation pointing at it (as the backend graph does)
    var worst = {};
    Object.keys(current.byWitness).forEach(function (w) {
      var edges = witnessEdges(w, current.byWitness[w]);
      Object.keys(edges).forEach(function (cid) {
        var s = REL_TO_CLAIM[edges[cid]];
        if (!worst[cid] || CLAIM_RANK[s] > CLAIM_RANK[worst[cid]]) worst[cid] = s;
      });
    });
    current.claims.forEach(function (c) {
      var s = worst[c.id];
      if (current.cy) {
        var node = current.cy.getElementById(c.id);
        node.removeClass("contradiction consistent inconclusive");
        if (s) node.addClass(s);
      }
      var mark = document.querySelector('mark[data-claim="' + c.id + '"]');
      if (mark) {
        ["c-contradiction", "c-consistent", "c-inconclusive"].forEach(function (k) { mark.classList.remove(k); });
        if (s) mark.classList.add("c-" + s);
      }
    });
  }

  function refit() {
    if (!current || !current.cy) return;
    var cy = current.cy;
    requestAnimationFrame(function () { cy.resize(); cy.fit(cy.elements(), 16); });
  }

  // ------------------------------------------------------------------ selection (graph <-> tiles <-> caption)
  function toggleSelect(kind, id) {
    if (current && current.sel && current.sel.kind === kind && current.sel.id === id) { clearSelection(); return; }
    select(kind, id);
  }

  function select(kind, id) {
    if (!current) return;
    current.sel = { kind: kind, id: id };
    applySelection();
    if (kind === "witness") renderDetail(id); else hideDetail();
  }

  function clearSelection() {
    if (!current) return;
    current.sel = null;
    applySelection();
    hideDetail();
  }

  function applySelection() {   // idempotent: re-run whenever the graph or tiles change
    if (!current) return;
    var sel = current.sel, cy = current.cy;
    var litClaims = {}, linkedWitnesses = {};
    if (sel && sel.kind === "witness") {
      Object.keys(witnessEdges(sel.id, current.byWitness[sel.id] || [])).forEach(function (c) { litClaims[c] = true; });
    } else if (sel && sel.kind === "claim") {
      Object.keys(current.byWitness).forEach(function (w) {
        if (witnessEdges(w, current.byWitness[w])[sel.id]) linkedWitnesses[w] = true;
      });
    }
    document.querySelectorAll(".tile").forEach(function (t) {
      var w = t.id.slice(5);
      t.classList.toggle("selected", !!sel && sel.kind === "witness" && sel.id === w);
      t.classList.toggle("linked", !!linkedWitnesses[w]);
    });
    document.querySelectorAll("mark[data-claim], .tag[data-claim]").forEach(function (m) {
      var c = m.getAttribute("data-claim");
      m.classList.toggle("sel", !!sel && sel.kind === "claim" && sel.id === c);
      m.classList.toggle("lit", !!litClaims[c]);
    });
    if (!cy) return;
    var all = cy.elements().not(".colhead, .anchor");
    all.removeClass("dim focus picked");
    if (!sel) return;
    var node = cy.getElementById(sel.kind === "witness" ? "w_" + sel.id : sel.id);
    if (!node.length) return;                          // not drawn yet: tiles and caption still show it
    var edges = sel.kind === "witness" ? node.outgoers("edge") : node.connectedEdges();
    var focus = node.union(edges).union(edges.connectedNodes());
    node.addClass("picked");
    focus.addClass("focus");
    all.not(focus).addClass("dim");
  }

  // ------------------------------------------------------------------ witness tiles
  function buildTiles() {
    var box = $("tiles");
    box.innerHTML = "";
    WITNESSES.forEach(function (w) {
      var t = el("div", "tile queued");
      t.id = "tile-" + w[0];
      var top = el("div", "t-top");
      var ic = el("span", "t-icon");
      ic.innerHTML = icon(w[2]);
      top.appendChild(ic);
      top.appendChild(el("span", "t-name", w[1]));
      var b = el("span", "badge s-queued");
      b.innerHTML = icon("clock") + "<span>Queued</span>";
      top.appendChild(b);
      t.appendChild(top);
      t.appendChild(el("div", "t-title", "Waiting to start"));
      t.appendChild(el("div", "t-summary", ""));
      t.appendChild(el("div", "t-foot mono", ""));
      t.appendChild(el("div", "t-progress"));
      t.addEventListener("click", function () { toggleSelect("witness", w[0]); });
      box.appendChild(t);
      if (isUnavailable(w[0])) markUnavailable(w[0]);
    });
  }

  function setBadge(tile, node) {
    var old = tile.querySelector(".badge");
    old.parentNode.replaceChild(node, old);
  }

  function markUnavailable(name) {   // a stub in this build: never presented as an investigation result
    var t = $("tile-" + name);
    if (!t) return;
    t.className = "tile unavailable";
    setBadge(t, badge("unavailable"));
    t.querySelector(".t-title").textContent = "This witness is not part of this build";
    t.querySelector(".t-summary").textContent = "It does not examine the evidence yet, so it adds nothing to this case.";
    t.querySelector(".t-foot").innerHTML = "";
    applySelection();
  }

  function tileRunning(name) {
    var t = $("tile-" + name);
    if (!t || isUnavailable(name)) return;
    t.classList.remove("queued");
    t.classList.add("investigating");
    var b = el("span", "badge s-investigating");
    b.innerHTML = icon("loader-circle") + "<span>Investigating</span>";
    setBadge(t, b);
    t.querySelector(".t-title").textContent = "Examining the evidence…";
  }

  function tileDone(name, findings, elapsedMs, failed, reusedFrom) {
    var t = $("tile-" + name);
    if (!t) return;
    if (isUnavailable(name)) { markUnavailable(name); return; }
    var sorted = bySignificance(findings);
    var status = failed ? "inconclusive" : worstStatus(findings);
    var main = sorted[0] || {};
    t.classList.remove("queued", "investigating");
    t.classList.add("done");
    setBadge(t, badge(status));
    t.querySelector(".t-title").textContent = main.title || STATUS[status].text;
    t.querySelector(".t-summary").textContent = main.summary || "";
    var foot = t.querySelector(".t-foot");
    foot.innerHTML = "";
    if (main.reliability) foot.appendChild(el("span", "", main.reliability + " reliability"));
    foot.appendChild(el("span", "", reusedFrom ? "re-used from the earlier run" :
                                    (elapsedMs != null ? (elapsedMs / 1000).toFixed(1) + " s" : "")));
    if (findings.length > 1) foot.appendChild(el("span", "t-more", "+" + (findings.length - 1) + " more finding" + (findings.length > 2 ? "s" : "")));
    fitTile(t);
  }

  function fitTile(t) {   // never cut the title: the summary preview shrinks to one line, then hides
    var s = t.querySelector(".t-summary");
    if (!s || $("wa-main").classList.contains("has-detail")) return;
    s.classList.remove("one", "none");
    if (t.scrollHeight > t.clientHeight + 1) s.classList.add("one");
    if (t.scrollHeight > t.clientHeight + 1) { s.classList.remove("one"); s.classList.add("none"); }
  }
  function fitTiles() { document.querySelectorAll(".tile.done").forEach(fitTile); }

  // ------------------------------------------------------------------ execution timeline
  function tsMs(iso) { var d = Date.parse(iso || ""); return isNaN(d) ? null : d; }

  function renderTimeline() {
    var lanes = $("tl-lanes");
    if (!current) return;
    var tm = current.timing, t0 = current.t0, tEnd = current.tLast;
    lanes.innerHTML = "";
    var span = (t0 != null && tEnd != null) ? Math.max(tEnd - t0, 1) : 1;
    $("tl-span").textContent = t0 != null ? "0 – " + (span / 1000).toFixed(1) + " s" : "";
    WITNESSES.forEach(function (w) {
      var r = tm[w[0]] || {}, unav = isUnavailable(w[0]);
      var lane = el("div", "lane");
      if (unav) lane.classList.add("unavailable");
      else if (r.start != null && r.end == null) lane.classList.add("running");
      else if (r.status) lane.classList.add("s-" + r.status);
      lane.appendChild(el("span", "ln-name", w[1]));
      var track = el("span", "ln-track");
      if (r.start != null && t0 != null) {
        var bar = el("i", "ln-bar");
        var end = r.end != null ? r.end : tEnd;
        bar.style.left = ((r.start - t0) / span * 100).toFixed(2) + "%";
        bar.style.width = (Math.max(end - r.start, 0) / span * 100).toFixed(2) + "%";
        track.appendChild(bar);
      }
      lane.appendChild(track);
      var dur = unav ? "n/a" : r.reused ? "re-used" : r.elapsed != null ? (r.elapsed / 1000).toFixed(1) + " s" :
                (r.start != null ? "…" : "");
      lane.appendChild(el("span", "ln-dur", dur));
      lane.title = w[1] + (unav ? ": not available in this build" : r.reused ? ": re-used from the earlier run" :
                   r.start != null ? ": started +" + ((r.start - t0) / 1000).toFixed(2) + " s" +
                   (r.end != null ? ", finished +" + ((r.end - t0) / 1000).toFixed(2) + " s" : ", running") : ": queued");
      lanes.appendChild(lane);
    });
  }

  // ------------------------------------------------------------------ detail panel
  function keyNumbers(details) {
    var rows = [];
    Object.keys(details || {}).forEach(function (k) {
      var v = details[k];
      if (v === null || v === undefined || rows.length >= 12) return;
      if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") {
        if (typeof v === "string" && v.length > 90) return;
        rows.push([k, v]);
      } else if (typeof v === "object" && !Array.isArray(v)) {
        Object.keys(v).forEach(function (sub) {
          var x = v[sub];
          if ((typeof x === "number" || typeof x === "boolean" || (typeof x === "string" && x.length <= 60)) && rows.length < 12) {
            rows.push([k + "." + sub, x]);
          }
        });
      }
    });
    return rows;
  }

  function section(title) {
    var s = el("div", "f-section");
    s.appendChild(el("span", "eyebrow", title));
    return s;
  }

  function addList(parent, items, sub) {
    if (!items || !items.length) return;
    if (sub) parent.appendChild(el("span", "sub", sub));
    var ul = el("ul");
    items.forEach(function (x) { ul.appendChild(el("li", "", x)); });
    parent.appendChild(ul);
  }

  function claimTag(c) {
    var tag = el("span", "tag");
    tag.setAttribute("data-claim", c.id);
    tag.appendChild(el("span", "tag-k", (CLAIM_TYPE[c.type] || "Claim") + " · "));
    tag.appendChild(document.createTextNode(c.source.text));
    tag.addEventListener("click", function (e) { e.stopPropagation(); toggleSelect("claim", c.id); });
    return tag;
  }

  function whyHeading(status) {
    return { contradiction: "Why it contradicts the claim", consistent: "Why it supports the claim",
             not_applicable: "Why it does not apply" }[status] || "Why it does not decide";
  }

  function whyText(w, f) {   // template from status + the claims this finding is linked to
    var measure = CHECKS[w] ? CHECKS[w].measure : LABEL[w];
    var scopes = scopesOf([f]);
    var others = captionScopes().filter(function (s) { return scopes.indexOf(s) < 0; });
    var stated = "the stated " + joinWith(scopes, "and");
    var t;
    if (f.status === "contradiction") {
      t = scopes.length ? measure + " disagrees with " + stated + ", so this evidence does not support that part of the caption."
                        : measure + " shows an inconsistency within the media itself. It does not test the caption's claims directly.";
      if (f.reliability === "low") t += " Its reliability is low, so it counts only as a weak indicator.";
    } else if (f.status === "consistent") {
      t = scopes.length ? measure + " agrees with " + stated + ". That supports this part of the caption only" +
                          (others.length ? "; it does not confirm " + theScopes(others, "or") + "." : ".")
                        : measure + " shows no inconsistency within the media itself. It does not test the caption's claims.";
    } else if (f.status === "not_applicable") {
      t = "This check needs something this media or caption does not have, so it says nothing about the caption.";
    } else {
      t = measure + " gave no clear answer" + (scopes.length ? " about " + stated : "") +
          ", so it neither supports nor contradicts the caption.";
      if (f.status === "insufficient_signal") t += " The signal it needs was too weak or missing, and a missing signal is not evidence either way.";
    }
    return t;
  }

  function renderDetail(witness) {
    $("detail-icon").innerHTML = icon(ICON[witness]);
    $("detail-name").textContent = LABEL[witness];
    var body = $("detail-body");
    body.innerHTML = "";
    var findings = bySignificance(current.byWitness[witness]);

    var checked = section("What was checked");
    if (isUnavailable(witness)) {
      checked.appendChild(el("p", "checked", "Nothing yet. This witness is not part of this build, so it did not examine the evidence. " +
        "The pipeline records a placeholder for it, which is not shown here as an investigation result."));
      body.appendChild(checked);
      showDetail();
      return;
    }
    checked.appendChild(el("p", "checked", CHECKS[witness] ? CHECKS[witness].what : ""));
    var ids = {};
    findings.forEach(function (f) { (f.claim_ids || []).forEach(function (c) { ids[c] = true; }); });
    var linked = current.claims.filter(function (c) { return ids[c.id]; });
    if (linked.length) {
      var row = el("div", "checked-claims");
      linked.forEach(function (c) { row.appendChild(claimTag(c)); });
      checked.appendChild(row);
    } else if (findings.length) {
      checked.appendChild(el("span", "sub", "It does not use the caption's claims."));
    }
    body.appendChild(checked);

    if (!findings.length) body.appendChild(el("p", "f-summary", "This witness has not reported yet."));
    findings.forEach(function (f) {
      var box = el("div", "finding");
      var top = el("div", "f-top");
      top.appendChild(badge(f.status));
      top.appendChild(el("span", "f-rel mono", f.reliability + " reliability" + (f.elapsed_ms != null ? " · " + (f.elapsed_ms / 1000).toFixed(1) + " s" : "")));
      box.appendChild(top);

      var data = section("Data retrieved");
      var nums = keyNumbers(f.details);
      if (nums.length) {
        var dl = el("dl", "f-nums");
        nums.forEach(function (r) { dl.appendChild(el("dt", "", r[0].replace(/_/g, " "))); dl.appendChild(el("dd", "", String(r[1]))); });
        data.appendChild(dl);
      }
      addList(data, (f.evidence || []).map(function (e) { return e.label + (e.kind === "api" ? " (" + e.kind + ")" : ": " + e.ref); }),
              nums.length ? "Sources" : null);
      if (!nums.length && !(f.evidence || []).length) data.appendChild(el("p", "", "No data was retrieved for this finding."));
      box.appendChild(data);

      var found = section("What was found");
      found.appendChild(el("div", "f-title", f.title));
      found.appendChild(el("p", "f-summary", f.summary));
      box.appendChild(found);

      var why = section(whyHeading(f.status));
      why.appendChild(el("p", "", whyText(witness, f)));
      box.appendChild(why);

      var alt = section("Alternatives & limitations");
      addList(alt, f.alternatives, "Other explanations");
      addList(alt, f.limitations, "Limitations");
      box.appendChild(alt);
      body.appendChild(box);
    });
    showDetail();
  }

  function showDetail() {
    var wasOpen = !$("detail").hidden;
    $("detail").hidden = false;
    $("wa-main").classList.add("has-detail");
    if (!wasOpen) $("detail-body").scrollTop = 0;
  }

  function hideDetail() {
    $("detail").hidden = true;
    $("wa-main").classList.remove("has-detail");
    fitTiles();
  }

  // ------------------------------------------------------------------ case details drawer
  function renderDrawer() {
    if (!current || $("drawer").hidden) return;
    var body = $("drawer-body");
    body.innerHTML = "";
    var c = section("Case");
    var kv = el("dl", "kv");
    var add = function (k, v, mono) { if (v === null || v === undefined || v === "") return; kv.appendChild(el("dt", "", k)); kv.appendChild(el("dd", mono ? "mono" : "", String(v))); };
    add("Investigation", current.id, true);
    add("Shared on", fmtTime(current.postedAt));
    add("Re-run of", current.parentId, true);
    add("Caption", current.caption);
    c.appendChild(kv);
    body.appendChild(c);

    current.inputs.forEach(function (i) {
      var s = section("Original file");
      var d = el("dl", "kv");
      var a = function (k, v, mono) { if (v === null || v === undefined || v === "") return; d.appendChild(el("dt", "", k)); d.appendChild(el("dd", mono ? "mono" : "", String(v))); };
      a("Name", i.name);
      a("Type", i.mime, true);
      a("Size", fmtSize(i.size), true);
      a("Duration", i.duration_s != null ? Number(i.duration_s).toFixed(2) + " s" : "still image", i.duration_s != null);
      a("SHA-256", i.sha256, true);
      a("Stored as", i.stored_path + " (read-only)", true);
      s.appendChild(d);
      body.appendChild(s);
    });
    if (!current.inputs.length) body.appendChild(el("p", "f-summary", "No file was received for this case."));

    var log = section("Chain of custody");
    var ul = el("ul", "custody");
    current.log.forEach(function (m) {
      var li = el("li");
      li.appendChild(el("span", "c-ts", fmtClock(m.ts)));
      li.appendChild(el("span", "c-ev", m.event));
      li.appendChild(el("span", "c-detail", m.detail || ""));
      ul.appendChild(li);
    });
    log.appendChild(ul);
    body.appendChild(log);
  }

  function toggleDrawer(on) {
    if (!current) return;
    if (on === undefined) on = $("drawer").hidden;
    $("drawer").hidden = !on;
    $("details-btn").classList.toggle("on", on);
    if (on) renderDrawer();
  }

  // ------------------------------------------------------------------ caption, assessment, panels
  function showCaption(caption, claims) {
    var p = $("caption");
    p.innerHTML = "";
    var pos = 0;
    claims.slice().sort(function (a, b) { return a.source.start - b.source.start; }).forEach(function (c) {
      if (c.source.start < pos || c.source.end > caption.length) return;   // no caption text (e.g. reopened mid-run)
      p.appendChild(document.createTextNode(caption.slice(pos, c.source.start)));
      var m = el("mark", "", caption.slice(c.source.start, c.source.end));
      m.setAttribute("data-claim", c.id);
      m.addEventListener("click", function (e) { e.stopPropagation(); toggleSelect("claim", c.id); });
      p.appendChild(m);
      pos = c.source.end;
    });
    p.appendChild(document.createTextNode(caption.slice(pos)));
    var box = $("claims");
    box.innerHTML = "";
    if (!claims.length) box.appendChild(el("span", "tag", "No checkable claim in the caption"));
    claims.forEach(function (c) { box.appendChild(claimTag(c)); });
  }

  function witnessSummary() {   // per-witness merged statuses, in display order; stubs counted apart
    var out = { contra: [], cons: [], other: [], unavailable: [] };
    WITNESSES.forEach(function (w) {
      var fs = current.byWitness[w[0]];
      if (!fs) return;
      if (isUnavailable(w[0])) { out.unavailable.push(w[0]); return; }
      var s = worstStatus(fs);
      var entry = { w: w[0], status: s, scopes: scopesOf(fs.filter(function (f) { return f.status === s; })) };
      if (s === "contradiction") out.contra.push(entry);
      else if (s === "consistent") out.cons.push(entry);
      else out.other.push(entry);
    });
    return out;
  }

  // Plain sentences from what each witness checked (template, no LLM), e.g.
  // "Recorded rainfall does not support the claimed flood. The capture time matches the stated time,
  //  which does not confirm the location or the event. 3 other checks gave no clear answer."
  function plainSentence(ws) {
    var parts = [], all = captionScopes();
    ws.contra.forEach(function (e) { parts.push(witnessClause(e.w, e.status, e.scopes) + "."); });
    ws.cons.forEach(function (e) {
      var others = all.filter(function (s) { return e.scopes.indexOf(s) < 0; });
      var tail = !all.length ? "." : !e.scopes.length ? ", which says nothing about the caption's claims." :
                 others.length ? ", which does not confirm " + theScopes(others, "or") + "." : ".";
      parts.push(witnessClause(e.w, e.status, e.scopes) + tail);
    });
    if (ws.other.length) {
      if (!ws.contra.length && !ws.cons.length) parts.push("None of the checks could confirm or contradict the caption with this media.");
      else parts.push(ws.other.length + " other check" + (ws.other.length > 1 ? "s" : "") + " gave no clear answer.");
    }
    if (ws.unavailable.length) {
      parts.push(joinWith(ws.unavailable.map(function (w) { return LABEL[w]; }), "and") + " " +
                 (ws.unavailable.length > 1 ? "are" : "is") + " not available in this build.");
    }
    return parts.join(" ");
  }

  function showAssessment(a) {
    $("result").className = "result " + headlineClass(a.headline);
    $("headline").textContent = a.headline_text;
    var ws = witnessSummary();
    $("sentence").textContent = plainSentence(ws);
    var counts = $("counts");
    counts.innerHTML = "";
    var rows = [["n-contra", ws.contra.length, ws.contra.length === 1 ? "contradiction" : "contradictions"],
                ["n-supports", ws.cons.length, "consistent"], ["n-other", ws.other.length, "inconclusive/other"]];
    if (ws.unavailable.length) rows.push(["n-unav", ws.unavailable.length, "not available"]);
    rows.forEach(function (c) {
      var span = el("span", c[0]);
      span.appendChild(el("b", "", pad2(c[1])));
      span.appendChild(document.createTextNode(" " + c[2]));
      counts.appendChild(span);
    });
    refit();
  }

  function showTimeMachine(invId, findings) {   // prior_appearance with frame matches: the archive moment
    // Only a high-reliability match (a near-identical copy) is shown as "Seen before"; a medium
    // "resembles" result keeps its pairs in the detail panel's data, not on the big screen.
    var f = findings.filter(function (x) {
      return x.reliability === "high" && x.details && x.details.frame_matches && x.details.frame_matches.length;
    })[0];
    if (!f) return;
    var d = f.details;
    var best = d.frame_matches.slice().sort(function (a, b) { return (a.hamming || 0) - (b.hamming || 0); })[0];
    // the panel only takes its final height once the frames have loaded: refit the graph and tiles then
    ["tm-upload", "tm-archive"].forEach(function (id) { $(id).onload = function () { refit(); fitTiles(); }; });
    $("tm-upload").src = fileUrl(invId, best.upload_frame);
    $("tm-archive").src = fileUrl(invId, best.archive_frame);
    $("tm-upload-cap").textContent = "This upload, at " + Number(best.upload_s || 0).toFixed(1) + " s";
    $("tm-archive-cap").textContent = "Verified " + (d.verified_date || "?") + " · " + (d.verified_location || "?") +
      (d.verified_by ? " · " + d.verified_by : "");
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
    }).catch(function () { /* parent not available: the normal summary still shows */ });
  }

  function setStatus(kind) {
    var s = $("case-status");
    var map = { running: ["loader-circle", "Investigating", ""], done: ["circle-check", "Complete", "done"],
                failed: ["triangle-alert", "Stopped with an error", "failed"] };
    var m = map[kind];
    s.className = "case-status " + m[2];
    s.innerHTML = icon(m[0], kind === "running" ? "spin" : "") + "<span></span>";
    s.lastChild.textContent = m[1];
    if (kind === "running") s.firstChild.style.animation = "spin 1.6s linear infinite";
  }

  function setRerun(on) {
    var t = $("rerun-tag");
    t.hidden = !on;
    if (on) t.innerHTML = icon("repeat-2") + "<span>Same file, new story</span>";
  }

  // ------------------------------------------------------------------ a case
  function openCase(id, meta) {
    if (current && current.id === id) return;
    if (current && current.es) current.es.close();
    meta = meta || {};
    current = { id: id, claims: [], byWitness: {}, inputs: [], parentId: meta.parent_id || null,
                caption: meta.caption || "", postedAt: meta.posted_at || null, assessment: null, es: null, cy: null,
                sel: null, log: [], timing: {}, t0: null, tLast: null };
    $("idle").hidden = true;
    $("case").hidden = false;
    $("case-id").textContent = id;
    $("caption").textContent = current.caption;
    $("claims").innerHTML = "";
    $("file-name").textContent = (meta.inputs || []).join(", ");
    $("file-time").textContent = fmtTime(meta.posted_at);
    setRerun(!!current.parentId);
    $("result").className = "result pending";
    $("headline").textContent = "Waiting for the witnesses…";
    $("sentence").textContent = "";
    $("counts").innerHTML = "";
    $("compare").hidden = true;
    $("timemachine").hidden = true;
    hideDetail();
    toggleDrawer(false);
    setStatus("running");
    buildTiles();
    renderTimeline();
    if (window.cytoscape) { $("graph").innerHTML = ""; current.cy = makeGraph(); }
    if (window.history && history.replaceState) history.replaceState(null, "", "/live?id=" + encodeURIComponent(id));

    var es = new EventSource("/api/investigations/" + encodeURIComponent(id) + "/events");
    current.es = es;
    var mine = current;
    var on = function (name, fn) {
      es.addEventListener(name, function (ev) {
        if (current !== mine) return;              // a newer case took over the screen
        var msg = JSON.parse(ev.data);
        current.log.push({ ts: msg.ts, event: msg.event, detail: msg.detail });
        var t = tsMs(msg.ts);
        if (t != null && (current.tLast == null || t > current.tLast)) current.tLast = t;
        fn(msg.data || {}, msg);
        renderDrawer();
      });
    };

    on("file_received", function (d) {
      if (!d.input) return;
      current.inputs.push(d.input);
      if (d.copied_from) { current.parentId = current.parentId || d.copied_from; setRerun(true); }
      $("file-name").textContent = current.inputs.map(function (i) { return i.name; }).join(", ");
      if (current.cy) ensureNode(current.cy, d.input.file_id, "input", d.input.name);
    });
    on("claims_extracted", function (d) {
      current.claims = d.claims || [];
      showCaption(current.caption, current.claims);
      if (current.cy) {
        current.claims.forEach(function (c) { ensureNode(current.cy, c.id, "claim", claimLabel(c)); });
        current.inputs.forEach(function (i) {
          current.claims.forEach(function (c) { ensureEdge(current.cy, i.file_id, c.id, "derived_from"); });
        });
      }
      applySelection();
    });
    on("witness_started", function (d, msg) {
      var t = tsMs(msg.ts);
      if (current.t0 == null || (t != null && t < current.t0)) current.t0 = t;
      current.timing[d.witness] = { start: t, end: null };
      tileRunning(d.witness);
      renderTimeline();
    });
    var completed = function (failed) {
      return function (d, msg) {
        var findings = d.findings || [];
        current.byWitness[d.witness] = findings;
        var r = current.timing[d.witness] || {};
        r.end = r.start != null ? tsMs(msg.ts) : null;
        r.elapsed = d.reused_from ? null : d.elapsed_ms;
        r.reused = !!d.reused_from;
        r.status = failed ? "inconclusive" : worstStatus(findings);
        current.timing[d.witness] = r;
        tileDone(d.witness, findings, d.elapsed_ms, failed, d.reused_from);
        drawWitness(d.witness);
        renderTimeline();
        if (current.sel && current.sel.kind === "witness" && current.sel.id === d.witness) renderDetail(d.witness);
        if (d.witness === "prior_appearance") showTimeMachine(current.id, findings);
      };
    };
    on("witness_completed", completed(false));
    on("witness_failed", completed(true));
    on("assessment_computed", function (d) { if (d.assessment) { current.assessment = d.assessment; showAssessment(d.assessment); } });
    on("graph_ready", function () { Object.keys(current.byWitness).forEach(drawWitness); });   // idempotent re-check
    on("done", function (d) {
      setStatus("done");
      es.close();
      var parent = d.parent_id || current.parentId;
      if (parent && current.assessment) showCompare(parent, current.caption, current.assessment);
    });
    on("failed", function (d) {
      setStatus("failed");
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
      openCase(m[1], { caption: inv.caption || "", parent_id: inv.parent_id || null, posted_at: inv.posted_at,
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

  // ------------------------------------------------------------------ wiring
  $("detail-close").innerHTML = icon("x");
  $("drawer-close").innerHTML = icon("x");
  $("drawer-icon").innerHTML = icon("fingerprint");
  $("details-btn").innerHTML = icon("fingerprint") + "<span>Case details</span>";
  $("detail-close").addEventListener("click", clearSelection);
  $("drawer-close").addEventListener("click", function () { toggleDrawer(false); });
  $("details-btn").addEventListener("click", function () { toggleDrawer(); });
  // a click on empty space (not a tile, claim, panel, graph or button) clears the selection
  $("case").addEventListener("click", function (e) {
    if (!e.target.closest(".tile, .detail, mark, .tag, .graph, button, .drawer")) clearSelection();
  });
  // ------------------------------------------------------------------ demo triggers (keyboard only)
  // 1 / 2 start Case A / Case B on the server. Nothing appears on screen: the case opens through the
  // live feed like any other. The outcome of the last trigger is only shown in the "?" help overlay.
  var DEMO_KEYS = { "1": "case_a", "2": "case_b" };
  var triggering = null;

  function helpStatus(text) { $("help-status").textContent = text; }

  function triggerDemo(name) {
    if (triggering) { helpStatus(triggering + " is still running; " + name + " was not started."); return; }
    triggering = name;
    helpStatus("Started " + name + ".");
    fetch("/api/demo/" + encodeURIComponent(name), { method: "POST" }).then(function (r) {
      if (r.ok) { helpStatus(name + " finished."); return; }
      return r.json().catch(function () { return {}; }).then(function (body) {
        helpStatus(name + " did not run: " + (body.detail || ("HTTP " + r.status)));
      });
    }).catch(function () {
      helpStatus(name + " did not run: the server could not be reached.");
    }).then(function () { triggering = null; });
  }

  function toggleHelp(on) {
    if (on === undefined) on = $("help").hidden;
    $("help").hidden = !on;
  }

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "?") { toggleHelp(); return; }
    if (e.key === "Escape") {
      if (!$("help").hidden) { toggleHelp(false); return; }
      if (!$("drawer").hidden) { toggleDrawer(false); return; }
      if (current && current.sel) { clearSelection(); return; }
      showIdle(true);                                  // Esc always returns to the idle screen
      return;
    }
    if (DEMO_KEYS[e.key]) { triggerDemo(DEMO_KEYS[e.key]); return; }
    if ((e.key === "d" || e.key === "D") && current && $("idle").hidden) { toggleDrawer(); return; }
    if (e.key === "i" || e.key === "I") showIdle($("idle").hidden);
  });
  $("help-close").innerHTML = icon("x");
  $("help-close").addEventListener("click", function () { toggleHelp(false); });
  window.addEventListener("resize", function () { refit(); fitTiles(); });

  loadIdle();
  listen();
  loadHealth().then(openFromUrl);
})();
