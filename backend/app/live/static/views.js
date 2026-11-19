/* ALIBI Live Desk: "Evidence views" (key V). Visualises the work the witnesses did, drawn ONLY from the
 * investigation's own data: claims, findings' details, their artifacts and the run's event log. Nothing is
 * animated or invented: a card appears when its data exists and stays hidden otherwise.
 *
 *   map         claimed place, verified archive place, file GPS, weather grid (mm); measured distances (km)
 *   time        claimed window vs verified recording date / file capture time; the witnesses' own gaps
 *   frames      every sampled upload frame with its best Hamming distance (prior_appearance frame_strip)
 *   rain        hourly rainfall at the claimed place (weather hourly_centre)
 *   lipsync     mouth vs audio curves (artifacts/lipsync_series.json)
 *   enf / ela   the witnesses' own images
 *   provenance  input SHA-256 + the chain-of-custody events as they arrive
 *
 * live.js calls AlibiViews.render(state) after every event; AlibiViews.reset() for a new case.
 */
(function () {
  "use strict";

  var C = { text: "#E8ECEF", text2: "#929DA7", border: "#303840", surface: "#15191D", elevated: "#1C2227",
            contra: "#D96B68", supports: "#69B88A", inconc: "#929CAA", accent: "#83B6D1", bg: "#0D1012" };
  var ORDER = ["map", "time", "frames", "rain", "grid", "lipsync", "enf", "ela", "provenance"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var NS = ["http", "//www.w3.org/2000/svg"].join(":");   // the SVG namespace: an identifier, nothing is fetched

  var india = null, indiaLoading = false;       // vendor/geo/india.json, loaded once
  var series = {};                               // "<inv>/<ref>" -> lipsync series JSON (or "loading")
  var cards = {};                                // key -> {el, sig}
  var root = null, onChange = null, last = null;

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function svg(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function fileUrl(invId, ref) {
    return "/api/files/" + encodeURIComponent(invId) + "/" + ref.split("/").map(encodeURIComponent).join("/");
  }
  function fmt(n, d) { return (Math.round(n * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }
  function dayLabel(d) { return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear(); }
  function clockLabel(d) { return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2); }
  function findings(state, witness) { return state.byWitness[witness] || []; }
  function artifact(state, witness, test) {
    var out = null;
    findings(state, witness).forEach(function (f) {
      (f.evidence || []).forEach(function (e) { if (!out && e.kind === "artifact" && test(e.ref)) out = e; });
    });
    return out;
  }
  function card(title, sub) {
    var c = el("section", "vcard");
    var h = el("div", "vhead");
    h.appendChild(el("span", "eyebrow", title));
    if (sub) h.appendChild(el("span", "vsub", sub));
    c.appendChild(h);
    return c;
  }

  // ------------------------------------------------------------------ map
  function locationClaim(state) {
    return state.claims.filter(function (c) {
      return c.type === "location" && c.normalized && c.normalized.lat != null && c.normalized.lon != null;
    })[0] || null;
  }

  function mapData(state) {
    var pts = [], lines = [], grid = null;
    var claim = locationClaim(state);
    var claimed = claim ? { lat: claim.normalized.lat, lon: claim.normalized.lon, kind: "claimed",
                            label: "Claimed: " + claim.source.text } : null;
    if (claimed) pts.push(claimed);

    findings(state, "prior_appearance").forEach(function (f) {
      var d = f.details || {}, m = d.matched_clip;
      if (!m || m.verified_lat == null || m.verified_lon == null) return;
      var v = { lat: m.verified_lat, lon: m.verified_lon, kind: "verified",
                label: "Verified recording: " + (m.verified_location || m.clip_id) };
      pts.push(v);
      var loc = d.comparison && d.comparison.location;
      if (claimed && loc && loc.distance_km != null) {
        lines.push({ a: claimed, b: v, km: loc.distance_km, bad: loc.result === "disagree" });
      }
    });

    findings(state, "metadata").forEach(function (f) {
      var d = f.details || {};
      (d.files || []).forEach(function (file) {
        if (file.gps_latitude == null || file.gps_longitude == null) return;
        if (pts.some(function (p) { return p.kind === "gps" && p.fileId === file.file_id; })) return;
        var g = { lat: file.gps_latitude, lon: file.gps_longitude, kind: "gps", fileId: file.file_id,
                  label: "File GPS: " + (file.filename || file.file_id) };
        pts.push(g);
        var rows = (d.comparisons || []).filter(function (r) { return r.file_id === file.file_id && r.distance_km != null; });
        if (claimed && rows.length) {
          var best = rows.reduce(function (a, b) { return a.distance_km <= b.distance_km ? a : b; });
          lines.push({ a: claimed, b: g, km: best.distance_km, bad: !best.consistent });
        }
      });
    });

    findings(state, "weather").forEach(function (f) {
      var d = f.details || {};
      if (!d.centre || !d.grid) return;
      grid = { centre: d.centre, points: d.grid };
    });
    return { pts: pts, lines: lines, grid: grid };
  }

  function buildMap(state) {
    var data = mapData(state);
    if (!data.pts.length) return null;
    if (!india) { loadIndia(); return null; }        // drawn as soon as the outline is loaded

    var lats = data.pts.map(function (p) { return p.lat; }).concat([india.bbox[1], india.bbox[3]]);
    var lons = data.pts.map(function (p) { return p.lon; }).concat([india.bbox[0], india.bbox[2]]);
    var minLat = Math.min.apply(null, lats), maxLat = Math.max.apply(null, lats);
    var minLon = Math.min.apply(null, lons), maxLon = Math.max.apply(null, lons);
    var k = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);       // equirectangular, true at mid-latitude
    var S = 20;                                                        // units per degree
    var pad = 1.2;
    var x0 = (minLon - pad) * k * S, y0 = -(maxLat + pad) * S;
    var W = (maxLon - minLon + 2 * pad) * k * S, H = (maxLat - minLat + 2 * pad) * S;
    function X(lon) { return lon * k * S - x0; }
    function Y(lat) { return -lat * S - y0; }
    var fs = H * 0.032;

    var s = svg("svg", { viewBox: "0 0 " + fmt(W, 1) + " " + fmt(H, 1), class: "vmap", role: "img",
                         "aria-label": "Map of the claimed and verified locations" });
    india.rings.forEach(function (ring) {
      var dpath = ring.map(function (p, i) { return (i ? "L" : "M") + fmt(X(p[0]), 1) + " " + fmt(Y(p[1]), 1); }).join("") + "Z";
      s.appendChild(svg("path", { d: dpath, fill: C.elevated, stroke: C.border, "stroke-width": fmt(H * 0.002, 2) }));
    });
    data.lines.forEach(function (ln) {
      var col = ln.bad ? C.contra : C.inconc;
      s.appendChild(svg("line", { x1: X(ln.a.lon), y1: Y(ln.a.lat), x2: X(ln.b.lon), y2: Y(ln.b.lat), stroke: col,
                                  "stroke-width": fmt(H * 0.005, 2), "stroke-dasharray": ln.bad ? "" : fmt(H * 0.012, 1) }));
      var mx = (X(ln.a.lon) + X(ln.b.lon)) / 2, my = (Y(ln.a.lat) + Y(ln.b.lat)) / 2;
      var t = svg("text", { x: mx, y: my - fs * 0.6, "text-anchor": "middle", fill: col, "font-size": fs * 1.15,
                            "font-weight": 600, "paint-order": "stroke", stroke: C.surface, "stroke-width": fs * 0.35 },
                  Math.round(ln.km).toLocaleString("en-IN") + " km");
      s.appendChild(t);
    });
    data.pts.forEach(function (p) {
      var x = X(p.lon), y = Y(p.lat), r = H * 0.016;
      var col = p.kind === "claimed" ? C.accent : (p.kind === "verified" ? C.text : C.supports);
      if (p.kind === "gps") s.appendChild(svg("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r, fill: col }));
      else if (p.kind === "verified") s.appendChild(svg("path", { d: "M" + x + " " + (y - r * 1.3) + "L" + (x + r * 1.3) + " " + y + "L" + x + " " + (y + r * 1.3) + "L" + (x - r * 1.3) + " " + y + "Z", fill: col }));
      else s.appendChild(svg("circle", { cx: x, cy: y, r: r, fill: "none", stroke: col, "stroke-width": r * 0.6 }));
    });

    var c = card("Locations", "Natural Earth outline (India point of view)");
    var wrap = el("div", "vmapwrap");
    wrap.appendChild(s);
    c.appendChild(wrap);
    var legend = el("div", "vlegend");
    data.pts.forEach(function (p) {
      var item = el("span", "vkey k-" + p.kind);
      item.appendChild(el("i"));
      item.appendChild(document.createTextNode(p.label));
      legend.appendChild(item);
    });
    c.appendChild(legend);
    return c;
  }

  function buildGrid(state) {   // the 5 weather points around the claimed place, with their measured mm
    var f = findings(state, "weather").filter(function (x) { return x.details && x.details.centre && x.details.grid; })[0];
    if (!f) return null;
    var grid = { centre: f.details.centre, points: f.details.grid };
    var box = card("Rain at the 5 grid points", "claimed window, mm");
    var s = svg("svg", { viewBox: "0 0 240 170", class: "vgrid" });
    var all = [{ label: "C", lat: grid.centre.lat, lon: grid.centre.lon,
                 mm: grid.centre.window_mm != null ? grid.centre.window_mm : grid.centre.day_mm }]
      .concat(grid.points.map(function (g) { return { label: g.label, lat: g.lat, lon: g.lon,
                                                       mm: g.window_mm != null ? g.window_mm : g.day_mm }; }));
    var dLat = Math.max.apply(null, all.map(function (p) { return Math.abs(p.lat - grid.centre.lat); })) || 1;
    var dLon = Math.max.apply(null, all.map(function (p) { return Math.abs(p.lon - grid.centre.lon); })) || 1;
    all.forEach(function (p) {
      var x = 120 + (p.lon - grid.centre.lon) / dLon * 70, y = 85 - (p.lat - grid.centre.lat) / dLat * 55;
      s.appendChild(svg("circle", { cx: x, cy: y, r: 5, fill: p.label === "C" ? C.accent : C.inconc }));
      // each label on its own side so neighbours never overlap: N above, S/C below, W left, E right
      var pos = { N: [0, -12, "middle"], S: [0, 22, "middle"], C: [0, 22, "middle"], W: [-10, 5, "end"], E: [10, 5, "start"] }[p.label] || [0, -12, "middle"];
      s.appendChild(svg("text", { x: x + pos[0], y: y + pos[1], "text-anchor": pos[2], fill: C.text, "font-size": 14,
                                  "font-family": "IBM Plex Mono, monospace" },
                        p.mm == null ? "n/a" : fmt(p.mm, 1)));
    });
    box.appendChild(s);
    box.appendChild(el("div", "vsub", "centre = claimed place; N/S/E/W about 15 km away"));
    return box;
  }

  function loadIndia() {
    if (indiaLoading) return;
    indiaLoading = true;
    fetch("/live/static/vendor/geo/india.json").then(function (r) { return r.json(); }).then(function (j) {
      india = j;
      if (last) render(last);
    }).catch(function () { indiaLoading = false; });
  }

  // ------------------------------------------------------------------ time
  function timeData(state) {
    var tc = state.claims.filter(function (c) { return c.type === "time" && c.normalized && c.normalized.start; })[0];
    if (!tc) return null;
    var win = { start: new Date(tc.normalized.start), end: new Date(tc.normalized.end), text: tc.source.text };
    var marks = [];
    findings(state, "prior_appearance").forEach(function (f) {
      var d = f.details || {}, cmp = d.comparison && d.comparison.date;
      var vd = (d.matched_clip && d.matched_clip.verified_date) || d.verified_date;
      if (!vd || !cmp || cmp.result === "not_compared") return;
      var day = new Date(vd + "T12:00:00");
      var earlier = day < win.start;
      var gap = cmp.days_apart === 0 ? "within the claimed window (± tolerance)" :
                cmp.days_apart + " day" + (cmp.days_apart === 1 ? "" : "s") + (earlier ? " earlier" : " later");
      marks.push({ at: day, label: "Verified recording", detail: dayLabel(new Date(vd + "T00:00:00")) + " · " + gap,
                   bad: cmp.result === "disagree" });
    });
    findings(state, "metadata").forEach(function (f) {
      ((f.details || {}).comparisons || []).forEach(function (r) {
        if (r.hours_outside == null || !r.capture_time) return;
        if (marks.some(function (m) { return m.fileId === r.file_id; })) return;
        var at = new Date(r.capture_time_claim_tz || r.capture_time);
        var gap = r.side === "inside" ? "inside the claimed window" :
                  fmt(r.hours_outside, 1) + " h" + (r.hours_outside >= 48 ? " (" + fmt(r.hours_outside / 24, 1) + " days)" : "") +
                  " " + (r.side === "after the end" ? "after" : "before") + " the claimed window";
        marks.push({ at: at, fileId: r.file_id, label: "File capture time",
                     detail: dayLabel(at) + " " + clockLabel(at) + " · " + gap + (r.tz_known ? "" : " (time zone unknown)"),
                     bad: r.status === "contradiction" });
      });
    });
    return marks.length ? { win: win, marks: marks } : null;
  }

  function buildTime(state) {
    var data = timeData(state);
    if (!data) return null;
    var times = [data.win.start, data.win.end].concat(data.marks.map(function (m) { return m.at; }))
      .map(function (d) { return d.getTime(); });
    var t0 = Math.min.apply(null, times), t1 = Math.max.apply(null, times);
    var span = Math.max(t1 - t0, 3600e3), padT = span * 0.06;
    var W = 1000, rowH = 70, H = 40 + rowH * (1 + data.marks.length);
    function X(t) { return 30 + (t - (t0 - padT)) / (span + 2 * padT) * (W - 60); }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "vtime" });
    s.appendChild(svg("line", { x1: 30, y1: H - 22, x2: W - 30, y2: H - 22, stroke: C.border, "stroke-width": 2 }));
    var wx0 = X(data.win.start.getTime()), wx1 = Math.max(X(data.win.end.getTime()), wx0 + 8);
    s.appendChild(svg("rect", { x: wx0, y: 22, width: wx1 - wx0, height: H - 44, fill: C.accent, opacity: 0.14 }));
    s.appendChild(svg("rect", { x: wx0, y: 30, width: wx1 - wx0, height: 22, rx: 4, fill: C.accent }));
    var right = wx1 < W * 0.55;                       // near the right edge: put the label on the left
    s.appendChild(svg("text", { x: right ? wx1 + 12 : wx0 - 12, y: 47, "text-anchor": right ? "start" : "end",
                                fill: C.text, "font-size": 20 },
                      "Claimed \u201c" + data.win.text + "\u201d \u00b7 " + dayLabel(data.win.start) +
                      (data.win.end - data.win.start < 86400e3 ? ", " + clockLabel(data.win.start) + "\u2013" + clockLabel(data.win.end) : "")));
    data.marks.forEach(function (m, i) {
      var y = 30 + rowH * (i + 1), x = X(m.at.getTime()), col = m.bad ? C.contra : C.supports;
      var near = Math.abs(x - wx0) < Math.abs(x - wx1) ? wx0 : wx1;
      if (Math.abs(x - near) > 4) {
        s.appendChild(svg("line", { x1: x, y1: y + 11, x2: near, y2: y + 11, stroke: col, "stroke-width": 2, "stroke-dasharray": "6 5" }));
      }
      s.appendChild(svg("circle", { cx: x, cy: y + 11, r: 9, fill: col }));
      var leftSide = x > W / 2;
      s.appendChild(svg("text", { x: leftSide ? x - 16 : x + 16, y: y + 4, "text-anchor": leftSide ? "end" : "start",
                                  fill: C.text, "font-size": 19, "font-weight": 600 }, m.label));
      s.appendChild(svg("text", { x: leftSide ? x - 16 : x + 16, y: y + 28, "text-anchor": leftSide ? "end" : "start",
                                  fill: col, "font-size": 18 }, m.detail));
    });
    s.appendChild(svg("text", { x: 30, y: H - 4, fill: C.text2, "font-size": 15 }, dayLabel(new Date(t0))));
    s.appendChild(svg("text", { x: W - 30, y: H - 4, "text-anchor": "end", fill: C.text2, "font-size": 15 }, dayLabel(new Date(t1))));
    var c = card("Time", "claimed window vs what the evidence says");
    c.appendChild(s);
    return c;
  }

  // ------------------------------------------------------------------ frame filmstrip
  function buildFrames(state) {
    var f = findings(state, "prior_appearance").filter(function (x) {
      return x.details && x.details.frame_strip && (x.details.frame_strip.frames || []).length;
    })[0];
    if (!f) return null;
    var strip = f.details.frame_strip, thr = strip.threshold;
    var maxD = Math.max(16, thr * 2, Math.max.apply(null, strip.frames.map(function (fr) { return fr.hamming; })));
    var matched = strip.frames.filter(function (fr) { return fr.matched; }).length;
    var c = card("Frames vs archive clip " + strip.clip_id,
                 matched + " of " + strip.frames.length + " sampled frames within Hamming " + thr);
    var row = el("div", "vstrip");
    strip.frames.forEach(function (fr) {
      var cell = el("figure", "vframe" + (fr.matched ? " ok" : ""));
      var img = el("img");
      img.src = fileUrl(state.id, fr.thumb);
      img.alt = "Frame at " + fmt(fr.t, 2) + " s";
      cell.appendChild(img);
      var bar = el("div", "vbar");
      var fill = el("i");
      fill.style.width = Math.max(4, 100 * fr.hamming / maxD) + "%";
      bar.appendChild(fill);
      var tick = el("b");
      tick.style.left = (100 * thr / maxD) + "%";
      bar.appendChild(tick);
      cell.appendChild(bar);
      cell.appendChild(el("figcaption", "mono", fmt(fr.t, 2) + " s \u00b7 " + fr.hamming));
      row.appendChild(cell);
    });
    c.appendChild(row);
    return c;
  }

  // ------------------------------------------------------------------ rain bars
  function buildRain(state) {
    var f = findings(state, "weather").filter(function (x) {
      return x.details && (x.details.hourly_centre || []).length;
    })[0];
    if (!f) return null;
    var d = f.details, hours = d.hourly_centre;
    var ws = d.claimed_window ? new Date(d.claimed_window.start).getTime() : null;
    var we = d.claimed_window ? new Date(d.claimed_window.end).getTime() : null;
    var maxMm = Math.max(1, Math.max.apply(null, hours.map(function (h) { return h.mm || 0; })));
    var W = 600, H = 220, bw = (W - 40) / hours.length;
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "vchart" });
    hours.forEach(function (h, i) {
      var t = new Date(h.t).getTime(), x = 30 + i * bw;
      var inWin = ws != null && t > ws && t <= we;       // hourly value covers the preceding hour
      if (inWin) s.appendChild(svg("rect", { x: x, y: 10, width: bw, height: H - 50, fill: C.accent, opacity: 0.14 }));
      var bh = (h.mm || 0) / maxMm * (H - 60);
      s.appendChild(svg("rect", { x: x + 1, y: H - 40 - Math.max(bh, 1.5), width: bw - 2, height: Math.max(bh, 1.5),
                                  fill: (h.mm || 0) > 0 ? C.accent : C.border }));
    });
    s.appendChild(svg("line", { x1: 30, y1: H - 40, x2: W - 10, y2: H - 40, stroke: C.border }));
    [0, Math.floor(hours.length / 2), hours.length - 1].forEach(function (i) {
      var t = new Date(hours[i].t);
      s.appendChild(svg("text", { x: 30 + i * bw + bw / 2, y: H - 18, "text-anchor": "middle", fill: C.text2, "font-size": 15 },
                        clockLabel(t)));
    });
    s.appendChild(svg("text", { x: 30, y: 24, fill: C.text2, "font-size": 15 }, "max " + fmt(maxMm, 1) + " mm/h"));
    var sub = (d.centre && d.centre.window_mm != null ? fmt(d.centre.window_mm, 1) + " mm in the claimed window" : "") +
              (d.prior_72h_mm != null ? " \u00b7 " + fmt(d.prior_72h_mm, 1) + " mm in the 72 h before" : "");
    var c = card("Rain at the claimed place, hour by hour", sub);
    c.appendChild(s);
    return c;
  }

  // ------------------------------------------------------------------ lip-sync curves
  function buildLipsync(state) {
    var ev = artifact(state, "lipsync", function (r) { return /lipsync_series\.json$/.test(r); });
    if (!ev) return null;
    var key = state.id + "/" + ev.ref;
    if (!series[key]) {
      series[key] = "loading";
      fetch(fileUrl(state.id, ev.ref)).then(function (r) { return r.json(); }).then(function (j) {
        series[key] = j;
        if (last && last.id === state.id) render(last);
      }).catch(function () { series[key] = "failed"; });
      return null;
    }
    var j = series[key];
    if (!j || typeof j !== "object" || !(j.t || []).length) return null;
    function norm(a) {
      var v = a.filter(function (x) { return x != null && isFinite(x); });
      var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), r = (hi - lo) || 1;
      return a.map(function (x) { return x == null || !isFinite(x) ? null : (x - lo) / r; });
    }
    var W = 600, H = 220, t = j.t, tMax = t[t.length - 1] || 1;
    // two lanes, so neither curve hides the other: mouth on top, audio below
    function line(vals, col, top, height) {
      var dpath = "", pen = false;
      vals.forEach(function (v, i) {
        if (v == null) { pen = false; return; }
        dpath += (pen ? "L" : "M") + fmt(20 + t[i] / tMax * (W - 40), 1) + " " + fmt(top + height - v * height, 1);
        pen = true;
      });
      return svg("path", { d: dpath, fill: "none", stroke: col, "stroke-width": 2 });
    }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "vchart" });
    (j.speech || []).forEach(function (sp, i) {
      if (sp && i + 1 < t.length) {
        s.appendChild(svg("rect", { x: 20 + t[i] / tMax * (W - 40), y: 14, width: (t[i + 1] - t[i]) / tMax * (W - 40) + 0.5,
                                    height: H - 44, fill: C.text2, opacity: 0.08 }));
      }
    });
    s.appendChild(line(norm(j.mouth), C.accent, 18, 78));
    s.appendChild(line(norm(j.audio), C.inconc, 108, 78));
    s.appendChild(svg("line", { x1: 20, y1: 102, x2: W - 20, y2: 102, stroke: C.border, "stroke-dasharray": "4 4" }));
    s.appendChild(svg("text", { x: 20, y: H - 8, fill: C.text2, "font-size": 15 }, "0 s"));
    s.appendChild(svg("text", { x: W - 20, y: H - 8, "text-anchor": "end", fill: C.text2, "font-size": 15 }, fmt(tMax, 1) + " s"));
    var f = findings(state, "lipsync")[0] || {}, d = f.details || {};
    var sub = (d.lag_s != null ? "best lag " + fmt(d.lag_s, 2) + " s" : "") + (d.corr != null ? " \u00b7 correlation " + fmt(d.corr, 2) : "");
    var c = card("Lip-sync: mouth movement vs speech loudness", sub);
    c.appendChild(s);
    var legend = el("div", "vlegend");
    [["k-mouth", "mouth (" + (j.mouth_feature || "aperture") + ")"], ["k-audio", "audio " + (j.audio_unit || "")],
     ["k-speech", "speech"]].forEach(function (p) {
      var it = el("span", "vkey " + p[0]); it.appendChild(el("i")); it.appendChild(document.createTextNode(p[1])); legend.appendChild(it);
    });
    c.appendChild(legend);
    return c;
  }

  // ------------------------------------------------------------------ witness images
  function buildImage(state, witness, test, title, sub) {
    var ev = artifact(state, witness, test);
    if (!ev) return null;
    var c = card(title, sub);
    var img = el("img", "vimg");
    img.src = fileUrl(state.id, ev.ref);
    img.alt = ev.label || title;
    c.appendChild(img);
    return c;
  }

  // ------------------------------------------------------------------ provenance
  function buildProvenance(state) {
    if (!state.inputs.length && !state.log.length) return null;
    var c = card("Provenance", "originals stored read-only; events as the pipeline logs them");
    state.inputs.forEach(function (i) {
      var r = el("div", "vhash");
      r.appendChild(el("span", "vhash-name", i.name));
      r.appendChild(el("span", "mono", "SHA-256 " + i.sha256));
      c.appendChild(r);
    });
    var chips = el("div", "vchips");
    state.log.forEach(function (m) {
      var chip = el("span", "vchip");
      var tm = /T(\d\d:\d\d:\d\d)(\.\d{1,3})?/.exec(m.ts || "");
      chip.appendChild(el("span", "mono", tm ? tm[1] + (tm[2] || "") : ""));
      chip.appendChild(document.createTextNode(" " + m.event + (m.event.indexOf("witness") === 0 && m.detail ? " \u00b7 " + m.detail.split(":")[0] : "")));
      chips.appendChild(chip);
    });
    c.appendChild(chips);
    return c;
  }

  // ------------------------------------------------------------------ render
  var BUILDERS = {
    map: buildMap,
    time: buildTime,
    frames: buildFrames,
    rain: buildRain,
    grid: buildGrid,
    lipsync: buildLipsync,
    enf: function (s) { return buildImage(s, "enf", function (r) { return /\.png$/.test(r); },
                                          "Power-grid hum spectrogram", "display only, never a verdict"); },
    ela: function (s) { return buildImage(s, "ela", function (r) { return /\.png$/.test(r); },
                                          "Error-level heatmap", "visual aid only"); },
    provenance: buildProvenance
  };

  function signature(key, state) {   // rebuild a card only when its inputs change
    var f = function (w) { return JSON.stringify(findings(state, w)); };
    return {
      map: JSON.stringify(state.claims) + f("prior_appearance") + f("metadata") + f("weather") + !!india,
      time: JSON.stringify(state.claims) + f("prior_appearance") + f("metadata"),
      frames: f("prior_appearance"),
      rain: f("weather"),
      grid: f("weather"),
      lipsync: f("lipsync") + JSON.stringify(Object.keys(series).map(function (k) { return typeof series[k]; })),
      enf: f("enf"),
      ela: f("ela"),
      provenance: JSON.stringify(state.inputs) + state.log.length
    }[key];
  }

  function render(state) {
    last = state;
    if (!root) return;
    ORDER.forEach(function (key, idx) {
      var sig = state.id + "|" + signature(key, state);
      var have = cards[key];
      if (have && have.sig === sig) return;
      var node = null;
      try { node = BUILDERS[key](state); } catch (e) { node = null; }     // a view never breaks the desk
      if (have) { have.el.remove(); delete cards[key]; }
      if (!node) return;
      node.classList.add("v-" + key);
      node.style.order = idx;
      root.appendChild(node);
      cards[key] = { el: node, sig: sig };
    });
    if (onChange) onChange(Object.keys(cards).length);
  }

  function reset() {
    Object.keys(cards).forEach(function (k) { cards[k].el.remove(); });
    cards = {};
    last = null;
    if (onChange) onChange(0);
  }

  window.AlibiViews = {
    init: function (container, changed) { root = container; onChange = changed; loadIndia(); },
    render: render,
    reset: reset
  };
})();
