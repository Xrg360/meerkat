"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type Page = "home" | "monitoring" | "settings";

type EventItem = {
  ts: string;
  alert_id: string;
  source: string;
  severity: string;
  status: string;
  title: string;
  body: string;
};

type StatusPayload = {
  alerts_silenced: boolean;
  internet_up: boolean | null;
  active_alerts: string[];
  recent_events: EventItem[];
};

type HealthPayload = {
  cpu_percent: number;
  ram_percent: number;
  disk_percent: number;
  cpu_temperature: number | null;
};

type DockerPayload = {
  available: boolean;
  error?: string;
  containers: Array<{ name: string; status: string; image: string }>;
};

type SiteSample = {
  ts: number;
  up: boolean;
  status_code: number | null;
  latency_ms: number | null;
};

type Site = {
  name: string;
  url: string;
  up: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
  history?: SiteSample[];
};

type SitesPayload = {
  total: number;
  up: number;
  down: number;
  sites: Site[];
};

type DataState = {
  status: StatusPayload | null;
  health: HealthPayload | null;
  docker: DockerPayload | null;
  sites: SitesPayload | null;
};

const skeletonStats = Array.from({ length: 4 });
const skeletonList = Array.from({ length: 3 });

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || "Meerkat API request failed");
  }
  return payload as T;
}

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 64 64" role="img" aria-label="Meerkat logo">
      <defs>
        <linearGradient id="logoGradient" x1="8" y1="7" x2="56" y2="58" gradientUnits="userSpaceOnUse">
          <stop stopColor="#20c977" />
          <stop offset="1" stopColor="#2563eb" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="12" fill="url(#logoGradient)" />
      <path d="M18 45V22c0-4 3-7 7-7h14c4 0 7 3 7 7v23h-8V25l-6 17h-5l-6-17v20h-8Z" fill="#fff" />
      <circle cx="24" cy="18" r="3" fill="#0e131b" opacity=".26" />
      <circle cx="40" cy="18" r="3" fill="#0e131b" opacity=".26" />
    </svg>
  );
}

function statusLabel(value: boolean | null | undefined) {
  if (value === true) return "up";
  if (value === false) return "down";
  return "unknown";
}

function latencyText(value: number | null | undefined) {
  return value == null ? "--" : `${Math.round(value)}ms`;
}

function samples(site: Site | null | undefined) {
  return Array.isArray(site?.history) ? site.history : [];
}

function uptime(site: Site | null | undefined) {
  const history = samples(site);
  if (!history.length) return site?.up ? 100 : 0;
  return Math.round((history.filter((sample) => sample.up).length / history.length) * 1000) / 10;
}

function averageLatency(site: Site | null | undefined) {
  const values = samples(site)
    .map((sample) => sample.latency_ms)
    .filter((value): value is number => value != null);
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function StatusBadge({ site }: { site: Site | null | undefined }) {
  if (!site) return <span className="status-mini warn">--</span>;
  return <span className={`status-mini ${site.up ? "" : "down"}`}>{site.up ? "UP" : "DN"}</span>;
}

function Bars({ site, count = 24 }: { site: Site | null | undefined; count?: number }) {
  const history = samples(site).slice(-count);
  const padded = Array(Math.max(0, count - history.length))
    .fill(null)
    .concat(history) as Array<SiteSample | null>;
  return (
    <div className="bars">
      {padded.map((sample, index) => (
        <span key={index} className={!sample ? "empty" : sample.up ? "" : "down"} />
      ))}
    </div>
  );
}

function SiteCard({
  site,
  onSelect,
  pinned,
  onTogglePin,
}: {
  site: Site;
  onSelect: (name: string) => void;
  pinned?: boolean;
  onTogglePin?: (name: string) => void;
}) {
  return (
    <div>
      <button className="site-row" onClick={() => onSelect(site.name)}>
        <div>
          <div className="site-name">{site.name}</div>
          <div className="site-url">{site.url}</div>
          <Bars site={site} count={18} />
        </div>
        <StatusBadge site={site} />
      </button>
      {onTogglePin ? (
        <label className="check-row">
          <input type="checkbox" checked={Boolean(pinned)} onChange={() => onTogglePin(site.name)} /> Show on Home
        </label>
      ) : null}
    </div>
  );
}

function StatSkeleton() {
  return (
    <section className="card stat">
      <div className="skeleton sk-line short" />
      <div className="skeleton sk-value" />
      <div className="skeleton sk-line medium" />
    </section>
  );
}

function Summary({ data }: { data: DataState }) {
  if (!data.status || !data.docker || !data.sites) {
    return (
      <div className="grid summary">
        {skeletonStats.map((_, index) => (
          <StatSkeleton key={index} />
        ))}
      </div>
    );
  }

  const running = data.docker.containers.filter((container) => container.status === "running").length;
  const items = [
    ["Internet", statusLabel(data.status.internet_up), data.status.internet_up ? "Reachable" : "Probe failing"],
    ["Alerts", String(data.status.active_alerts.length), data.status.alerts_silenced ? "Silenced" : "Active"],
    ["Containers", `${running}/${data.docker.containers.length}`, data.docker.available ? "Docker connected" : "Docker unavailable"],
    ["Sites", `${data.sites.up}/${data.sites.total}`, data.sites.down ? `${data.sites.down} down` : "All up"],
  ];

  return (
    <div className="grid summary">
      {items.map(([label, value, subvalue]) => (
        <section className="card stat" key={label}>
          <div className="label">{label}</div>
          <div className="value">{value}</div>
          <div className="subvalue">{subvalue}</div>
        </section>
      ))}
    </div>
  );
}

function MetricRow({ name, value, suffix = "%" }: { name: string; value: number; suffix?: string }) {
  const rounded = Math.round(value || 0);
  const tone = rounded >= 90 ? "bad" : rounded >= 75 ? "warn" : "";
  return (
    <div className="metric-row">
      <div className="muted">{name}</div>
      <div className="track">
        <div className={`fill ${tone}`} style={{ width: `${Math.max(0, Math.min(100, rounded))}%` }} />
      </div>
      <div>
        {rounded}
        {suffix}
      </div>
    </div>
  );
}

function HealthCard({ health }: { health: HealthPayload | null }) {
  return (
    <section className="card">
      <div className="card-head">
        <h3>System Health</h3>
        <span className="pill">CPU {health?.cpu_temperature == null ? "unavailable" : `${Math.round(health.cpu_temperature)}C`}</span>
      </div>
      <div className="card-body">
        {health ? (
          <div className="metrics">
            <MetricRow name="CPU" value={health.cpu_percent} />
            <MetricRow name="RAM" value={health.ram_percent} />
            <MetricRow name="Disk" value={health.disk_percent} />
            {health.cpu_temperature == null ? null : <MetricRow name="Temp" value={health.cpu_temperature} suffix="C" />}
          </div>
        ) : (
          <div className="metrics">
            <div className="skeleton sk-line" />
            <div className="skeleton sk-line" />
            <div className="skeleton sk-line" />
          </div>
        )}
      </div>
    </section>
  );
}

function LatencyChart({ site }: { site: Site | null }) {
  const values = samples(site)
    .filter((sample) => sample.latency_ms != null)
    .slice(-60);

  if (!values.length) {
    return (
      <svg viewBox="0 0 720 240" role="img" aria-label="Loading response data">
        <rect width="720" height="240" rx="8" fill="var(--soft)" />
        <polyline
          points="46,172 140,146 232,156 326,112 420,128 514,82 612,104 704,72"
          fill="none"
          stroke="var(--brand)"
          strokeWidth="3"
          strokeLinecap="round"
          opacity=".35"
        />
        <text x="24" y="34" fill="var(--muted)" fontSize="12">
          Loading response data...
        </text>
      </svg>
    );
  }

  const width = 720;
  const height = 240;
  const pad = { left: 44, right: 16, top: 16, bottom: 34 };
  const max = Math.max(...values.map((sample) => Number(sample.latency_ms)), 10);
  const min = Math.min(...values.map((sample) => Number(sample.latency_ms)), 0);
  const range = Math.max(1, max - min);
  const step = values.length > 1 ? (width - pad.left - pad.right) / (values.length - 1) : 0;
  const points = values
    .map((sample, index) => {
      const x = pad.left + index * step;
      const y = height - pad.bottom - ((Number(sample.latency_ms) - min) / range) * (height - pad.top - pad.bottom);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="HTTP response time chart">
      <polyline points={`${pad.left},${height - pad.bottom} ${points} ${width - pad.right},${height - pad.bottom}`} fill="rgba(22,185,112,.12)" stroke="none" />
      <polyline points={points} fill="none" stroke="var(--brand)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <text x="8" y="24" fill="var(--muted)" fontSize="11">
        {Math.round(max)}ms
      </text>
      <text x={pad.left} y={height - 9} fill="var(--muted)" fontSize="11">
        older
      </text>
      <text x={width - pad.right - 34} y={height - 9} fill="var(--muted)" fontSize="11">
        now
      </text>
    </svg>
  );
}

export function MeerkatApp({ page }: { page: Page }) {
  const [data, setData] = useState<DataState>({ status: null, health: null, docker: null, sites: null });
  const [selectedSiteName, setSelectedSiteName] = useState("");
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);
  const [refreshAge, setRefreshAge] = useState("--s");
  const [toasts, setToasts] = useState<Array<{ id: number; title: string; body: string }>>([]);
  const [pinned, setPinned] = useState<Set<string>>(new Set());
  const [seenAlerts, setSeenAlerts] = useState<Set<string>>(new Set());
  const [theme, setTheme] = useState("light");
  const [refreshMs, setRefreshMs] = useState(10000);
  const [popups, setPopups] = useState(true);
  const [token, setToken] = useState("");
  const [siteName, setSiteName] = useState("");
  const [siteUrl, setSiteUrl] = useState("");

  const sites = data.sites?.sites ?? [];
  const selectedSite = useMemo(() => sites.find((site) => site.name === selectedSiteName) ?? sites[0] ?? null, [selectedSiteName, sites]);

  function showToast(title: string, body: string) {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, title, body }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 8000);
  }

  async function refresh() {
    try {
      const [status, health, docker, sitesPayload] = await Promise.all([
        apiGet<StatusPayload>("/api/meerkat/status"),
        apiGet<HealthPayload>("/api/meerkat/health"),
        apiGet<DockerPayload>("/api/meerkat/docker"),
        apiGet<SitesPayload>("/api/meerkat/sites"),
      ]);
      setData({ status, health, docker, sites: sitesPayload });
      setLastRefresh(Date.now());
      if (!selectedSiteName && sitesPayload.sites?.length) setSelectedSiteName(sitesPayload.sites[0].name);

      const nextSeen = new Set(seenAlerts);
      status.recent_events
        ?.filter((event: EventItem) => ["critical", "emergency", "warning"].includes(event.severity))
        .slice(0, 5)
        .forEach((event: EventItem) => {
          const key = `${event.ts}:${event.alert_id}:${event.status}`;
          if (!nextSeen.has(key)) {
            nextSeen.add(key);
            if (popups) showToast(event.title || "Alert", event.body || event.severity);
            if ("Notification" in window && Notification.permission === "granted") {
              new Notification(event.title || "Meerkat alert", { body: event.body || event.severity });
            }
          }
        });
      setSeenAlerts(nextSeen);
      localStorage.setItem("meerkatSeenAlerts", JSON.stringify([...nextSeen].slice(-100)));
    } catch {
      showToast("Backend unavailable", "Start the Python monitor API on port 8710.");
    }
  }

  useEffect(() => {
    const rawPrefs = JSON.parse(localStorage.getItem("meerkatUiPrefs") || "{}") as { theme?: string; refresh?: number; popups?: boolean };
    const rawPins = JSON.parse(localStorage.getItem("meerkatPinnedSites") || "[]") as string[];
    const rawSeen = JSON.parse(localStorage.getItem("meerkatSeenAlerts") || "[]") as string[];
    setTheme(rawPrefs.theme || "light");
    setRefreshMs(rawPrefs.refresh || 10000);
    setPopups(rawPrefs.popups !== false);
    setToken(localStorage.getItem("meerkatActionToken") || "");
    setPinned(new Set(rawPins));
    setSeenAlerts(new Set(rawSeen));
  }, []);

  useEffect(() => {
    document.body.dataset.theme = theme;
    localStorage.setItem("meerkatUiPrefs", JSON.stringify({ theme, refresh: refreshMs, popups }));
  }, [theme, refreshMs, popups]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, refreshMs);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshMs, popups, seenAlerts.size]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRefreshAge(lastRefresh ? `${Math.floor((Date.now() - lastRefresh) / 1000)}s` : "--s");
    }, 1000);
    return () => window.clearInterval(timer);
  }, [lastRefresh]);

  async function postJson(path: string, body: Record<string, unknown>) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["X-Meerkat-Action-Token"] = token;
    const response = await fetch(`/api/meerkat/${path}`, { method: "POST", headers, body: JSON.stringify(body) });
    const payload = await response.json();
    if (!payload.ok) showToast("Action failed", payload.error || "Request failed");
    else showToast("Done", payload.message || "Action completed");
    await refresh();
  }

  async function addSite() {
    if (!siteName.trim() || !siteUrl.trim()) {
      showToast("Missing site details", "Name and URL are required.");
      return;
    }
    await postJson("actions/sites/add", { name: siteName.trim(), url: siteUrl.trim() });
    setSelectedSiteName(siteName.trim());
    setSiteName("");
    setSiteUrl("");
  }

  async function removeSelectedSite() {
    if (!selectedSite) {
      showToast("No site selected", "Select a runtime site first.");
      return;
    }
    await postJson("actions/sites/remove", { name: selectedSite.name });
    setSelectedSiteName("");
  }

  function selectSite(name: string) {
    setSelectedSiteName(name);
    if (page !== "monitoring") window.location.href = `/monitoring?site=${encodeURIComponent(name)}`;
  }

  function togglePin(name: string) {
    const next = new Set(pinned);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setPinned(next);
    localStorage.setItem("meerkatPinnedSites", JSON.stringify([...next]));
  }

  function saveActionToken() {
    if (token.trim()) localStorage.setItem("meerkatActionToken", token.trim());
    else localStorage.removeItem("meerkatActionToken");
    showToast("Action token", token.trim() ? "Saved for this browser." : "Cleared.");
  }

  async function enableNotifications() {
    if (!("Notification" in window)) {
      showToast("Notifications unavailable", "This browser does not support desktop notifications.");
      return;
    }
    const result = await Notification.requestPermission();
    showToast("Desktop notifications", result);
  }

  const pinnedSites = sites.filter((site) => pinned.has(site.name));
  const homeSites = (pinnedSites.length ? pinnedSites : sites.slice(0, 2)).slice(0, 4);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <Logo />
          <div>
            <h1>Meerkat</h1>
            <div className="subtitle">Uptime and homelab monitoring</div>
          </div>
        </div>
        <div className="pill">
          <span className={`dot ${lastRefresh ? "good" : "bad"}`} />
          <span>{lastRefresh ? "Live" : "Loading"}</span>
          <span>{refreshAge}</span>
        </div>
        <nav className="nav">
          <Link className={page === "home" ? "active" : ""} href="/">
            Home <span>Overview</span>
          </Link>
          <Link className={page === "monitoring" ? "active" : ""} href="/monitoring">
            Monitoring <span>HTTP</span>
          </Link>
          <Link className={page === "settings" ? "active" : ""} href="/settings">
            Settings <span>Prefs</span>
          </Link>
        </nav>
      </aside>

      <main className="content">
        <div className="topbar">
          <div>
            <h2>{page === "home" ? "Operations Home" : page === "monitoring" ? "HTTP Monitoring" : "Settings"}</h2>
            <div className="subtitle">
              {page === "home"
                ? "Clean system overview."
                : page === "monitoring"
                  ? "Uptime bars, latency chart, and site controls."
                  : "Display, notifications, token, and dashboard preferences."}
            </div>
          </div>
          <div className="top-actions">
            <Link href="/monitoring" className="primary">
              Monitoring
            </Link>
            <button onClick={refresh}>Refresh</button>
          </div>
        </div>

        {page === "home" ? (
          <>
            <Summary data={data} />
            <div className="grid two">
              <section className="card">
                <div className="card-head">
                  <h3>Pinned Monitors</h3>
                  <Link className="pill" href="/settings">
                    Choose
                  </Link>
                </div>
                <div className="card-body">
                  <div className="site-list">
                    {data.sites
                      ? homeSites.length
                        ? homeSites.map((site) => <SiteCard key={site.name} site={site} onSelect={selectSite} />)
                        : <div className="empty">No monitors yet.</div>
                      : skeletonList.map((_, index) => <div className="skeleton" style={{ height: 68 }} key={index} />)}
                  </div>
                </div>
              </section>
              <section className="card">
                <div className="card-head">
                  <h3>Active Alerts</h3>
                  <span className="pill">{data.status?.alerts_silenced ? "Silenced" : "Active"}</span>
                </div>
                <div className="card-body">
                  {data.status ? (
                    data.status.active_alerts.length ? (
                      data.status.active_alerts.map((alert) => (
                        <div className="event critical" key={alert}>
                          <div className="event-title">{alert}</div>
                          <div className="event-meta">currently active</div>
                        </div>
                      ))
                    ) : (
                      <div className="empty">No active alerts.</div>
                    )
                  ) : (
                    <>
                      <div className="skeleton sk-line" />
                      <div className="skeleton sk-line medium" />
                    </>
                  )}
                </div>
              </section>
            </div>
          </>
        ) : null}

        {page === "monitoring" ? (
          <div className="grid two">
            <div className="grid">
              <section className="card">
                <div className="card-body">
                  <div className="detail-title">
                    <div>
                      <div className="monitor-name">{selectedSite ? selectedSite.name : "No monitor selected"}</div>
                      {selectedSite ? (
                        <a className="site-url" href={selectedSite.url} target="_blank" rel="noreferrer">
                          {selectedSite.url}
                        </a>
                      ) : null}
                    </div>
                    <StatusBadge site={selectedSite} />
                  </div>
                  <Bars site={selectedSite} count={24} />
                  <div className="subtitle">{selectedSite ? `HTTP ${selectedSite.status_code ?? "none"} - ${selectedSite.error || "latest check completed"}` : "Add a monitor to start collecting data."}</div>
                  <div className="metric-cards">
                    <div className="metric-card">
                      <span className="muted">Response</span>
                      <strong>{latencyText(selectedSite?.latency_ms)}</strong>
                    </div>
                    <div className="metric-card">
                      <span className="muted">Average</span>
                      <strong>{latencyText(averageLatency(selectedSite))}</strong>
                    </div>
                    <div className="metric-card">
                      <span className="muted">Uptime</span>
                      <strong>{uptime(selectedSite)}%</strong>
                    </div>
                  </div>
                </div>
              </section>
              <section className="card">
                <div className="card-head">
                  <h3>Response Time</h3>
                  <span className="pill">Recent samples</span>
                </div>
                <div className="card-body chart-wrap">
                  <LatencyChart site={selectedSite} />
                </div>
              </section>
              <section className="card">
                <div className="card-head">
                  <h3>Add Site</h3>
                  <span className="pill">Website action</span>
                </div>
                <div className="card-body">
                  <div className="form-grid">
                    <input value={siteName} onChange={(event) => setSiteName(event.target.value)} placeholder="Name" />
                    <input value={siteUrl} onChange={(event) => setSiteUrl(event.target.value)} placeholder="https://example.com" />
                    <button className="primary" onClick={addSite}>
                      Add
                    </button>
                  </div>
                </div>
              </section>
            </div>
            <div className="grid">
              <section className="card">
                <div className="card-head">
                  <h3>Sites</h3>
                  <button onClick={removeSelectedSite}>Remove selected</button>
                </div>
                <div className="card-body">
                  <div className="site-list">
                    {data.sites
                      ? sites.length
                        ? sites.map((site) => <SiteCard key={site.name} site={site} onSelect={selectSite} />)
                        : <div className="empty">No monitors yet.</div>
                      : skeletonList.map((_, index) => <div className="skeleton" style={{ height: 68 }} key={index} />)}
                  </div>
                </div>
              </section>
              <HealthCard health={data.health} />
            </div>
          </div>
        ) : null}

        {page === "settings" ? (
          <div className="grid two">
            <section className="card">
              <div className="card-head">
                <h3>Preferences</h3>
                <span className="pill">Browser local</span>
              </div>
              <div className="card-body">
                <div className="settings-grid">
                  <div className="field">
                    <label htmlFor="themeSelect">Theme</label>
                    <select id="themeSelect" value={theme} onChange={(event) => setTheme(event.target.value)}>
                      <option value="light">Light</option>
                      <option value="dark">Dark</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="refreshSelect">Refresh interval</label>
                    <select id="refreshSelect" value={refreshMs} onChange={(event) => setRefreshMs(Number(event.target.value))}>
                      <option value={5000}>5 seconds</option>
                      <option value={10000}>10 seconds</option>
                      <option value={30000}>30 seconds</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="tokenInput">Action token</label>
                    <input id="tokenInput" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Only needed if configured" />
                  </div>
                  <div className="field">
                    <label>&nbsp;</label>
                    <button onClick={saveActionToken}>Save token</button>
                  </div>
                  <div className="field">
                    <label>Alert popups</label>
                    <label className="check-row">
                      <input type="checkbox" checked={popups} onChange={(event) => setPopups(event.target.checked)} /> Show in-app alert popups
                    </label>
                  </div>
                  <div className="field">
                    <label>Desktop notifications</label>
                    <button onClick={enableNotifications}>Enable notifications</button>
                  </div>
                </div>
              </div>
            </section>
            <section className="card">
              <div className="card-head">
                <h3>Home Monitor Cards</h3>
                <span className="pill">Pick what appears on Home</span>
              </div>
              <div className="card-body">
                <div className="site-list">
                  {data.sites
                    ? sites.length
                      ? sites.map((site) => <SiteCard key={site.name} site={site} onSelect={selectSite} pinned={pinned.has(site.name)} onTogglePin={togglePin} />)
                      : <div className="empty">Add monitors from the Monitoring page.</div>
                    : skeletonList.map((_, index) => <div className="skeleton" style={{ height: 68 }} key={index} />)}
                </div>
              </div>
            </section>
          </div>
        ) : null}
      </main>

      <div className="toast-stack">
        {toasts.map((toast) => (
          <div className="toast" key={toast.id}>
            <div className="event-title">{toast.title}</div>
            <div className="event-body">{toast.body}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
