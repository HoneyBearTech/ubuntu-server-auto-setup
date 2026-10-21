// The UI's icons and the brand mark, drawn for this project (MIT, like the rest of it). Icons are decorative:
// every one sits next to text that says the same thing, so they are hidden from screen readers.

const PATHS = {
  overview: <path d="M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z" />,
  proxmox: (
    <>
      <rect x="3" y="3" width="14" height="5" rx="1.2" />
      <rect x="3" y="12" width="14" height="5" rx="1.2" />
      <path d="M6 5.5h.01M6 14.5h.01" />
    </>
  ),
  key: (
    <>
      <circle cx="7" cy="10" r="3.5" />
      <path d="M10.5 10H17M14.5 10v3M17 10v2" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 5h12M4 10h12M4 15h12" />
      <circle cx="8" cy="5" r="1.6" />
      <circle cx="13" cy="10" r="1.6" />
      <circle cx="7" cy="15" r="1.6" />
    </>
  ),
  servers: (
    <>
      <rect x="3" y="3.5" width="14" height="13" rx="1.6" />
      <path d="M3 8h14M3 12.5h14" />
    </>
  ),
  shield: (
    <>
      <path d="M10 2.5 16 5v4.6c0 3.6-2.5 6.3-6 7.6-3.5-1.3-6-4-6-7.6V5z" />
      <path d="m7.5 10 1.8 1.8L12.8 8" />
    </>
  ),
  play: <path d="M6 4l10 6-10 6z" />,
  history: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6v4l2.5 2" />
    </>
  ),
  user: (
    <>
      <circle cx="10" cy="7" r="3.2" />
      <path d="M3.8 17c.8-3.2 3.3-5 6.2-5s5.4 1.8 6.2 5" />
    </>
  ),
  menu: <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />,
  close: <path d="m5 5 10 10M15 5 5 15" />,
  check: <path d="m4.5 10.5 3.5 3.5 7.5-8" />,
  alert: (
    <>
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 6v4.5M10 13.6h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 9v5M10 6.4h.01" />
    </>
  ),
  download: <path d="M10 3.5v9M6 9l4 4 4-4M4 16.5h12" />,
  stop: <rect x="5" y="5" width="10" height="10" rx="1.5" />,
  plus: <path d="M10 4.5v11M4.5 10h11" />,
  vms: (
    <>
      <rect x="2.5" y="3.5" width="15" height="10" rx="1.4" />
      <path d="M7 16.5h6M10 13.5v3M6 7.5h4M6 10h2.5" />
    </>
  ),
  refresh: <path d="M16 10a6 6 0 1 1-1.8-4.3M16 3.5v3h-3" />,
  pause: <path d="M7 5v10M13 5v10" />,
  sort: <path d="m6.5 8 3.5-3.5L13.5 8M6.5 12l3.5 3.5 3.5-3.5" />,
  "sort-up": <path d="m6 12 4-4 4 4" />,
  "sort-down": <path d="m6 8 4 4 4-4" />,
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name }: { name: IconName }) {
  return (
    <svg className="icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

// The mark: a shield with two server units cut out of it, one status light each (static/favicon.svg is the
// same drawing). It takes the accent colour of the theme.
export function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <path className="mark-shield" d="M16 1.8 28.2 6.3v9.1c0 7.3-4.9 12.6-12.2 14.9C8.7 28 3.8 22.7 3.8 15.4V6.3z" />
      <rect className="mark-unit" x="9" y="9.2" width="14" height="5.2" rx="1.4" />
      <rect className="mark-unit" x="9" y="16.6" width="14" height="5.2" rx="1.4" />
      <circle className="mark-light" cx="19.6" cy="11.8" r="1.25" />
      <circle className="mark-light" cx="19.6" cy="19.2" r="1.25" />
    </svg>
  );
}
