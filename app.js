let deferredInstallPrompt = null;
const installButton = document.getElementById("installApp");
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/service-worker.js").catch(console.error));
}
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  if (installButton) installButton.hidden = false;
});
if (installButton) {
  installButton.addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    installButton.hidden = true;
  });
}
window.addEventListener("appinstalled", () => {
  if (installButton) installButton.hidden = true;
});
