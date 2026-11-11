// Insights: one tooltip for every chart mark that carries data-tip. It only shows what the page's
// tables and labels already say, and it is filled with textContent, never markup.
(function () {
  var tip = document.getElementById("viz-tip"), on = null;
  if (!tip) return;
  function hide() {
    if (on) on.classList.remove("on");
    on = null; tip.hidden = true;
  }
  function show(mark, x, y) {
    if (on !== mark) { hide(); on = mark; mark.classList.add("on"); tip.textContent = mark.getAttribute("data-tip"); }
    tip.hidden = false;
    var width = tip.offsetWidth, room = document.documentElement.clientWidth;
    tip.style.left = Math.max(8, Math.min(x + 14, room - width - 8)) + window.scrollX + "px";
    tip.style.top = Math.max(8, y - tip.offsetHeight - 12) + window.scrollY + "px";
  }
  function over(e) {
    var mark = e.target.closest ? e.target.closest("[data-tip]") : null;
    if (mark) show(mark, e.clientX, e.clientY); else hide();
  }
  document.addEventListener("pointermove", over);
  document.addEventListener("pointerdown", over);
  document.addEventListener("scroll", hide, { passive: true });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
})();
