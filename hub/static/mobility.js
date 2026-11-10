"use strict";
(function () {
  function mount() {
    var form = document.getElementById("trip-form");
    if (!form) return;
    document.getElementById("swap-places").addEventListener("click", function () {
      var origin = document.getElementById("origin");
      var destination = document.getElementById("destination");
      var previous = origin.value;
      origin.value = destination.value;
      destination.value = previous;
      origin.focus();
    });
    document.getElementById("leave-now").addEventListener("click", function () {
      document.getElementById("departure").value = "";
    });
    var sorter = document.getElementById("route-sort");
    if (sorter) sorter.addEventListener("change", function () {
      var list = document.getElementById("route-list");
      var field = { fastest: "minutes", riding: "riding", transfers: "transfers" }[sorter.value];
      Array.from(list.children).sort(function (a, b) {
        return Number(a.dataset[field]) - Number(b.dataset[field]) || Number(a.dataset.minutes) - Number(b.dataset.minutes);
      }).forEach(function (card) { list.appendChild(card); });
    });
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (form.dataset.busy) return;
      form.dataset.busy = "true";
      var data = new FormData(form);
      var controls = form.querySelectorAll("input, select, button");
      controls.forEach(function (control) { control.disabled = true; });
      form.setAttribute("aria-busy", "true");
      var progress = document.getElementById("search-progress");
      var error = document.getElementById("search-error");
      progress.hidden = false;
      error.hidden = true;
      var start = Date.now();
      var timer = window.setInterval(function () {
        var seconds = Math.floor((Date.now() - start) / 1000);
        progress.textContent = (seconds < 15 ? "Checking ride routes and reachable buses… " : "Still comparing connections—no need to search again. ") + seconds + "s";
      }, 1000);
      try {
        var response = await fetch(form.action, { method: "POST", body: data, credentials: "same-origin", redirect: "error", headers: { Accept: "text/html" } });
        if (!response.ok) throw new Error("Search request failed");
        var documentResult = new DOMParser().parseFromString(await response.text(), "text/html");
        var main = documentResult.querySelector("main.mobility-main");
        if (!main) throw new Error("Unexpected response");
        document.querySelector("main.mobility-main").replaceWith(main);
        mount();
        var target = document.getElementById("trip-results");
        var problem = document.getElementById("search-error");
        if (problem && !problem.hidden) { problem.setAttribute("tabindex", "-1"); problem.focus(); }
        else if (target) { target.focus({ preventScroll: true }); target.scrollIntoView({ behavior: "auto", block: "start" }); }
      } catch (_) {
        error.textContent = "The search connection was interrupted. It may still be finishing on the Mac. Wait a moment before trying again; your places are still here.";
        error.hidden = false;
      } finally {
        window.clearInterval(timer);
        document.body.classList.remove("loading");
        controls.forEach(function (control) { control.disabled = false; });
        progress.hidden = true;
        form.removeAttribute("aria-busy");
        delete form.dataset.busy;
      }
    });
  }
  mount();
})();
