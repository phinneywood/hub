(function () {
  function start() { document.body.classList.add("loading"); }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a");
    if (!a || !a.href || a.target === "_blank" || e.metaKey || e.ctrlKey || e.shiftKey) return;
    if (a.origin !== location.origin || a.pathname === location.pathname && a.hash) return;
    start();
  });
  document.addEventListener("submit", start);
  window.addEventListener("pageshow", function () { document.body.classList.remove("loading"); });
})();
document.addEventListener("DOMContentLoaded", function () {
  var form = document.querySelector("form[data-autosubmit]");
  if (form) setTimeout(function () { form.submit(); }, 400);
});
document.addEventListener("DOMContentLoaded", function () {
  var on = document.querySelector(".shell-nav a.on");
  if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest", inline: "center" });
});
