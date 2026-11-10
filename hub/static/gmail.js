// Keyboard shortcuts on the sender card: A / F / K / L press the matching choice.
document.addEventListener("keydown", function (e) {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
  var t = e.target;
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
  var form = document.querySelector("form[data-shortcuts]");
  if (!form) return;
  var button = form.querySelector('[data-key="' + e.key.toLowerCase() + '"]');
  if (!button) return;
  e.preventDefault();
  button.click();
});
