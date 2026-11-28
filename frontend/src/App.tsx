import { useCallback, useEffect, useRef, useState } from "react";
import { api, setToken, getToken, User } from "./api";
import { Login, Register } from "./pages/Auth";
import Fleet from "./pages/Fleet";
import PlantPage from "./pages/Plant";
import Alerts from "./pages/Alerts";
import Compliance from "./pages/Compliance";
import { AdminUsers, AdminSchedules, AdminAudit } from "./pages/Admin";
import { PlantsList, PlantForm } from "./pages/PlantsAdmin";
import Inspect from "./pages/Inspect";
import PollNow from "./pages/PollNow";
import Reports from "./pages/Reports";
import { EmailLog, RecipientLists } from "./pages/Recipients";
import Icon from "./Icon";
import Tour, { HelpMenu } from "./Tour";

const THEME_KEY = "gso_theme";
const TOUR_KEY = "gso_tour_shown";
function useTheme() {
  const [theme, setTheme] = useState<string>(() => document.documentElement.dataset.theme || "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }, [theme]);
  return [theme, () => setTheme(t => (t === "dark" ? "light" : "dark"))] as const;
}

function useHash() {
  const [hash, setHash] = useState(location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(location.hash || "#/");
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash.slice(1);
}

export default function App() {
  const route = useHash();
  const [theme, toggleTheme] = useTheme();
  const mainRef = useRef<HTMLElement>(null);
  const firstRoute = useRef(true);
  // Move focus to the page content on navigation, so keyboard and screen reader users land on the new page.
  useEffect(() => {
    if (firstRoute.current) { firstRoute.current = false; return; }
    mainRef.current?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.split("?")[0].split("/").slice(0, 3).join("/")]);
  const [user, setUser] = useState<User | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadMe = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoaded(true); return; }
    try { setUser(await api<User>("/auth/me")); } catch { setUser(null); }
    setLoaded(true);
  }, []);
  useEffect(() => { loadMe(); }, [loadMe]);

  // Guided tour: opens once per login (a new token) while the user's "show at login" setting is on.
  const [tourOpen, setTourOpen] = useState(false);
  useEffect(() => {
    if (!user || user.show_tour === false) return;
    const t = (getToken() || "").slice(-24);
    let seen: string | null = null;
    try { seen = sessionStorage.getItem(TOUR_KEY); } catch { /* ignore */ }
    if (t && seen !== t) {
      try { sessionStorage.setItem(TOUR_KEY, t); } catch { /* ignore */ }
      setTourOpen(true);
    }
  }, [user]);
  const saveTourPref = useCallback(async (show: boolean) => {
    setUser(u => (u ? { ...u, show_tour: show } : u));
    try { await api("/auth/me/tour", { method: "PUT", body: { show } }); } catch { /* keep the local choice; it is saved next time */ }
  }, []);
  const closeTour = useCallback((dontShow: boolean) => {
    setTourOpen(false);
    if (user && dontShow === (user.show_tour !== false)) saveTourPref(!dontShow);
  }, [user, saveTourPref]);

  if (!loaded) return null;
  if (!user) {
    return route.startsWith("/register") ? <Register /> : <Login onLogin={loadMe} />;
  }
  const isAdmin = user.role === "gso_admin";
  const isGso = isAdmin || user.role === "gso_user";
  const [path, query = ""] = route.split("?");
  const [, page, arg, sub, sub2, sub3] = path.split("/");

  let body;
  if (page === "plant" && arg) body = <PlantPage mnem={arg} user={user} />;
  else if (page === "attempt" && arg) body = <Inspect key={arg} id={arg} canPoll={isAdmin} />;
  else if (page === "alerts") body = <Alerts key={query} canAck={isGso} query={query} />;
  else if (page === "compliance") body = <Compliance key={query} query={query} />;
  else if (page === "reports") body = <Reports key={route} query={query} />;
  else if (page === "admin" && isAdmin && arg === "plants" && sub === "new") body = <PlantForm key="new" />;
  else if (page === "admin" && isAdmin && arg === "plants" && sub) body = <PlantForm key={sub} mnem={sub} />;
  else if (page === "admin" && isAdmin && arg === "plants") body = <PlantsList query={query} />;
  else if (page === "admin" && isAdmin && arg === "poll")
    body = sub === "runs"
      ? <PollNow key={route} runId={sub2} />
      : <PollNow key={route} plant={sub || "all"} category={sub2 || "all"} autorun={sub3 === "run"} />;
  else if (page === "admin" && isAdmin && arg === "users") body = <AdminUsers />;
  else if (page === "admin" && isAdmin && arg === "schedules") body = <AdminSchedules />;
  else if (page === "admin" && isAdmin && arg === "audit") body = <AdminAudit />;
  else if (page === "admin" && isAdmin && arg === "recipients")
    body = sub === "log" ? <EmailLog key={route} query={query} /> : <RecipientLists />;
  else body = <Fleet key={query} isAdmin={isAdmin} query={query} />;

  const link = (href: string, label: string, icon: string) => {
    const on = ("#" + route).startsWith(href) && (href !== "#/" || route === "/" || route.startsWith("/plant/") || route.startsWith("/attempt/"));
    return <a href={href} className={on ? "active" : ""} aria-current={on ? "page" : undefined}><Icon name={icon} />{label}</a>;
  };
  return (
    <div className="shell">
      <a className="skip" href="#main" onClick={e => { e.preventDefault(); mainRef.current?.focus(); }}>Skip to content</a>
      <header>
        <a className="brand" href="#/" aria-label="GSO Solar Data Platform, home">
          <img className="brand-mark" src="/brand/gso-mark.png" alt="" width={26} height={29} />
          <span className="brand-text"><span className="brand-org">Grid System Operator</span><span className="brand-app">Solar Data Platform</span></span>
          <span className="beta">beta</span>
        </a>
        <nav aria-label="Main">
          {link("#/", "Fleet", "grid")}
          {link("#/alerts", "Alerts", "bell")}
          {link("#/compliance", "Compliance", "shield")}
          {link("#/reports", "Reports", "table")}
          {isAdmin && <span className="nav-sep" aria-hidden="true" />}
          {isAdmin && link("#/admin/poll", "Retrieve now", "zap")}
          {isAdmin && link("#/admin/plants", "Plants", "sun")}
          {isAdmin && link("#/admin/users", "Users", "users")}
          {isAdmin && link("#/admin/schedules", "Schedules", "clock")}
          {isAdmin && link("#/admin/recipients", "Email reports", "mail")}
          {isAdmin && link("#/admin/audit", "Audit", "list")}
        </nav>
        <div className="who">
          <span className="name">{user.name}</span>
          {user.role && <span className="role">{user.role.replace("_", " ")}</span>}
          <button className="ghost theme-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Light theme" : "Dark theme"}><Icon name={theme === "dark" ? "sun" : "moon"} /></button>
          <HelpMenu showAtLogin={user.show_tour !== false} onTour={() => setTourOpen(true)} onToggleLogin={saveTourPref} />
          <button className="ghost" onClick={() => { setTourOpen(false); setToken(null); setUser(null); location.hash = "#/login"; }}><Icon name="logout" />Log out</button>
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1}>{body}</main>
      {tourOpen && <Tour user={user} isAdmin={isAdmin} isGso={isGso} onClose={closeTour} />}
    </div>
  );
}
