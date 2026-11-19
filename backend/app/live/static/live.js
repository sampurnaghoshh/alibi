/* ALIBI Live Desk (CLAUDE.md section 14, step 3). Plain JS, no build step, works offline.
 *
 * - /api/live/feed (SSE): a new investigation (Telegram, upload, demo, re-run) opens automatically.
 * - /api/investigations/{id}/events (SSE): everything on screen comes from these events, drawn the
 *   moment each one arrives, in the order the pipeline wrote them. No timers, no staged delays:
 *   the only motion is a short fade when an element appears.
 * - Graph: fixed three columns (Media -> Claims -> Witnesses), ONE node per witness (its findings
 *   merged, most significant status wins), edges only from the findings' claim links.
 * - Click a witness tile or graph node for its detail panel. /live?id=inv_... reopens a case.
 *   I toggles the idle screen; Esc closes the detail panel, then toggles idle.
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

  var STATUS = {
    contradiction:       { text: "Contradiction", icon: "triangle-alert" },
    consistent:          { text: "Consistent", icon: "circle-check" },
    inconclusive:        { text: "Inconclusive", icon: "circle-help" },
    insufficient_signal: { text: "Insufficient signal", icon: "circle-help" },
    not_applicable:      { text: "Not applicable", icon: "circle-minus" }
  };
  var RANK = { contradiction: 4, consistent: 3, inconclusive: 2, insufficient_signal: 1, not_applicable: 0 };
  var RELATION = { contradiction: "contradicts", consistent: "supports", inconclusive: "inconclusive",
                   insufficient_signal: "inconclusive" };
  var REL_RANK = { contradicts: 3, supports: 2, inconclusive: 1 };
  var REL_TO_CLAIM = { contradicts: "contradiction", inconclusive: "inconclusive", supports: "consistent" };
  var CLAIM_RANK = { contradiction: 3, inconclusive: 2, consistent: 1 };
  var CLAIM_TYPE = { location: "Location", time: "Time", weather_condition: "Event", event: "Event",
                     speaker: "Speaker", other: "Claim" };
  var C = { bg: "#0D1012", surface: "#15191D", elevated: "#1C2227", border: "#303840", text: "#E8ECEF",
            text2: "#929DA7", contra: "#D96B68", supports: "#69B88A", inconc: "#929CAA", accent: "#83B6D1" };
  var COLX = { input: 0, claim: 300, witness: 600 };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var $ = function (id) { return document.getElementById(id); };
  var current = null;

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
    return b;
  }
  function joinNames(names) {
    if (names.length < 2) return names.join("");
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
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
        $("qr").innerHTML = qr.createSvgTag(8, 0);
        $("qr").hidden = false;
      }
    }).catch(function () { /* offline: generic text stays */ });
  }

  // ------------------------------------------------------------------ graph
  function makeGraph() {
    var cy = cytoscape({
      container: $("graph"), elements: [], layout: { name: "preset" },
      userZoomingEnabled: false, userPanningEnabled: false, boxSelectionEnabled: false, autoungrabify: true,
      style: [
        { selector: "node", style: {
            "label": "data(label)", "font-family": "Inter, system-ui, sans-serif", "font-size": 17, "font-weight": 500,
            "color": C.text, "text-valign": "center", "text-halign": "center", "text-wrap": "wrap", "text-max-width": 176,
            "shape": "round-rectangle", "width": 190, "height": 46, "background-color": C.elevated,
            "border-width": 1, "border-color": C.border } },
        { selector: "node.colhead", style: { "background-opacity": 0, "border-width": 0, "color": C.text2,
            "font-size": 12, "font-weight": 600, "text-transform": "uppercase", "height": 20, "events": "no" } },
        { selector: "node.anchor", style: { "opacity": 0, "width": 1, "height": 1, "events": "no" } },
        { selector: "node.input", style: { "border-color": C.accent } },
        { selector: "node.claim.contradiction", style: { "border-color": C.contra } },
        { selector: "node.claim.consistent", style: { "border-color": C.supports } },
        { selector: "node.claim.inconclusive", style: { "border-color": C.inconc } },
        { selector: "node.witness", style: { "height": 54, "font-size": 16 } },
        { selector: "node.witness.contradiction", style: { "border-color": C.contra, "border-width": 1.5 } },
        { selector: "node.witness.consistent", style: { "border-color": C.supports, "border-width": 1.5 } },
        { selector: "node.witness.inconclusive", style: { "border-color": C.inconc } },
        { selector: "node.witness.insufficient_signal", style: { "border-color": C.inconc, "border-style": "dashed" } },
        { selector: "node.witness.not_applicable", style: { "color": C.text2 } },
        { selector: "node.witness.selected", style: { "border-color": C.accent, "border-width": 2 } },
        { selector: "edge", style: { "width": 1.25, "curve-style": "bezier", "line-color": C.inconc,
            "target-arrow-shape": "triangle", "target-arrow-color": C.inconc, "arrow-scale": 0.7,
            "font-family": "Inter, system-ui, sans-serif", "font-size": 12, "color": C.text,
            "text-background-color": C.bg, "text-background-opacity": 1, "text-background-padding": 3 } },
        { selector: "edge.contradicts", style: { "line-color": C.contra, "target-arrow-color": C.contra, "width": 1.75 } },
        { selector: "edge.supports", style: { "line-color": C.supports, "target-arrow-color": C.supports, "width": 1.75 } },
        { selector: "edge.inconclusive", style: { "line-style": "dashed", "line-dash-pattern": [5, 4] } },
        { selector: "edge.derived_from", style: { "line-color": C.border, "target-arrow-color": C.border, "width": 1 } },
        { selector: "edge.hover", style: { "label": "data(label)", "width": 2.5 } }
      ]
    });
    // Column headers + invisible corner anchors: the view is fitted once to the full grid, so it never
    // zooms or jumps while nodes arrive.
    [["h_media", "Media", COLX.input], ["h_claims", "Claims", COLX.claim], ["h_witnesses", "Witnesses", COLX.witness]]
      .forEach(function (h) { cy.add({ group: "nodes", data: { id: h[0], label: h[1] }, classes: "colhead", position: { x: h[2], y: -250 } }); });
    cy.add({ group: "nodes", data: { id: "a1", label: "" }, classes: "anchor", position: { x: COLX.input - 97, y: -262 } });
    cy.add({ group: "nodes", data: { id: "a2", label: "" }, classes: "anchor", position: { x: COLX.witness + 97, y: 222 } });
    cy.on("mouseover", "edge", function (e) { e.target.addClass("hover"); });
    cy.on("mouseout", "edge", function (e) { e.target.removeClass("hover"); });
    cy.on("tap", "node.witness", function (e) { openDetail(e.target.data("witness")); });
    cy.on("mouseover", "node.witness", function () { $("graph").style.cursor = "pointer"; });
    cy.on("mouseout", "node.witness", function () { $("graph").style.cursor = ""; });
    cy.fit(cy.elements(), 16);
    return cy;
  }

  function place(cy) {
    var inputs = cy.nodes(".input"), claims = cy.nodes(".claim");
    inputs.forEach(function (n, i) { n.position({ x: COLX.input, y: (i - (inputs.length - 1) / 2) * 90 }); });
    claims.forEach(function (n, i) { n.position({ x: COLX.claim, y: (i - (claims.length - 1) / 2) * 92 }); });
    cy.nodes(".witness").forEach(function (n) { n.position({ x: COLX.witness, y: (SLOT[n.data("witness")] - 2.5) * 76 }); });
  }

  function fadeIn(ele) {
    ele.style("opacity", 0);
    ele.animate({ style: { opacity: 1 } }, { duration: 420 });
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

  function claimLabel(c) { return c.value.charAt(0).toUpperCase() + c.value.slice(1); }

  function witnessEdges(witness, findings) {   // claim -> strongest relation among this witness's findings
    var out = {};
    findings.forEach(function (f) {
      var rel = RELATION[f.status];
      if (!rel) return;                                // not applicable: node only, no edges
      var targets = (f.claim_ids && f.claim_ids.length) ? f.claim_ids :
        [(f.locations && f.locations[0] && f.locations[0].file_id) || (current.inputs[0] && current.inputs[0].file_id)];
      targets.forEach(function (t) {
        if (t && (!out[t] || REL_RANK[rel] > REL_RANK[out[t]])) out[t] = rel;
      });
    });
    return out;
  }

  function drawWitness(witness) {
    if (!current.cy) return;
    var cy = current.cy, findings = current.byWitness[witness] || [];
    var status = worstStatus(findings);
    var n = ensureNode(cy, "w_" + witness, "witness", LABEL[witness] + "\n" + STATUS[status].text, { witness: witness });
    n.classes("witness " + status + (current.selected === witness ? " selected" : ""));
    n.data("label", LABEL[witness] + "\n" + STATUS[status].text);
    var edges = witnessEdges(witness, findings);
    Object.keys(edges).forEach(function (t) { ensureEdge(cy, "w_" + witness, t, edges[t]); });
    colourClaims();
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
      if (mark) mark.className = s ? "c-" + s : "";
    });
  }

  function refit() {
    if (!current || !current.cy) return;
    var cy = current.cy;
    requestAnimationFrame(function () { cy.resize(); cy.fit(cy.elements(), 16); });
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
      t.addEventListener("click", function () { openDetail(w[0]); });
      box.appendChild(t);
    });
  }

  function setBadge(tile, node) {
    var old = tile.querySelector(".badge");
    old.parentNode.replaceChild(node, old);
  }

  function tileRunning(name) {
    var t = $("tile-" + name);
    if (!t) return;
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
    if (!s) return;
    s.classList.remove("one", "none");
    if (t.scrollHeight > t.clientHeight + 1) s.classList.add("one");
    if (t.scrollHeight > t.clientHeight + 1) { s.classList.remove("one"); s.classList.add("none"); }
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

  function listSection(title, items) {
    if (!items || !items.length) return null;
    var s = el("div", "f-section");
    s.appendChild(el("span", "eyebrow", title));
    var ul = el("ul");
    items.forEach(function (x) { ul.appendChild(el("li", "", x)); });
    s.appendChild(ul);
    return s;
  }

  function openDetail(witness) {
    if (!current) return;
    current.selected = witness;
    document.querySelectorAll(".tile").forEach(function (t) { t.classList.toggle("selected", t.id === "tile-" + witness); });
    if (current.cy) {
      current.cy.nodes(".witness").removeClass("selected");
      current.cy.getElementById("w_" + witness).addClass("selected");
    }
    $("detail-icon").innerHTML = icon(ICON[witness]);
    $("detail-name").textContent = LABEL[witness];
    var body = $("detail-body");
    body.innerHTML = "";
    var findings = bySignificance(current.byWitness[witness]);
    if (!findings.length) body.appendChild(el("p", "f-summary", "This witness has not reported yet."));
    findings.forEach(function (f) {
      var box = el("div", "finding");
      var top = el("div", "f-top");
      top.appendChild(badge(f.status));
      top.appendChild(el("span", "f-rel mono", f.reliability + " reliability" + (f.elapsed_ms != null ? " · " + (f.elapsed_ms / 1000).toFixed(1) + " s" : "")));
      box.appendChild(top);
      box.appendChild(el("div", "f-title", f.title));
      box.appendChild(el("p", "f-summary", f.summary));
      var nums = keyNumbers(f.details);
      if (nums.length) {
        var s = el("div", "f-section");
        s.appendChild(el("span", "eyebrow", "Key numbers"));
        var dl = el("dl", "f-nums");
        nums.forEach(function (r) { dl.appendChild(el("dt", "", r[0].replace(/_/g, " "))); dl.appendChild(el("dd", "", String(r[1]))); });
        s.appendChild(dl);
        box.appendChild(s);
      }
      var ev = (f.evidence || []).map(function (e) { return e.label + (e.kind === "api" ? " (" + e.kind + ")" : ": " + e.ref); });
      [listSection("Evidence", ev), listSection("Alternative explanations", f.alternatives),
       listSection("Limitations", f.limitations)].forEach(function (x) { if (x) box.appendChild(x); });
      body.appendChild(box);
    });
    $("detail").hidden = false;
  }

  function closeDetail() {
    $("detail").hidden = true;
    if (!current) return;
    current.selected = null;
    document.querySelectorAll(".tile.selected").forEach(function (t) { t.classList.remove("selected"); });
    if (current.cy) current.cy.nodes(".witness").removeClass("selected");
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
      p.appendChild(m);
      pos = c.source.end;
    });
    p.appendChild(document.createTextNode(caption.slice(pos)));
    var box = $("claims");
    box.innerHTML = "";
    if (!claims.length) box.appendChild(el("span", "tag", "No checkable claim in the caption"));
    claims.forEach(function (c) {
      var tag = el("span", "tag");
      tag.appendChild(el("span", "tag-k", (CLAIM_TYPE[c.type] || "Claim") + " · "));
      tag.appendChild(document.createTextNode(c.source.text));
      box.appendChild(tag);
    });
  }

  function witnessSummary() {   // per-witness merged statuses, in display order
    var contra = [], cons = [], other = [], contraClaims = {};
    WITNESSES.forEach(function (w) {
      var fs = current.byWitness[w[0]];
      if (!fs) return;
      var s = worstStatus(fs);
      if (s === "contradiction") {
        contra.push(w[1]);
        fs.forEach(function (f) { if (f.status === "contradiction") (f.claim_ids || []).forEach(function (c) { contraClaims[c] = true; }); });
      } else if (s === "consistent") cons.push(w[1]);
      else other.push(w[1]);
    });
    return { contra: contra, cons: cons, other: other,
             claimTexts: current.claims.filter(function (c) { return contraClaims[c.id]; }).map(function (c) { return c.source.text; }) };
  }

  function plainSentence(ws) {   // one plain-language sentence from the findings (template, no LLM)
    var parts = [];
    if (ws.contra.length) {
      parts.push(joinNames(ws.contra) + " evidence " + (ws.contra.length > 1 ? "do" : "does") + " not support the caption" +
                 (ws.claimTexts.length ? " (" + ws.claimTexts.join(", ") + ")" : "") + ".");
    }
    if (ws.cons.length) {
      parts.push(joinNames(ws.cons) + " " + (ws.cons.length > 1 ? "are" : "is") + " consistent with " + (ws.contra.length ? "it" : "the caption") + ".");
    }
    if (ws.other.length) {
      if (!ws.contra.length && !ws.cons.length) parts.push("None of the checks could confirm or contradict the caption with this media.");
      else parts.push(ws.other.length + " other check" + (ws.other.length > 1 ? "s" : "") + " gave no clear answer either way.");
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
    [["n-contra", ws.contra.length, ws.contra.length === 1 ? "contradiction" : "contradictions"],
     ["n-supports", ws.cons.length, "consistent"], ["n-other", ws.other.length, "inconclusive/other"]]
      .forEach(function (c) {
        var span = el("span", c[0]);
        span.appendChild(el("b", "", pad2(c[1])));
        span.appendChild(document.createTextNode(" " + c[2]));
        counts.appendChild(span);
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
                caption: meta.caption || "", assessment: null, es: null, cy: null, selected: null };
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
    $("detail").hidden = true;
    $("detail-close").innerHTML = icon("x");
    setStatus("running");
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
    });
    on("witness_started", function (d) { tileRunning(d.witness); });
    var completed = function (failed) {
      return function (d) {
        var findings = d.findings || [];
        current.byWitness[d.witness] = findings;
        tileDone(d.witness, findings, d.elapsed_ms, failed, d.reused_from);
        drawWitness(d.witness);
        if (current.selected === d.witness) openDetail(d.witness);
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

  $("detail-close").addEventListener("click", closeDetail);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("detail").hidden) { closeDetail(); return; }
    if (e.key === "i" || e.key === "I" || e.key === "Escape") showIdle($("idle").hidden);
  });
  window.addEventListener("resize", function () {
    refit();
    document.querySelectorAll(".tile.done").forEach(fitTile);
  });

  loadIdle();
  listen();
  openFromUrl();
})();
