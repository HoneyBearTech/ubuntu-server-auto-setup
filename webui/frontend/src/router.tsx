// A small router over the History API: the page URLs are the server's (main.py PAGES), links within the app
// change the page without a reload, and the back and forward buttons work. After each change of page the
// document title follows and focus moves to the page's heading, so screen readers announce the new page.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";
import { location_ } from "./api";
import type { Notice } from "./types";

interface Location {
  path: string;
  search: string;
  notice: Notice | null;
  seq: number;
}

interface RouterValue extends Location {
  // Changes the page; notice, if given, is shown at the top of the new page (such as "Password set").
  navigate: (to: string, notice?: Notice | null) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function current(seq: number, notice: Notice | null = null): Location {
  return { path: window.location.pathname, search: window.location.search, notice, seq };
}

export function Router({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<Location>(() => current(0));

  useEffect(() => {
    const onPop = () => setLocation((l) => current(l.seq + 1));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((to: string, notice: Notice | null = null) => {
    const url = new URL(to, window.location.href);
    if (url.origin !== window.location.origin) {
      location_.assign(to);
      return;
    }
    window.history.pushState({}, "", url.pathname + url.search + url.hash);
    window.scrollTo?.(0, 0);
    setLocation((l) => current(l.seq + 1, notice));
  }, []);

  const value = useMemo(() => ({ ...location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error("useRouter outside <Router>");
  return value;
}

// An <a> that changes the page within the app. Downloads, other sites, new tabs and modified clicks are left
// to the browser.
export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const { navigate } = useRouter();
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey ||
        event.altKey || rest.download !== undefined || rest.target || !href.startsWith("/")) {
      return;
    }
    event.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={handle} {...rest} />;
}

// The parts of a run URL, /runs/<id>.
export function runId(path: string): string | null {
  const match = /^\/runs\/([^/]+)$/.exec(path);
  return match ? decodeURIComponent(match[1]) : null;
}
