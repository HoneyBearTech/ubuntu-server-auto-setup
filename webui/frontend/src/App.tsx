// Picks the page for the URL. The pages and their titles match the server's (main.py PAGES).

import { useEffect } from "react";
import type { ReactNode } from "react";
import { AuthLayout, Layout } from "./components/Layout";
import { PageHead } from "./components/Parts";
import { Account, Login, Setup } from "./pages/Auth";
import { Overview } from "./pages/Overview";
import { RunDetail, RunPage, RunsPage } from "./pages/Runs";
import { Defaults, Hardening, Proxmox } from "./pages/Sections";
import { Servers } from "./pages/Servers";
import { Ssh } from "./pages/Ssh";
import { Vms } from "./pages/Vms";
import { Link, runId, useRouter } from "./router";

const PAGES: Record<string, { title: string; page: () => ReactNode }> = {
  "/": { title: "Overview", page: () => <Overview /> },
  "/proxmox": { title: "Proxmox API", page: () => <Proxmox /> },
  "/ssh": { title: "SSH key", page: () => <Ssh /> },
  "/defaults": { title: "Defaults", page: () => <Defaults /> },
  "/servers": { title: "Servers", page: () => <Servers /> },
  "/hardening": { title: "Hardening", page: () => <Hardening /> },
  "/vms": { title: "VMs", page: () => <Vms /> },
  "/run": { title: "Run", page: () => <RunPage /> },
  "/runs": { title: "History", page: () => <RunsPage /> },
  "/account": { title: "Account", page: () => <Account /> },
};

function NotFound() {
  return (
    <>
      <PageHead title="Page not found" />
      <p>There is no page here. Go to the <Link href="/">overview</Link>.</p>
    </>
  );
}

export function App({ version }: { version: string }) {
  const { path, seq } = useRouter();
  const id = runId(path);
  const known = PAGES[path];
  const title = path === "/login" ? "Sign in" : path === "/setup" ? "Set up" : known?.title ?? (id ? "Run" : "Not found");

  useEffect(() => {
    document.title = `${title} · USAS`;
    // After a change of page (not on the first load), move focus to the new page's heading.
    if (seq > 0) document.getElementById("page-title")?.focus();
  }, [seq, title]);

  if (path === "/login") return <AuthLayout version={version}><Login /></AuthLayout>;
  if (path === "/setup") return <AuthLayout version={version}><Setup /></AuthLayout>;
  const page = known ? known.page() : id ? <RunDetail key={id} id={id} /> : <NotFound />;
  return <Layout version={version}>{page}</Layout>;
}
