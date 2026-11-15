// The look chosen in the app (Evening or Day), so an older page matches it. Automatic follows the device.
// Loaded before the page draws, so it never flashes the other look.
try {
  var look = localStorage.getItem("hub.look");
  if (look === "dusk" || look === "day") document.documentElement.dataset.theme = look;
} catch (e) { /* storage blocked: the device's look it is */ }
