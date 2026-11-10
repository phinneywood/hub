// Refresh is optional; never interrupt source selection or a hidden tab.
const auto = document.getElementById('network-auto');
let editing = false;
document.querySelectorAll('.source-form').forEach(form => {
  form.addEventListener('input', () => { editing = true; });
  form.addEventListener('change', () => { editing = true; });
});
setInterval(() => {
  if (auto?.checked && !editing && !document.hidden && !document.getElementById('sources')?.open) {
    window.location.reload();
  }
}, 60000);
