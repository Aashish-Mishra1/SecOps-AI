import { useEffect, useState, type FormEvent } from "react";
import { request, type Session } from "./client";

const pages = [
  "Overview",
  "Security events",
  "Knowledge base",
  "Investigations",
  "LLM analytics",
];
const paths = [
  "/alerts",
  "/events",
  "/documents",
  "/investigations",
  "/investigations",
];
type Row = Record<string, any>;
const label = (value: string) => value.replaceAll("_", " ");
function Fields({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <>N/A</>;
  if (Array.isArray(value))
    return value.length ? (
      <ol>
        {value.map((item, i) => (
          <li key={i}>
            <Fields value={item} />
          </li>
        ))}
      </ol>
    ) : (
      <>None</>
    );
  if (typeof value === "object")
    return (
      <dl>
        {Object.entries(value).map(([key, v]) => (
          <div key={key}>
            <dt>{label(key)}</dt>
            <dd>
              <Fields value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  return <>{String(value)}</>;
}
function form(e: FormEvent<HTMLFormElement>) {
  e.preventDefault();
  return Object.fromEntries(new FormData(e.currentTarget));
}
function Analytics({ metrics: m }: { metrics: Row }) {
  const units = [
    ["Total tokens", m.total_tokens],
    ["Output tokens / second", m.tokens_per_second],
    ["Active time (ms)", m.active_execution_ms],
    ["Tool failures", m.tool_failures],
  ];
  return (
    <>
      <p>
        {m.provider} · {m.model}
      </p>
      <div className="cards">
        {units.map(([name, value]) => (
          <article key={name}>
            <small>{name}</small>
            <b>
              {typeof value === "number"
                ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
                : "N/A"}
            </b>
          </article>
        ))}
      </div>
      <h3>Agent duration</h3>
      {m.agents.map((agent: Row) => (
        <div className="bar" key={agent.id}>
          <span>{label(agent.agent)}</span>
          <meter
            min={0}
            max={Math.max(1, ...m.agents.map((a: Row) => a.latency_ms || 0))}
            value={agent.latency_ms || 0}
          />
          <span>{agent.latency_ms?.toFixed(1) ?? "N/A"} ms</span>
        </div>
      ))}
      <h3>Provider requests</h3>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              {[
                "Provider",
                "Status",
                "Input tokens",
                "Output tokens",
                "Latency (ms)",
                "Cost (USD)",
              ].map((v) => (
                <th key={v}>{v}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.calls.map((c: Row) => (
              <tr key={c.id}>
                {[
                  "provider",
                  "status",
                  "input_tokens",
                  "output_tokens",
                  "latency_ms",
                  "cost_usd",
                ].map((k) => (
                  <td key={k}>{c[k] ?? "N/A"}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
function Workspace({
  session,
  logout,
}: {
  session: Session;
  logout: () => void;
}) {
  const route = () =>
    Math.max(0, pages.indexOf(decodeURIComponent(location.hash.slice(1))));
  const [page, setPage] = useState(route),
    [offset, setOffset] = useState(0),
    [filter, setFilter] = useState("");
  const [rows, setRows] = useState<Row[]>([]),
    [total, setTotal] = useState(0),
    [counts, setCounts] = useState<number[]>([]);
  const [id, select] = useState(""),
    [detail, setDetail] = useState<Row>(),
    [answer, setAnswer] = useState<Row>();
  const [version, refresh] = useState(0),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true);
  const [error, setError] = useState(""),
    [provider, setProvider] = useState(""),
    [comment, setComment] = useState("");
  const api = (p: string, b?: unknown) => request(p, session.access_token, b);
  const analyst = session.user.role === "analyst";
  useEffect(() => {
    const change = () => {
      setPage(route());
      setOffset(0);
      setFilter("");
      select("");
      setDetail(undefined);
      setAnswer(undefined);
      setError("");
    };
    addEventListener("hashchange", change);
    return () => removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    Promise.all([
      api(paths[page] + "?limit=15&offset=" + offset + filter),
      api("/health/ready"),
      page === 0
        ? Promise.all(
            [
              "/events",
              "/alerts",
              "/investigations",
              "/investigations?status=awaiting_review",
            ].map((p) => api(p + (p.includes("?") ? "&" : "?") + "limit=1")),
          )
        : [],
    ])
      .then(([list, health, stats]) => {
        if (active) {
          setRows(list.items);
          setTotal(list.total);
          setProvider(health.provider);
          setCounts(stats.map((s: Row) => s.total));
        }
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setRows([]);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [page, offset, filter, version]);
  useEffect(() => {
    if (!id) return;
    let active = true,
      timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const base = "/investigations/" + id;
        const [record, timeline, evidence, metrics] = await Promise.all([
          api(base),
          api(base + "/timeline"),
          api(base + "/evidence?limit=100"),
          api(base + "/metrics"),
        ]);
        if (active) {
          setDetail({ record, timeline, evidence, metrics });
          if (["queued", "running"].includes(record.status))
            timer = setTimeout(load, 2000);
        }
      } catch (e) {
        if (active) setError(String(e));
      }
    }
    load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, version]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      refresh((v) => v + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const cols = [
    ["title", "severity_hint", "status"],
    ["timestamp", "username", "source_ip", "event_type", "outcome"],
    ["title", "ingestion_status", "embedding_model"],
    ["id", "status", "provider", "created_at"],
    ["id", "status", "model"],
  ][page];
  function open(key: string) {
    if (key === id) {refresh(v=>v+1); return}
    setDetail(undefined);
    setComment("");
    select(key);
  }
  return (
    <div className="layout">
      <aside>
        <strong>◈ &nbsp; SecOps AI</strong>
        <small>OPERATIONS</small>
        <nav>
          {pages.map((p, i) => (
            <a
              key={p}
              aria-current={page === i ? "page" : undefined}
              href={"#" + p}
            >
              {p}
            </a>
          ))}
        </nav>
        <footer>
          {session.user.email}
          <br />
          {session.user.role}
          <button onClick={logout}>Sign out</button>
        </footer>
      </aside>
      <main>
        <header>
          <span>Security operations / Workspace</span>
          <Theme />
        </header>
        <h1>{pages[page]}</h1>
        <p className="mode">
          {provider && provider.toUpperCase() + " PROVIDER"}
        </p>
        {error && (
          <p role="alert">
            {error} <button onClick={() => refresh((v) => v + 1)}>Retry</button>
          </p>
        )}
        {page === 0 && (
          <section className="cards">
            {counts.map((c, i) => (
              <article key={i}>
                <small>
                  {
                    [
                      "Security events",
                      "Alerts",
                      "Investigations",
                      "Awaiting review",
                    ][i]
                  }
                </small>
                <b>{c}</b>
              </article>
            ))}
          </section>
        )}
        {page === 1 && (
          <form
            onSubmit={(e) => {
              const f = form(e);
              setFilter(
                "&" +
                  new URLSearchParams(
                    Object.entries(f)
                      .filter(([, v]) => v)
                      .map(([k, v]) => [k, String(v)]),
                  ),
              );
              setOffset(0);
            }}
          >
            {["username", "source_ip", "outcome"].map((k) => (
              <input
                key={k}
                name={k}
                placeholder={label(k)}
                aria-label={label(k)}
              />
            ))}
            <button>Filter</button>
          </form>
        )}
        {(page === 1 || page === 2) && analyst && (
          <form
            onSubmit={(e) => {
              const f = new FormData(e.currentTarget);
              e.preventDefault();
              act(async () =>
                setAnswer(
                  await api(page === 1 ? "/events/import" : "/documents", f),
                ),
              );
            }}
          >
            <input
              type="file"
              name="file"
              required
              aria-label="Upload file"
              accept={page === 1 ? ".csv,.json" : ".pdf,.md,.txt"}
            />
            <button disabled={busy}>
              Import {page === 1 ? "events" : "document"}
            </button>
          </form>
        )}
        {page === 2 && (
          <form
            onSubmit={(e) => {
              const f = form(e);
              act(async () =>
                setAnswer(await api("/rag/query", { ...f, mode: "hybrid" })),
              );
            }}
          >
            <input
              name="query"
              aria-label="Search policies"
              placeholder="Search policies and playbooks"
              required
            />
            <button disabled={busy}>Search knowledge</button>
          </form>
        )}
        {answer && (
          <article>
            <Fields value={answer} />
          </article>
        )}
        <section>
          <h2>
            {
              [
                "Alert queue",
                "Event stream",
                "Indexed documents",
                "Case queue",
                "Investigation performance",
              ][page]
            }
          </h2>
          {loading ? (
            <p role="status">Loading records…</p>
          ) : rows.length ? (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    {cols.map((c) => (
                      <th key={c}>{label(c)}</th>
                    ))}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      {cols.map((c) => (
                        <td key={c}>{String(r[c] ?? "—")}</td>
                      ))}
                      <td>
                        {page === 0 && analyst ? (
                          <button
                            disabled={busy}
                            onClick={() =>
                              act(async () => {
                                const item = await api("/investigations", {
                                  alert_id: r.id,
                                  request_id: crypto.randomUUID(),
                                });
                                open(item.id);
                              })
                            }
                          >
                            Investigate
                          </button>
                        ) : (
                          page >= 3 && (
                            <button onClick={() => open(r.id)}>Inspect</button>
                          )
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>No matching records.</p>
          )}
          <div className="pager">
            <span>{total} records</span>
            <button disabled={!offset} onClick={() => setOffset(offset - 15)}>
              Previous
            </button>
            <button
              disabled={offset + 15 >= total}
              onClick={() => setOffset(offset + 15)}
            >
              Next
            </button>
          </div>
        </section>
        {id && !detail && <p>Loading investigation…</p>}
        {detail && (
          <section>
            <h2>Investigation detail</h2>
            <p>
              {id} · {detail.record.status}
            </p>
            {page === 4 ? (
              <Analytics metrics={detail.metrics} />
            ) : (
              <>
                <h3>Agent execution</h3>
                <div aria-label="Investigation workflow">
                  {[["supervisor"],["log_analysis","knowledge","threat_intel"],["remediation"],["critic"],["reporting"],["human_review"],["finalize"]].map(stage=><div className="stage" key={stage[0]}>{stage.map(agent=>{
                    const runs=detail.timeline.items.filter((r:Row)=>r.agent===agent);
                    return runs.length ? runs.map((r:Row)=>(
                    <details key={r.id}>
                      <summary>
                        {label(r.agent)} · {r.status}
                      </summary>
                      <Fields value={r} />
                    </details>
                  )) : <span key={agent}>{label(agent)} · {agent==="human_review" && detail.record.status==="awaiting_review"?"awaiting decision":"no recorded run"}</span>
                  })}</div>)}
                </div>
                <h3>Incident report</h3>
                {detail.record.report ? (
                  <><a download={'report-'+id+'.json'} href={'data:application/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(detail.record.report,null,2))}>Download report JSON</a><Fields value={detail.record.report} /></>
                ) : (
                  <p>Report pending</p>
                )}
                <details>
                  <summary>Supporting evidence</summary>
                  <Fields value={detail.evidence.items} />
                </details>
                {analyst && detail.record.status === "queued" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(() => api("/investigations/" + id + "/start", {}))
                    }
                  >
                    Start
                  </button>
                )}
                {["failed", "interrupted", "awaiting_review"].includes(
                  detail.record.status,
                ) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(() => api("/investigations/" + id + "/resume", {}))
                    }
                  >
                    Resume
                  </button>
                )}
                {!analyst && detail.record.status === "awaiting_review" && (
                  <form>
                    <input
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="Review comment"
                      aria-label="Review comment"
                    />
                    {["approve", "reject"].map((decision) => (
                      <button
                        type="button"
                        disabled={
                          busy || (decision === "reject" && !comment.trim())
                        }
                        key={decision}
                        onClick={() =>
                          act(() =>
                            api("/investigations/" + id + "/review", {
                              decision,
                              comment,
                            }),
                          )
                        }
                      >
                        {decision}
                      </button>
                    ))}
                  </form>
                )}
              </>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
function Theme() {
  const toggle = () => {
    const theme =
      document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  };
  return <button onClick={toggle}>Switch theme</button>;
}
export function Desk() {
  const [session, setSession] = useState<Session>(),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    document.documentElement.dataset.theme =
      localStorage.getItem("theme") || "dark";
    const expire = () => {
      setSession(undefined);
      setError("Session expired. Sign in again.");
    };
    addEventListener("session-expired", expire);
    return () => removeEventListener("session-expired", expire);
  }, []);
  if (session)
    return <Workspace session={session} logout={() => setSession(undefined)} />;
  return (
    <main className="login">
      <article>
        <small>SECOPS AI</small>
        <h1>Analyst workspace</h1>
        <form
          onSubmit={async (e) => {
            const data = form(e);
            setBusy(true);
            setError("");
            try {
              setSession(await request("/auth/login", "", data));
            } catch (e) {
              setError(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Email
            <input name="email" type="email" autoComplete="username" required />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        {error && <p role="alert">{error}</p>}
      </article>
    </main>
  );
}
