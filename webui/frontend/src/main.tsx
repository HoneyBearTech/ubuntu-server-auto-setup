// The app's entry point. The server's HTML shell (webui/usas_web/templates/app.html) gives #root the
// session's CSRF token and the version; the app replaces what the shell shows until it has loaded.

import "@fontsource/atkinson-hyperlegible-next/latin-400.css";
import "@fontsource/atkinson-hyperlegible-next/latin-500.css";
import "@fontsource/atkinson-hyperlegible-next/latin-600.css";
import "@fontsource/atkinson-hyperlegible-next/latin-700.css";
import "@fontsource/atkinson-hyperlegible-next/latin-800.css";
import "@fontsource/atkinson-hyperlegible-mono/latin-400.css";
import "@fontsource/atkinson-hyperlegible-mono/latin-600.css";
import "./styles/app.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { setCsrf } from "./api";
import { App } from "./App";
import { Router } from "./router";

const root = document.getElementById("root");
if (root) {
  setCsrf(root.dataset.csrf ?? "");
  createRoot(root).render(
    <StrictMode>
      <Router>
        <App version={root.dataset.version ?? "dev"} />
      </Router>
    </StrictMode>,
  );
}
