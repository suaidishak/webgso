/* Guided tour: a step-by-step bubble that walks a user through every page they can open.
   Opens at each login until the user ticks "Don't show this again" (saved on their account),
   and can be reopened anytime from the Help menu. Admin-only pages are skipped for other roles. */
import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { User } from "./api";
import Icon from "./Icon";

type Who = { isAdmin: boolean; isGso: boolean; name: string };
type Step = {
  title: string;
  body: (w: Who) => ReactNode;
  /** Hash route to open before showing the step. */
  route?: string;
  /** CSS selectors tried in order; the first visible match is highlighted. None = centred bubble. */
  target?: string[];
  admin?: boolean;
};

const nav = (href: string) => `header nav a[href="${href}"]`;

const STEPS: Step[] = [
  {
    title: "Welcome to the GSO Solar Data Platform",
    body: w => <>
      <p>Hi {w.name.split(" ")[0]}. This short tour shows each part of the platform and what it is for. It takes about a minute.</p>
      <p className="muted">Use <b>Next</b> and <b>Back</b>, or the arrow keys. Press <b>Esc</b> to close. You can reopen the tour anytime from <b>Help</b> at the top right.</p>
    </>,
  },
  {
    title: "Fleet board",
    route: "#/", target: [nav("#/")],
    body: w => <p>Your home page. It lists {w.isGso ? "every plant in the fleet" : "the plants assigned to your organisation"} with
      the latest state of each API: Rolling, Day-ahead, Week-ahead, 4-months and MMF. Green means the last poll returned valid data.</p>,
  },
  {
    title: "Fleet at a glance",
    route: "#/", target: ["main .tiles", nav("#/")],
    body: () => <p>These counts summarise the fleet: problem plants, non-compliant and unreachable endpoints, and open alerts.
      Click a count to show only those plants.</p>,
  },
  {
    title: "Find plants fast",
    route: "#/", target: ["main .board-tools", nav("#/")],
    body: w => <p>Search by plant, name or region, filter by API, region or status, and group the board by region.
      Click a plant to open its page with forecast charts and recent polls, or click any status to see the <b>raw JSON</b> that poll returned, with problems highlighted.
      {w.isAdmin && <> Tick plants to poll, pause or report on many at once.</>}</p>,
  },
  {
    title: "Alerts",
    route: "#/alerts", target: [nav("#/alerts")],
    body: w => <p>The alert log records every problem the poller found: a plant not answering, a late submission or data breaking a rule.
      Filter by plant, region, API or rule. {w.isGso ? "GSO staff can acknowledge alerts once they are being handled." : "Alerts clear on their own once the plant sends valid data again."}</p>,
  },
  {
    title: "Compliance",
    route: "#/compliance", target: [nav("#/compliance")],
    body: () => <p>Shows how well each plant follows the web service spec over time: which rules are broken, how often,
      and whether forecasts arrived before their deadlines. Major issues are listed first.</p>,
  },
  {
    title: "Reports",
    route: "#/reports", target: [nav("#/reports")],
    body: () => <p>Opens on the <b>client templates</b>: forecasts in the DDQ layout with one column per LSS, and MMF with one sheet per
      parameter (each station or the average, at 5, 15 or 60 minutes; missing readings show as highlighted 0s). View them online or
      <b> Download Excel</b>. <b>Table view</b> has every polled point as a filterable table with CSV download.</p>,
  },
  {
    title: "Poll now",
    admin: true, route: "#/admin/poll", target: [nav("#/admin/poll")],
    body: () => <p>Fetch data from plants right away instead of waiting for the schedule. Choose one plant, several or the whole fleet,
      and one API or all. Every run is saved with its own link, and each result opens the <b>raw JSON viewer</b> so you can see exactly what the plant sent.</p>,
  },
  {
    title: "Plants",
    admin: true, route: "#/admin/plants", target: [nav("#/admin/plants")],
    body: () => <p>Add and edit plants and their API addresses and keys. Use the switches to pause polling for the fleet, an API type or a single plant,
      <b> Test</b> to check a plant's endpoints without saving data, and <b>Import plants</b> to load many from a file.</p>,
  },
  {
    title: "Users",
    admin: true, route: "#/admin/users", target: [nav("#/admin/users")],
    body: () => <p>Approve new sign-ups, set each person's role (GSO admin, GSO user or LSS user) and choose which plants an LSS user can see.</p>,
  },
  {
    title: "Schedules",
    admin: true, route: "#/admin/schedules", target: [nav("#/admin/schedules")],
    body: () => <p>When each API is polled and when submissions are due. Change the interval or cron timing, retries and timeouts per API.</p>,
  },
  {
    title: "Audit",
    admin: true, route: "#/admin/audit", target: [nav("#/admin/audit")],
    body: () => <p>A record of every admin change: who changed what and when, with the values before and after.</p>,
  },
  {
    title: "Light or dark",
    target: ["header .theme-btn"],
    body: () => <p>Switch between the dark and light theme. Your choice is remembered on this browser.</p>,
  },
  {
    title: "Help is always here",
    route: "#/", target: ["header .help-btn"],
    body: () => <p>Open <b>Help</b> to take this tour again whenever you like, or to turn the tour at login back on.</p>,
  },
  {
    title: "You're all set",
    body: () => <p>That's the whole platform. If you don't need this tour next time you log in, tick the box below before you finish.</p>,
  },
];

type Rect = { top: number; left: number; width: number; height: number };
const PAD = 6, GAP = 12, MARGIN = 12;

function findTarget(sels?: string[]): HTMLElement | null {
  for (const s of sels || []) {
    const el = document.querySelector<HTMLElement>(s);
    if (el) { const r = el.getBoundingClientRect(); if (r.width > 0 && r.height > 0) return el; }
  }
  return null;
}

export default function Tour({ user, isAdmin, isGso, onClose }: {
  user: User; isAdmin: boolean; isGso: boolean; onClose: (dontShow: boolean) => void;
}) {
  const steps = STEPS.filter(s => !s.admin || isAdmin);
  const [i, setI] = useState(0);
  const [dontShow, setDontShow] = useState(user.show_tour === false);
  const [rect, setRect] = useState<Rect | null>(null);
  const [ready, setReady] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; arrow: number; side: "top" | "bottom" | "none" }>({ top: 0, left: 0, arrow: 0, side: "none" });
  const bubble = useRef<HTMLDivElement>(null);
  const nextBtn = useRef<HTMLButtonElement>(null);
  const targetEl = useRef<HTMLElement | null>(null);
  const step = steps[i];
  const last = i === steps.length - 1;
  const who: Who = { isAdmin, isGso, name: user.name || "" };

  const close = useCallback(() => onClose(dontShow), [onClose, dontShow]);

  // Open the step's page, then wait (briefly) for its target to render.
  useEffect(() => {
    setReady(false);
    targetEl.current = null;
    if (step.route && location.hash.split("?")[0] !== step.route && !(step.route === "#/" && (location.hash === "" || location.hash === "#")))
      location.hash = step.route;
    let tries = 0, timer = 0;
    const look = () => {
      const el = findTarget(step.target);
      if (el || !step.target || tries++ > 25) {
        targetEl.current = el;
        if (el) {
          const r = el.getBoundingClientRect();
          if (r.top < 70 || r.bottom > innerHeight - 40) el.scrollIntoView({ block: "center" });
        }
        measure();
        setReady(true);
        // Keep keyboard focus in the bubble (page navigation moves focus to the main area).
        requestAnimationFrame(() => nextBtn.current?.focus({ preventScroll: true }));
        return;
      }
      timer = window.setTimeout(look, 80);
    };
    timer = window.setTimeout(look, step.route ? 120 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i]);

  const measure = useCallback(() => {
    const el = targetEl.current;
    if (!el || !el.isConnected) { setRect(null); return; }
    const r = el.getBoundingClientRect();
    setRect({ top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 });
  }, []);

  // Follow the target as the page scrolls, resizes or re-renders.
  useEffect(() => {
    const on = () => measure();
    addEventListener("resize", on);
    addEventListener("scroll", on, true);
    const t = window.setInterval(on, 400);
    return () => { removeEventListener("resize", on); removeEventListener("scroll", on, true); clearInterval(t); };
  }, [measure]);

  // Place the bubble below the target, or above when there is no room, kept inside the window.
  useLayoutEffect(() => {
    const b = bubble.current;
    if (!b) return;
    const w = b.offsetWidth, h = b.offsetHeight, vw = innerWidth, vh = innerHeight;
    if (!rect) { setPos({ top: Math.max(MARGIN, (vh - h) / 2), left: Math.max(MARGIN, (vw - w) / 2), arrow: 0, side: "none" }); return; }
    const cx = rect.left + rect.width / 2;
    const left = Math.min(Math.max(MARGIN, cx - w / 2), vw - w - MARGIN);
    const below = rect.top + rect.height + GAP;
    let top: number, side: "top" | "bottom" | "none";
    if (below + h <= vh - MARGIN) { top = below; side = "top"; }
    else if (rect.top - GAP - h >= MARGIN) { top = rect.top - GAP - h; side = "bottom"; }
    else { top = Math.max(MARGIN, vh - h - MARGIN); side = "none"; }
    setPos({ top, left, arrow: Math.min(Math.max(18, cx - left), w - 18), side });
  }, [rect, i, ready]);

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowRight" && !(e.target instanceof HTMLInputElement)) { e.preventDefault(); last ? close() : setI(n => n + 1); }
      else if (e.key === "ArrowLeft" && !(e.target instanceof HTMLInputElement)) { e.preventDefault(); setI(n => Math.max(0, n - 1)); }
      else if (e.key === "Tab" && bubble.current) {
        // Keep Tab inside the bubble while the tour is open.
        const f = bubble.current.querySelectorAll<HTMLElement>("button, input");
        const first = f[0], end = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); end.focus(); }
        else if (!e.shiftKey && document.activeElement === end) { e.preventDefault(); first.focus(); }
        else if (!bubble.current.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      }
    };
    addEventListener("keydown", on, true);
    return () => removeEventListener("keydown", on, true);
  }, [close, last]);

  return (
    <div className="tour" aria-live="off">
      <div className={`tour-backdrop${rect ? "" : " dim"}`} />
      {rect && <div className="tour-spot" style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }} aria-hidden="true" />}
      <div ref={bubble} className={`tour-bubble arrow-${pos.side}${ready ? " shown" : ""}`} role="dialog" aria-modal="true"
        aria-labelledby="tour-title" aria-describedby="tour-body"
        style={{ top: pos.top, left: pos.left, ["--arrow-x" as string]: `${pos.arrow}px` }}>
        <div className="tour-head">
          <span className="tour-count">{i + 1} of {steps.length}</span>
          <button className="ghost icon-btn" onClick={close} aria-label="Close tour" title="Close tour"><Icon name="x" size={14} /></button>
        </div>
        <h3 id="tour-title">{step.title}</h3>
        <div id="tour-body" className="tour-body">{step.body(who)}</div>
        <div className="tour-dots" aria-hidden="true">{steps.map((_, k) => <i key={k} className={k === i ? "on" : k < i ? "done" : ""} />)}</div>
        <div className="tour-foot">
          <label className="check-field tour-dont"><input type="checkbox" checked={dontShow} onChange={e => setDontShow(e.target.checked)} />Don't show this again</label>
          <span className="tour-nav">
            {i > 0 && <button className="ghost" onClick={() => setI(i - 1)}>Back</button>}
            {!last && i === 0 && <button className="ghost" onClick={close}>Skip tour</button>}
            <button ref={nextBtn} onClick={() => (last ? close() : setI(i + 1))}>{last ? "Finish" : i === 0 ? "Start tour" : "Next"}</button>
          </span>
        </div>
      </div>
    </div>
  );
}

/** Header Help menu: reopen the tour, and turn the tour at login on or off. */
export function HelpMenu({ showAtLogin, onTour, onToggleLogin }: {
  showAtLogin: boolean; onTour: () => void; onToggleLogin: (v: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); wrap.current?.querySelector<HTMLElement>(".help-btn")?.focus(); } };
    addEventListener("mousedown", down);
    addEventListener("keydown", key);
    requestAnimationFrame(() => wrap.current?.querySelector<HTMLElement>(".help-menu [role=menuitem]")?.focus());
    return () => { removeEventListener("mousedown", down); removeEventListener("keydown", key); };
  }, [open]);
  return (
    <div className="help-wrap" ref={wrap}>
      <button className="ghost help-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <Icon name="help" />Help
      </button>
      {open && (
        <div className="help-menu" role="menu" aria-label="Help">
          <button role="menuitem" className="ghost" onClick={() => { setOpen(false); onTour(); }}><Icon name="compass" />Take the guided tour</button>
          <button role="menuitemcheckbox" aria-checked={showAtLogin} className="ghost" onClick={() => onToggleLogin(!showAtLogin)}>
            <span className={`menu-check${showAtLogin ? " on" : ""}`} aria-hidden="true">{showAtLogin && <Icon name="check" size={12} />}</span>
            Show the tour when I log in
          </button>
        </div>
      )}
    </div>
  );
}
