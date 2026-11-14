/* Music on every Hub page but Ask: a mini-player bar while something plays, opening to the same controls as
   Ask's Now playing card, and the Music screen (/house/music) with every speaker. Both read /house/now (the
   speakers' live state through the broker) while the page is visible, and every tap posts to the signed-in
   /house/now/<speaker> route, which checks it and records it with Undo like any other change.
   Speaker and song names are data: they are only ever set as text, never as markup. */
(function () {
  "use strict";
  var POLL = 4000, SETTLE = 450, DOWN = "Hub isn’t answering right now.";
  var bar = document.getElementById("mp-bar"), screen = document.querySelector("[data-music-screen]");
  if (!bar && !screen) return;
  var memo = {};          // per group leader: {said, batch, error}, kept across redraws
  var hold = 0;           // sliders being moved or taps in flight: no redraw meanwhile
  var open = false;

  function h(tag, props) {
    var el = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === "text") el.textContent = props[k];
      else if (k === "on") Object.keys(props.on).forEach(function (e) { el.addEventListener(e, props.on[e]); });
      else if (k === "cls") el.className = props[k];
      else if (props[k] !== null && props[k] !== undefined && props[k] !== false) el.setAttribute(k, props[k] === true ? "" : props[k]);
    });
    for (var i = 2; i < arguments.length; i++) if (arguments[i]) el.append(arguments[i]);
    return el;
  }
  function answer(r) {
    return r.json().catch(function () { return {}; }).then(function (b) { if (!r.ok) throw new Error(b.error || DOWN); return b; });
  }
  function post(path, form) {
    return fetch(path, { method: "POST", body: new URLSearchParams(form),
      headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" } }).then(answer);
  }
  var ICON = { pause: "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z", play: "M8 5l11 7-11 7z", next: "M5 5l9 7-9 7zM16 5h3v14h-3z" };
  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), path = document.createElementNS(svg.namespaceURI, "path");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", "22"); svg.setAttribute("height", "22"); svg.setAttribute("aria-hidden", "true");
    path.setAttribute("d", ICON[name]); path.setAttribute("fill", "currentColor"); svg.append(path);
    return svg;
  }
  function rooms(g) { return g.speakers.filter(function (s) { return s.in; }); }
  function title(g) { return g.title || (g.playing || g.state === "paused" ? "Music" : "Nothing playing"); }

  // One tap: post it, keep what it said (and its Undo) for this group, then read again.
  function act(g, via, form) {
    var note = memo[g.leader] = {};
    hold++;
    form.via = via;
    return post("/house/now/" + encodeURIComponent(g.leader), form).then(function (d) { note.said = d.said; note.batch = d.batch; },
      function (e) { note.error = e.message; }).then(function () { hold--; read(); });
  }
  function undo(g, batch) {
    var note = memo[g.leader] = {};
    hold++;
    post("/house/undo/" + encodeURIComponent(batch), {}).then(function (d) { note.said = "Put back. " + d.said; },
      function (e) { note.error = e.message; }).then(function () { hold--; read(); });
  }

  function level(label, value, send) {
    var timer = null, out = h("output", { text: value === null ? "–" : value + "%" });
    var input = h("input", { type: "range", min: 0, max: 100, step: 1, value: value === null ? 0 : value,
      "aria-label": "Volume: " + label, disabled: value === null });
    function go() { clearTimeout(timer); timer = null; send(Number(input.value)).then(function () { hold--; read(); }); }
    input.addEventListener("input", function () {
      out.textContent = input.value + "%";
      if (timer === null) hold++;
      clearTimeout(timer); timer = setTimeout(go, SETTLE);
    });
    input.addEventListener("pointerup", function () { if (timer !== null) go(); });
    return h("label", { cls: "mp-level" }, h("span", { text: label }), input, out);
  }

  // A group's controls, as on Ask's card: what plays, play/pause and next, sliders, a chip per speaker.
  function panel(g, via, link) {
    var group = rooms(g), note = memo[g.leader] || {};
    var box = h("section", { cls: "mp-panel", "aria-label": "Music in " + group.map(function (s) { return s.name; }).join(", ") },
      h("div", { cls: "mp-head" }, h("span", { cls: "mp-art", "aria-hidden": "true", text: "♪" }),
        h("span", { cls: "mp-what" }, h("small", { text: g.playing ? "Now playing" : g.label }), h("b", { text: title(g) }),
          (g.artist || g.album) && h("span", { text: [g.artist, g.album].filter(Boolean).join(" · ") }))),
      h("div", { cls: "mp-keys" },
        h("button", { type: "button", cls: "mp-key mp-go", "aria-label": g.playing ? "Pause" : "Play",
          on: { click: function () { act(g, via, { action: g.playing ? "pause" : "play" }); } } }, icon(g.playing ? "pause" : "play")),
        h("button", { type: "button", cls: "mp-key", "aria-label": "Next", disabled: !g.playing,
          on: { click: function () { act(g, via, { action: "next" }); } } }, icon("next"))));
    if (group.length > 1) box.append(level("All rooms", g.volume, function (v) { return act(g, via, { action: "group_volume", value: v }); }));
    group.forEach(function (s) {
      box.append(level(s.name, s.offline ? null : s.volume, function (v) { return act(g, via, { action: "volume", target: s.id, value: v }); }));
    });
    var chips = h("div", { cls: "mp-rooms", role: "group", "aria-label": "Rooms" });
    g.speakers.forEach(function (s) {
      chips.append(h("button", { type: "button", cls: "mp-room" + (s.in ? " in" : ""), "aria-pressed": String(s.in), disabled: s.offline,
        title: s.zone.length ? "Plays together with its zone" : null, text: (s.in ? "✓ " : "+ ") + s.name,
        on: { click: function () { act(g, via, { action: s.in ? "remove" : "add", target: s.id }); } } }));
    });
    box.append(chips);
    if (note.said) {
      box.append(h("p", { cls: "mp-said" }, h("span", { text: note.said }), note.batch && h("button", { type: "button", cls: "mp-undo",
        text: "Undo", on: { click: function () { undo(g, note.batch); } } })));
    }
    if (note.error) box.append(h("p", { cls: "mp-said bad", text: note.error }));
    if (link) box.append(h("a", { cls: "mp-away", href: link, target: "_blank", rel: "noopener", text: "Open in Music Assistant ↗" }));
    return box;
  }

  // ---- The mini-player: one line while something plays; tapping it opens every playing group's controls ----
  function drawBar(d) {
    var on = d.groups.filter(function (g) { return g.playing || g.state === "paused"; });
    bar.hidden = !on.length;
    document.body.classList.toggle("mp-on", on.length > 0);
    if (!on.length) { open = false; bar.replaceChildren(); return; }
    var g = on[0], names = rooms(g).map(function (s) { return s.name; }).join(", ");
    var line = h("div", { cls: "mp-line" },
      h("button", { type: "button", cls: "mp-open", "aria-expanded": String(open), "aria-controls": "mp-sheet",
        on: { click: function () { open = !open; drawBar(d); } } },
        h("span", { cls: "mp-art small", "aria-hidden": "true", text: "♪" }),
        h("span", { cls: "mp-what" }, h("b", { text: title(g) }),
          h("span", { text: names + (on.length > 1 ? " · " + (on.length - 1) + " more" : "") }))),
      h("button", { type: "button", cls: "mp-key mp-go", "aria-label": g.playing ? "Pause" : "Play",
        on: { click: function () { act(g, "bar", { action: g.playing ? "pause" : "play" }); } } }, icon(g.playing ? "pause" : "play")));
    var parts = [line];
    if (open) {
      var sheet = h("div", { cls: "mp-sheet", id: "mp-sheet" });
      on.forEach(function (one) { sheet.append(panel(one, "bar", d.open)); });
      sheet.append(h("a", { cls: "mp-away", href: "/house/music", text: "All speakers ›" }));
      parts.push(sheet);
    }
    bar.replaceChildren.apply(bar, parts);
  }

  function drawScreen(d) {
    screen.replaceChildren.apply(screen, d.groups.length ? d.groups.map(function (g) { return panel(g, "music", d.open); })
      : [h("p", { cls: "note", text: "Hub doesn’t see any Music Assistant speakers." })]);
  }

  function draw(d) {
    if (bar) drawBar(d);
    if (screen) drawScreen(d);
  }

  function read() {
    if (document.hidden) return Promise.resolve();
    return fetch("/house/now", { headers: { Accept: "application/json" } }).then(answer).then(function (d) {
      if (!hold) draw(d);
    }, function (e) {
      if (screen && !hold) screen.replaceChildren(h("p", { cls: "banner warn", text: e.message }));
    });
  }
  function loop() { read().then(function () { setTimeout(loop, POLL); }); }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) read(); });
  loop();
})();
