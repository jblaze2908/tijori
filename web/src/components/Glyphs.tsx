// 16px line icons from the Paper design ("Tijori — UI v1"); stroke follows currentColor.
const g = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

export const G = {
  overview: (
    <svg {...g}>
      <rect x="1.75" y="1.75" width="5" height="5" rx="1.25" />
      <rect x="9.25" y="1.75" width="5" height="5" rx="1.25" />
      <rect x="1.75" y="9.25" width="5" height="5" rx="1.25" />
      <rect x="9.25" y="9.25" width="5" height="5" rx="1.25" />
    </svg>
  ),
  spending: (
    <svg {...g}>
      <path d="M2 12l4-4 3 3 5-6" />
    </svg>
  ),
  subscriptions: (
    <svg {...g}>
      <path d="M13.5 6.5A5.5 5.5 0 0 0 3.2 5M2.5 9.5A5.5 5.5 0 0 0 12.8 11M3 2.5V5.2h2.7M13 13.5v-2.7h-2.7" />
    </svg>
  ),
  card: (
    <svg {...g}>
      <rect x="2" y="4" width="12" height="8.5" rx="1.5" />
      <path d="M2 7h12" />
    </svg>
  ),
  transactions: (
    <svg {...g}>
      <path d="M2 4h12M2 8h12M2 12h8" />
    </svg>
  ),
  orders: (
    <svg {...g}>
      <path d="M3 5.25h10l-.8 8.1a.75.75 0 0 1-.75.65h-6.9a.75.75 0 0 1-.75-.65zM5.75 7V4.25a2.25 2.25 0 0 1 4.5 0V7" />
    </svg>
  ),
  networth: (
    <svg {...g}>
      <path d="M2 6l6-3.5L14 6M3.5 7v5M8 7v5M12.5 7v5M2 13.5h12" />
    </svg>
  ),
  inbox: (
    <svg {...g}>
      <path d="M2 9.5l1.6-6.1A1 1 0 0 1 4.6 2.7h6.8a1 1 0 0 1 1 .7L14 9.5M2 9.5V13a.5.5 0 0 0 .5.5h11a.5.5 0 0 0 .5-.5V9.5M2 9.5h3.5l1 1.5h3l1-1.5H14" />
    </svg>
  ),
  gear: (
    <svg {...g}>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
    </svg>
  ),
  search: (
    <svg {...g} viewBox="0 0 14 14">
      <circle cx="6" cy="6" r="4.25" />
      <path d="M9.25 9.25L12.5 12.5" />
    </svg>
  ),
  calendar: (
    <svg {...g} viewBox="0 0 14 14">
      <rect x="1.75" y="2.75" width="10.5" height="9.5" rx="1.5" />
      <path d="M1.75 5.75h10.5M4.5 1.25v2.5M9.5 1.25v2.5" />
    </svg>
  ),
  left: (
    <svg {...g} viewBox="0 0 14 14">
      <path d="M8.5 3L4.5 7l4 4" />
    </svg>
  ),
  right: (
    <svg {...g} viewBox="0 0 14 14">
      <path d="M5.5 3l4 4-4 4" />
    </svg>
  ),
  down: (
    <svg {...g} viewBox="0 0 10 10">
      <path d="M2.5 4l2.5 2.5L7.5 4" />
    </svg>
  ),
  up: (
    <svg {...g} viewBox="0 0 10 10">
      <path d="M2.5 6L5 3.5 7.5 6" />
    </svg>
  ),
  sortDown: (
    <svg {...g} viewBox="0 0 10 10" width={10} height={10}>
      <path d="M5 2v6M2.5 5.5L5 8l2.5-2.5" />
    </svg>
  ),
  sortUp: (
    <svg {...g} viewBox="0 0 10 10" width={10} height={10}>
      <path d="M5 8V2M2.5 4.5L5 2l2.5 2.5" />
    </svg>
  ),
  close: (
    <svg {...g} viewBox="0 0 12 12">
      <path d="M3 3l6 6M9 3l-6 6" />
    </svg>
  ),
  download: (
    <svg {...g} viewBox="0 0 14 14">
      <path d="M7 1.75v7M4 6l3 3 3-3M2 10.5v1a.75.75 0 0 0 .75.75h8.5A.75.75 0 0 0 12 11.5v-1" />
    </svg>
  ),
  check: (
    <svg {...g} viewBox="0 0 12 12" strokeWidth={2}>
      <path d="M2.5 6.5l2.2 2.2L9.5 3.8" />
    </svg>
  ),
  mail: (
    <svg {...g}>
      <rect x="1.75" y="3.25" width="12.5" height="9.5" rx="1.5" />
      <path d="M2 4.5l6 4.25 6-4.25" />
    </svg>
  ),
  doc: (
    <svg {...g}>
      <path d="M4 1.75h5.5L12.5 4.75v9a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V2.25a.5.5 0 0 1 .5-.5zM9.5 1.75v3h3M5.75 8h4.5M5.75 10.75h4.5" />
    </svg>
  ),
  link: (
    <svg {...g}>
      <path d="M6.5 9.5l3-3M7 4.5l1-1a2.5 2.5 0 0 1 3.5 3.5l-1 1M9 11.5l-1 1a2.5 2.5 0 0 1-3.5-3.5l1-1" />
    </svg>
  ),
  transfer: (
    <svg {...g}>
      <path d="M2.5 5.5h10l-2.5-2.5M13.5 10.5h-10l2.5 2.5" />
    </svg>
  ),
  upload: (
    <svg {...g}>
      <path d="M8 10.5V2.5M5 5.5l3-3 3 3M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2" />
    </svg>
  ),
};

/** The Tijori keyhole: the one brass mark in the product. */
export const Keyhole = ({ size = 22 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 22 22" fill="none" aria-hidden>
    <rect x="1" y="1" width="20" height="20" rx="5" stroke="var(--brass)" strokeWidth="1.5" />
    <circle cx="11" cy="9.5" r="2.5" fill="var(--brass)" />
    <path d="M11 11.5v4" stroke="var(--brass)" strokeWidth="2" strokeLinecap="round" />
  </svg>
);
