// The frame around every page once signed in: the sidebar (brand, navigation, theme, account) and the footer.
// Below 60rem the sidebar is a top bar whose menu button shows the navigation. Escape closes the menu and puts
// focus back on the button; so does moving focus out of it (it would otherwise cover the page focus moved to).

import { useEffect, useRef, useState } from "react";
import type { FocusEvent, ReactNode } from "react";
import { location_, post } from "../api";
import { useData } from "../data";
import { Link, useRouter } from "../router";
import { THEMES, chooseTheme, currentTheme } from "../theme";
import type { OverviewData, Theme } from "../types";
import { Icon, Mark } from "./Icons";
import type { IconName } from "./Icons";
import { Notice } from "./Parts";

const DOCS = "https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs";

interface NavItem {
  href: string;
  label: string;
  icon: IconName;
  steps?: string[]; // the overview steps this page completes
}

export const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Workspace", items: [{ href: "/", label: "Overview", icon: "overview" }] },
  {
    group: "Set up",
    items: [
      { href: "/proxmox", label: "Proxmox", icon: "proxmox", steps: ["/proxmox"] },
      { href: "/ssh", label: "SSH key", icon: "key", steps: ["/ssh"] },
      { href: "/defaults", label: "Defaults", icon: "sliders", steps: ["/defaults"] },
      { href: "/servers", label: "Servers", icon: "servers", steps: ["/servers"] },
      { href: "/hardening", label: "Hardening", icon: "shield" },
    ],
  },
  {
    group: "Playbooks",
    items: [
      { href: "/vms", label: "VMs", icon: "vms" },
      { href: "/updates", label: "Updates", icon: "refresh" },
      { href: "/run", label: "Run", icon: "play" },
      { href: "/runs", label: "History", icon: "history" },
    ],
  },
];

export function isCurrent(href: string, path: string): boolean {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

export function Brand() {
  return (
    <Link className="brand" href="/">
      <Mark />
      <span className="brand-text">
        <span className="brand-name">usas</span>
        <span className="brand-sub">Ubuntu Server Auto Setup</span>
      </span>
    </Link>
  );
}

export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const [failed, setFailed] = useState(false);
  const change = async (value: Theme) => {
    setTheme(value);
    setFailed(!(await chooseTheme(value)));
  };
  return (
    <fieldset className="theme-switch">
      <legend>Theme</legend>
      <div className="segmented">
        {THEMES.map(({ value, label }) => (
          <span key={value}>
            <input type="radio" id={`theme-${value}`} name="theme" value={value} checked={theme === value}
                   onChange={() => change(value)} />
            <label htmlFor={`theme-${value}`}>{label}</label>
          </span>
        ))}
      </div>
      {failed ? <p className="help" role="status">The theme applies to this page only: it couldn't be saved.</p> : null}
    </fieldset>
  );
}

function StepState({ done }: { done: boolean }) {
  return (
    <>
      <span className={`step-state ${done ? "done" : "todo"}`} aria-hidden="true" />
      <span className="visually-hidden">{done ? " (done)" : " (to do)"}</span>
    </>
  );
}

export function Footer({ version, children }: { version: string; children?: ReactNode }) {
  return (
    <footer>
      <p>
        USAS web UI {version} · <a href={`${DOCS}/web-ui.md`}>Help</a> ·{" "}
        <a href={`${DOCS}/accessibility.md`}>Accessibility</a>
      </p>
      {children}
    </footer>
  );
}

export function Layout({ version, children }: { version: string; children: ReactNode }) {
  const { path, seq, notice } = useRouter();
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const overview = useData<OverviewData>("/api/overview");
  const { reload } = overview;

  useEffect(() => {
    setOpen(false);
    if (seq > 0) reload();
  }, [seq, reload]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // Focus moved somewhere else on the page (not to another window, which leaves relatedTarget empty).
  const onBlur = (event: FocusEvent<HTMLElement>) => {
    const next = event.relatedTarget;
    if (open && next instanceof Node && !event.currentTarget.contains(next)) setOpen(false);
  };

  const stepDone = (hrefs: string[]) => {
    const steps = overview.data?.steps.filter((s) => hrefs.includes(s.href)) ?? [];
    return steps.length ? steps.every((s) => s.done) : null;
  };

  const signOut = async () => {
    const reply = await post<{ redirect?: string }>("/api/logout", {});
    location_.assign(reply.data.redirect ?? "/login");
  };

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to the content</a>
      <header className="sidebar" onBlur={onBlur}>
        <div className="sidebar-top">
          <Brand />
          <button type="button" className="button secondary small menu-button" aria-expanded={open} ref={menuButton}
                  aria-controls="sidebar-panel" onClick={() => setOpen(!open)}>
            <Icon name={open ? "close" : "menu"} />
            Menu
          </button>
        </div>
        <div className={`sidebar-panel${open ? " open" : ""}`} id="sidebar-panel">
          <nav className="nav" aria-label="Main">
            {NAV.map(({ group, items }) => (
              <div key={group}>
                <p className="nav-group" id={`nav-${group.toLowerCase().replace(" ", "-")}`}>{group}</p>
                <ul aria-labelledby={`nav-${group.toLowerCase().replace(" ", "-")}`}>
                  {items.map((item) => {
                    const done = item.steps ? stepDone(item.steps) : null;
                    return (
                      <li key={item.href}>
                        <Link href={item.href} aria-current={isCurrent(item.href, path) ? "page" : undefined}>
                          <Icon name={item.icon} />
                          {item.label}
                          {done !== null ? <StepState done={done} /> : null}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
          <div className="sidebar-foot">
            <ThemeSwitch />
            <div className="account">
              <Link href="/account" aria-current={path === "/account" ? "page" : undefined}>
                <Icon name="user" />
                Account
              </Link>
              <button type="button" className="link-button" onClick={signOut}>Sign out</button>
            </div>
          </div>
        </div>
      </header>
      <div className="content">
        <main id="main" tabIndex={-1}>
          {notice ? <Notice notice={notice} /> : null}
          {children}
        </main>
        <Footer version={version} />
      </div>
    </div>
  );
}

// The frame of the sign-in and first-run pages: the brand above a narrow form.
export function AuthLayout({ version, children }: { version: string; children: ReactNode }) {
  return (
    <div className="auth-page">
      <a className="skip" href="#main">Skip to the content</a>
      <div className="stack">
        <header><Brand /></header>
        <main id="main" tabIndex={-1} className="card">
          <div className="card-body">{children}</div>
        </main>
        <Footer version={version}>
          <ThemeSwitch />
        </Footer>
      </div>
    </div>
  );
}
