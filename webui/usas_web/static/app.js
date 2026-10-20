// Progressive enhancement only: every page works without this script.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // Move focus to the error summary so screen readers announce it and keyboard users start there.
  const summary = document.getElementById("error-summary");
  if (summary) summary.focus();

  // Show times in the viewer's local time zone.
  for (const el of document.querySelectorAll("time[data-epoch]")) {
    const date = new Date(Number(el.dataset.epoch) * 1000);
    el.dateTime = date.toISOString();
    el.textContent = date.toLocaleString();
  }

  // The live run log: append output as it arrives, keep the view at the bottom unless the user scrolled up.
  const log = document.getElementById("log");
  if (log && log.dataset.stream && window.EventSource) {
    const source = new EventSource(log.dataset.stream);
    const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.scrollTop = log.scrollHeight;
    source.onmessage = (event) => {
      const follow = atBottom();
      log.append(event.data + "\n");
      if (follow) log.scrollTop = log.scrollHeight;
    };
    source.addEventListener("end", (event) => {
      source.close();
      const status = document.getElementById("run-status");
      if (status) {
        const names = { succeeded: "Succeeded", failed: "Failed", stopped: "Stopped", interrupted: "Interrupted" };
        status.textContent = names[event.data] || event.data;
        status.className = "status status-" + event.data;
      }
      for (const form of document.querySelectorAll('form[action$="/stop"]')) form.remove();
    });
  }
});
