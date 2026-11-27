import { useCallback, useEffect, useState } from "react";
import { api, setToken, getToken, User } from "./api";
import { Login, Register } from "./pages/Auth";
import Fleet from "./pages/Fleet";
import PlantPage from "./pages/Plant";
import Alerts from "./pages/Alerts";
import Compliance from "./pages/Compliance";
import { AdminUsers, AdminSchedules, AdminAudit } from "./pages/Admin";
import { PlantsList, PlantForm } from "./pages/PlantsAdmin";
import Inspect from "./pages/Inspect";

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
  const [user, setUser] = useState<User | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadMe = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoaded(true); return; }
    try { setUser(await api<User>("/auth/me")); } catch { setUser(null); }
    setLoaded(true);
  }, []);
  useEffect(() => { loadMe(); }, [loadMe]);

  if (!loaded) return null;
  if (!user) {
    return route.startsWith("/register") ? <Register /> : <Login onLogin={loadMe} />;
  }
  const isAdmin = user.role === "gso_admin";
  const isGso = isAdmin || user.role === "gso_user";
  const [, page, arg, sub] = route.split("/");

  let body;
  if (page === "plant" && arg) body = <PlantPage mnem={arg} user={user} />;
  else if (page === "attempt" && arg) body = <Inspect key={arg} id={arg} />;
  else if (page === "alerts") body = <Alerts canAck={isGso} />;
  else if (page === "compliance") body = <Compliance />;
  else if (page === "admin" && isAdmin && arg === "plants" && sub === "new") body = <PlantForm key="new" />;
  else if (page === "admin" && isAdmin && arg === "plants" && sub) body = <PlantForm key={sub} mnem={sub} />;
  else if (page === "admin" && isAdmin && arg === "plants") body = <PlantsList />;
  else if (page === "admin" && isAdmin && arg === "users") body = <AdminUsers />;
  else if (page === "admin" && isAdmin && arg === "schedules") body = <AdminSchedules />;
  else if (page === "admin" && isAdmin && arg === "audit") body = <AdminAudit />;
  else body = <Fleet />;

  const link = (href: string, label: string) => (
    <a href={href} className={("#" + route).startsWith(href) && (href !== "#/" || route === "/") ? "active" : ""}>{label}</a>
  );
  return (
    <div className="shell">
      <header>
        <div className="brand"><span className="sun" />GSO Solar Data Platform <span className="beta">beta</span></div>
        <nav>
          {link("#/", "Fleet")}
          {link("#/alerts", "Alerts")}
          {link("#/compliance", "Compliance")}
          {isAdmin && link("#/admin/plants", "Plants")}
          {isAdmin && link("#/admin/users", "Users")}
          {isAdmin && link("#/admin/schedules", "Schedules")}
          {isAdmin && link("#/admin/audit", "Audit")}
        </nav>
        <div className="who">
          <span>{user.name} · {user.role?.replace("_", " ")}</span>
          <button className="ghost" onClick={() => { setToken(null); setUser(null); location.hash = "#/login"; }}>Log out</button>
        </div>
      </header>
      <main>{body}</main>
    </div>
  );
}
