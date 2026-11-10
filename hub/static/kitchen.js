(function () {
  var lastId = 0, timer = null, planCards = {}, loaded = false;
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function link(url, text, cls) {
    var a = el("a", cls || "", text);
    if (/^https:\/\//.test(url)) { a.href = url; a.target = "_blank"; a.rel = "noopener"; }
    return a;
  }

  function api(method, path, body) {
    return fetch("/kitchen" + path, {
      method: method, headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().then(function (d) { if (r.status === 502) throw d; d._status = r.status; return d; });
    });
  }

  function clock(at) {
    var d = new Date(at * 1000), today = new Date().toDateString() === d.toDateString();
    var t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return today ? t : d.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + t;
  }

  // Replies are meant to be plain text. As a safety net, tidy stray Markdown and make links tappable,
  // building DOM nodes only (never HTML strings).
  function inline(parent, line) {
    line = line.replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1").replace(/^#{1,6}\s+/, "");
    var re = /\[([^\]]+)\]\((https:\/\/[^\s)]+)\)|(https:\/\/[^\s)]+)/g, at = 0, m;
    while ((m = re.exec(line))) {
      parent.appendChild(document.createTextNode(line.slice(at, m.index)));
      parent.appendChild(link(m[2] || m[3], m[1] || m[3]));
      at = re.lastIndex;
    }
    parent.appendChild(document.createTextNode(line.slice(at)));
  }

  function textBlock(parent, text) {
    text.split(/\n{2,}/).forEach(function (para) {
      if (!para.trim()) return;
      var p = el("p");
      para.split("\n").forEach(function (line, i) {
        if (i) p.appendChild(document.createTextNode("\n"));
        inline(p, line);
      });
      parent.appendChild(p);
    });
  }

  function send(text) {
    var box = $("k-text");
    box.disabled = true;
    return api("POST", "/messages", { text: text }).then(function (d) {
      if (d.error) { alert(d.error); return false; }
      $("k-empty").hidden = true;
      $("k-working").hidden = false;
      poll();
      return true;
    }, down).then(function (ok) {
      box.disabled = false;
      return ok;
    });
  }

  function recipeCard(r) {
    var c = el("div", "k-card");
    c.appendChild(r.url ? link(r.url, r.title, "k-card-title") : el("strong", "k-card-title", r.title));
    var facts = el("div", "k-facts");
    [r.active && "Active " + r.active, r.total && "Total " + r.total, r.dishes].forEach(function (f) {
      if (f) facts.appendChild(el("span", "k-fact", f));
    });
    if (facts.childNodes.length) c.appendChild(facts);
    if (r.diet) c.appendChild(el("div", "k-diet" + (/swap|check|leave out|without|side/i.test(r.diet) ? " warn" : ""), r.diet));
    var on = (r.recent || r.check) ? [["Bought recently", r.recent], ["Check your shelf", r.check]] : [["Have", r.have]];
    on.concat([["Need", r.need]]).forEach(function (pair) {
      if (!pair[1]) return;
      var line = el("div", "k-line");
      line.appendChild(el("b", "", pair[0] + ": "));
      line.appendChild(document.createTextNode(pair[1]));
      c.appendChild(line);
    });
    var add = el("button", "k-pill", "Add missing to cart");
    add.type = "button";
    add.onclick = function () {
      add.disabled = true;
      send("Plan the ingredients we still need for “" + r.title + "”.");
    };
    c.appendChild(add);
    return c;
  }

  function price(v) { var n = parseFloat(String(v || "").replace(/[^0-9.]/g, "")); return isNaN(n) ? null : n; }

  function planState(card, plan) {
    var acts = card.querySelector(".k-plan-acts");
    acts.textContent = "";
    if (plan.status === "pending") {
      [["approve", "Add to cart", "k-go"], ["dismiss", "Not now", ""]].forEach(function (b) {
        var btn = el("button", b[2], b[1]);
        btn.type = "button";
        btn.onclick = function () {
          acts.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
          api("POST", "/plans/" + plan.id + "/" + b[0]).then(function (d) {
            if (d.error) { alert(d.error); acts.querySelectorAll("button").forEach(function (x) { x.disabled = false; }); }
            poll();
          }, down);
        };
        acts.appendChild(btn);
      });
    } else {
      var words = { approved: "Approved, adding to the cart…", done: "Added to the cart", dismissed: "Dismissed",
                    superseded: "Replaced by a newer plan", expired: "Expired", unknown: "Not confirmed: check the cart" };
      acts.appendChild(el("span", "k-plan-status" + (plan.status === "done" ? " ok" : ""),
        (words[plan.status] || plan.status) + (plan.decided_by ? " · " + plan.decided_by : "")));
    }
  }

  function planCard(plan) {
    var c = el("div", "k-card k-plan");
    c.appendChild(el("strong", "k-card-title", "Cart plan"));
    var ul = el("ul", "k-plan-items"), total = 0, priced = 0;
    plan.items.forEach(function (i) {
      var li = el("li"), what = el("span", "k-what");
      what.appendChild(i.url ? link(i.url, i.product) : el("span", "", i.product));
      what.appendChild(el("small", "", [i.size, "× " + i.qty].filter(Boolean).join(" · ")));
      li.appendChild(what);
      var p = price(i.price);
      if (p !== null) { total += p * i.qty; priced++; }
      li.appendChild(el("span", "k-price", p !== null ? "$" + (p * i.qty).toFixed(2) : ""));
      ul.appendChild(li);
    });
    c.appendChild(ul);
    if (priced) {
      var t = el("div", "k-total");
      t.appendChild(el("span", "", "Estimate" + (priced < plan.items.length ? " (some unpriced)" : "")));
      t.appendChild(el("span", "", "$" + total.toFixed(2)));
      c.appendChild(t);
    }
    c.appendChild(el("div", "k-plan-acts"));
    c.appendChild(el("span", "k-small", "Adds to your Fred Meyer cart only. You check out."));
    planState(c, plan);
    planCards[plan.id] = c;
    return c;
  }

  // The cart report after an add. Totals come from the service, which computes them from the lines.
  function resultCard(r) {
    var c = el("div", "k-card k-result");
    c.appendChild(el("strong", "k-card-title", r.in_cart + " of " + r.planned + " in the cart"));
    var ul = el("ul", "k-plan-items"), words = { added: "Added", already: "Already in cart", unavailable: "Unavailable" };
    r.lines.forEach(function (l) {
      var li = el("li", l.status === "unavailable" ? "k-gone" : ""), what = el("span", "k-what");
      what.appendChild(el("span", "", l.product));
      what.appendChild(el("small", "", [words[l.status], "× " + l.qty,
        l.was !== null ? "was $" + l.was.toFixed(2) : ""].filter(Boolean).join(" · ")));
      li.appendChild(what);
      li.appendChild(el("span", "k-price", l.price !== null && l.status !== "unavailable" ? "$" + (l.price * l.qty).toFixed(2) : ""));
      ul.appendChild(li);
    });
    c.appendChild(ul);
    [["Plan items" + (r.unpriced ? " (some unpriced)" : ""), r.total], ["Cart subtotal shown", r.cart_subtotal]].forEach(function (t) {
      if (t[1] === null || t[1] === undefined) return;
      var row = el("div", "k-total");
      row.appendChild(el("span", "", t[0]));
      row.appendChild(el("span", "", "$" + t[1].toFixed(2)));
      c.appendChild(row);
    });
    c.appendChild(link("https://www.fredmeyer.com/cart", "Open the cart", "k-pill"));
    return c;
  }

  function render(m) {
    var li = el("li", "k-msg k-" + m.role);
    if (m.role === "notice") {
      li.textContent = m.text;
    } else {
      // With a result card, skip the service's one-paragraph summary of the same numbers.
      var text = m.result ? m.text.split(/\n{2,}/).slice(1).join("\n\n") : m.text;
      if (text) textBlock(li, text);
      (m.recipes || []).forEach(function (r) { li.appendChild(recipeCard(r)); });
      if (m.plan) li.appendChild(planCard(m.plan));
      if (m.result) li.appendChild(resultCard(m.result));
      li.appendChild(el("span", "k-meta", (m.role === "member" ? m.author : "Kitchen") + " · " + clock(m.at)));
    }
    $("k-thread").appendChild(li);
  }

  function down() { $("k-down").hidden = false; }

  function poll() {
    clearTimeout(timer);
    api("GET", "/messages?after=" + lastId).then(function (d) {
      $("k-down").hidden = true;
      var nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
      var fresh = d.messages || [];
      fresh.forEach(function (m) { render(m); lastId = Math.max(lastId, m.id); });
      Object.keys(d.plans || {}).forEach(function (id) {
        if (planCards[id]) planState(planCards[id], { id: +id, status: d.plans[id].status, decided_by: d.plans[id].decided_by });
      });
      var working = !!(d.busy || d.queued);
      $("k-working").hidden = !working;
      $("k-empty").hidden = lastId > 0 || working;
      if (fresh.length) loadList();
      if (fresh.length && (nearBottom || !loaded)) window.scrollTo(0, document.body.scrollHeight);
      loaded = true;
    }, down).then(function () {
      timer = setTimeout(poll, document.hidden ? 15000 : 3000);
    });
  }

  function loadList() {
    api("GET", "/list").then(function (d) {
      var items = (d.text || "").split("\n").map(function (l) {
        return l.replace(/<!--.*?-->/g, "").replace(/^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?/, "").trim();
      }).filter(function (l, i, all) { return l && !/^#/.test(l); });
      var ul = $("k-list-items");
      ul.textContent = "";
      items.forEach(function (t) { var li = el("li"); inline(li, t); ul.appendChild(li); });
      if (!items.length) ul.appendChild(el("li", "k-none", "Nothing on the list. Say “add eggs” to start one."));
      $("k-list-count").textContent = items.length ? items.length + (items.length === 1 ? " item" : " items") : "Empty";
    }, function () {});
  }

  function keyBytes(b64) {
    var s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
    return Uint8Array.from(s, function (c) { return c.charCodeAt(0); });
  }

  function setupNotifications() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !window.Notification) return;
    api("GET", "/status").then(function (s) {
      if (!s.push_public_key) return;
      navigator.serviceWorker.register("/kitchen/sw.js", { scope: "/kitchen/" }).then(function (reg) {
        return reg.pushManager.getSubscription().then(function (sub) {
          if (sub && Notification.permission === "granted") return save(sub);
          if (Notification.permission === "denied") return;
          $("k-notify").hidden = false;
          $("k-notify-btn").onclick = function () {
            Notification.requestPermission().then(function (p) {
              if (p !== "granted") return;
              return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(s.push_public_key) })
                .then(save).then(function () { $("k-notify").hidden = true; });
            });
          };
        });
      });
    }, function () {});
    function save(sub) { return api("POST", "/subscribe", sub.toJSON()); }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var box = $("k-text"), form = $("k-compose");
    function grow() { box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 144) + "px"; }
    box.addEventListener("input", grow);
    box.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      e.stopPropagation();  // keep the page's loading dim off: this form doesn't navigate
      var text = box.value.trim();
      if (!text) return;
      send(text).then(function (ok) { if (ok) { box.value = ""; grow(); } box.focus(); });
    });
    $("k-suggest").addEventListener("click", function (e) {
      if (e.target.tagName === "BUTTON") send(e.target.textContent);
    });
    $("k-learn").addEventListener("click", function () {
      var b = this;
      b.disabled = true;
      api("POST", "/learn").then(function (d) { if (d.error) alert(d.error); poll(); }, down)
        .then(function () { setTimeout(function () { b.disabled = false; }, 5000); });
    });
    loadList();
    poll();
    setupNotifications();
  });
})();
