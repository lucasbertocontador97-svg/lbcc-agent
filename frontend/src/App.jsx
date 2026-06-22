import { useState, useEffect, useRef, useCallback } from "react";
import { Bubble, TypingIndicator } from "./components/Bubble.jsx";
import { Icon } from "./components/Icons.jsx";
import { useSocket } from "./hooks/useSocket.js";
import { api } from "./services/api.js";

const QUICK = [
  "Abra o Google",
  "Abra o site da Receita Federal",
  "Acesse o FGTS Digital",
  "Pesquise no Google: CNPJ consulta",
];

// ── Páginas ───────────────────────────────────────────────────────────────────

function ExecutionsPage() {
  const [execs, setExecs]     = useState([]);
  const [selected, setSelected] = useState(null);
  const [logs, setLogs]       = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listExecutions().then(setExecs).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const openExec = async (ex) => {
    setSelected(ex);
    const l = await api.getExecLogs(ex.id).catch(() => []);
    setLogs(l);
  };

  const exportLogs = (exec_id) => {
    window.open(`/api/executions/${exec_id}/logs/export`, "_blank");
  };

  const STATUS_COLOR = {
    running:   "#3b6eea",
    completed: "#22c55e",
    error:     "#ef4444",
    stopped:   "#f59e0b",
    timeout:   "#a855f7",
  };

  if (loading) return <div className="page-center">Carregando...</div>;

  return (
    <div className="page">
      <div className="page-header">
        <h2>Execuções</h2>
        <span className="page-sub">{execs.length} execuções registradas</span>
      </div>

      <div className="exec-layout">
        <div className="exec-list">
          {execs.length === 0 && (
            <div className="empty">Nenhuma execução ainda.</div>
          )}
          {execs.map(ex => (
            <div
              key={ex.id}
              className={`exec-item${selected?.id === ex.id ? " active" : ""}`}
              onClick={() => openExec(ex)}
            >
              <div className="exec-status-dot"
                   style={{ background: STATUS_COLOR[ex.status] || "#666" }} />
              <div className="exec-info">
                <div className="exec-cmd">{ex.command}</div>
                <div className="exec-meta">
                  {new Date(ex.started_at).toLocaleString("pt-BR")}
                  {ex.duration_ms && ` · ${(ex.duration_ms/1000).toFixed(1)}s`}
                  {ex.retries > 0 && ` · ${ex.retries} retry`}
                </div>
              </div>
              <div className="exec-badge" style={{ color: STATUS_COLOR[ex.status] }}>
                {ex.status}
              </div>
            </div>
          ))}
        </div>

        {selected && (
          <div className="exec-detail">
            <div className="exec-detail-header">
              <div>
                <div style={{ fontWeight: 600, color: "var(--text)" }}>{selected.command}</div>
                <div style={{ fontSize: 12, color: "var(--text3)", marginTop: 4 }}>
                  {new Date(selected.started_at).toLocaleString("pt-BR")}
                  {selected.duration_ms && ` · ${(selected.duration_ms/1000).toFixed(1)}s`}
                </div>
              </div>
              <button className="btn-sm" onClick={() => exportLogs(selected.id)}>
                ↓ Exportar JSON
              </button>
            </div>
            <div className="log-list">
              {logs.map((l, i) => (
                <div key={i} className={`log-item${l.ok ? "" : " log-err"}`}>
                  <span className="log-action">{l.action}</span>
                  <span className="log-detail">
                    {l.detail?.url || l.detail?.selector || l.detail?.value || ""}
                  </span>
                  <span className="log-status">{l.ok ? "✓" : "✗"}</span>
                </div>
              ))}
              {logs.length === 0 && <div className="empty">Sem logs.</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MediaPage() {
  const [tab, setTab]         = useState("screenshots");
  const [screenshots, setShots] = useState([]);
  const [videos, setVideos]   = useState([]);
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    api.listScreenshots().then(setShots).catch(() => {});
    api.listVideos().then(setVideos).catch(() => {});
  }, []);

  return (
    <div className="page">
      <div className="page-header">
        <h2>Mídia</h2>
        <div className="tab-btns">
          {["screenshots", "videos"].map(t => (
            <button key={t} className={`tab-btn${tab === t ? " active" : ""}`}
                    onClick={() => setTab(t)}>
              {t === "screenshots" ? `📸 Screenshots (${screenshots.length})` : `🎬 Vídeos (${videos.length})`}
            </button>
          ))}
        </div>
      </div>

      {tab === "screenshots" && (
        <div className="media-grid">
          {screenshots.length === 0 && <div className="empty">Nenhum screenshot ainda.</div>}
          {screenshots.map(s => (
            <div key={s.filename} className="media-card" onClick={() => setPreview(s.url)}>
              <img src={s.url} alt={s.filename} />
              <div className="media-name">{s.filename}</div>
            </div>
          ))}
        </div>
      )}

      {tab === "videos" && (
        <div className="media-video-list">
          {videos.length === 0 && <div className="empty">Nenhum vídeo ainda.</div>}
          {videos.map(v => (
            <div key={v.filename} className="video-item">
              <video src={v.url} controls style={{ width: "100%", borderRadius: 8 }} />
              <div className="media-name">{v.filename} ({(v.size/1024).toFixed(0)} KB)</div>
            </div>
          ))}
        </div>
      )}

      {preview && (
        <div className="lightbox" onClick={() => setPreview(null)}>
          <img src={preview} alt="preview" />
        </div>
      )}
    </div>
  );
}

// ── App principal ─────────────────────────────────────────────────────────────

export default function App() {
  const [page, setPage]           = useState("chat");
  const [convs, setConvs]         = useState([]);
  const [activeConv, setActiveConv] = useState(null);
  const [events, setEvents]       = useState([]);
  const [llmHistory, setLlmHistory] = useState([]);
  const [busy, setBusy]           = useState(false);
  const [manualMode, setManualMode] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [browserUrl, setBrowserUrl]   = useState("");
  const [lastScreenshot, setLastScreenshot] = useState(null);
  const [input, setInput]         = useState("");
  const [currentExecId, setCurrentExecId] = useState(null);
  const bottomRef   = useRef(null);
  const textareaRef = useRef(null);

  // ── Socket ──────────────────────────────────────────────────────────────────

  const onEvent = useCallback((ev) => {
    const { type } = ev;
    if (type === "pong") return;

    if (type === "screenshot") {
      setLastScreenshot(ev.b64);
      setEvents(prev => [...prev, ev]);
      return;
    }

    if (["action","result","system","retry"].includes(type)) {
      setEvents(prev => [...prev, ev]);
      return;
    }

    if (type === "exec_start") {
      setCurrentExecId(ev.exec_id);
      return;
    }

    if (type === "exec_end") {
      setCurrentExecId(null);
      return;
    }

    if (["done","error","ask","stopped","timeout"].includes(type)) {
      setEvents(prev => [...prev, ev]);
      setBusy(false);
      if (type === "done") {
        setLlmHistory(prev => [...prev, { role: "assistant", content: ev.text || "" }]);
      }
      return;
    }
  }, []);

  const { connected, send } = useSocket(onEvent);

  // ── Init ────────────────────────────────────────────────────────────────────

  useEffect(() => {
    api.listConvs().then(setConvs).catch(() => {});
    const poll = () => api.status()
      .then(s => { setBrowserUrl(s.browser_url || ""); setManualMode(s.manual_mode || false); })
      .catch(() => {});
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, busy]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [input]);

  // ── Conversations ────────────────────────────────────────────────────────────

  const newConv = async () => {
    const c = await api.createConv("Nova conversa");
    setConvs(prev => [c, ...prev]);
    setActiveConv(c);
    setEvents([]);
    setLlmHistory([]);
    setPage("chat");
  };

  const openConv = async (c) => {
    setActiveConv(c);
    setPage("chat");
    const msgs = await api.getMessages(c.id).catch(() => []);
    setEvents(msgs.map(m => ({ type: m.role === "user" ? "user" : "done", text: m.content })));
    setLlmHistory(msgs.filter(m => ["user","assistant"].includes(m.role))
                      .map(m => ({ role: m.role, content: m.content })));
  };

  // ── Send ─────────────────────────────────────────────────────────────────────

  const sendMsg = async (text) => {
    text = text.trim();
    if (!text || busy) return;

    let conv = activeConv;
    if (!conv) {
      conv = await api.createConv(text.substring(0, 60));
      setConvs(prev => [conv, ...prev]);
      setActiveConv(conv);
    }

    setEvents(prev => [...prev, { type: "user", text }]);
    const newHist = [...llmHistory, { role: "user", content: text }];
    setLlmHistory(newHist);
    setBusy(true);
    setInput("");

    send({ type: "chat", text, conv_id: conv.id, history: newHist });
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(input); }
  };

  const stopExec = () => send({ type: "stop" });

  const toggleManual = () => {
    if (manualMode) {
      send({ type: "manual_off" });
      setManualMode(false);
    } else {
      send({ type: "manual_on" });
      setManualMode(true);
    }
  };

  const takeScreenshot = () => send({ type: "screenshot" });

  // ── Render ───────────────────────────────────────────────────────────────────

  const NAV = [
    { id: "chat",       label: "💬 Chat" },
    { id: "executions", label: "⚡ Execuções" },
    { id: "media",      label: "🖼️ Mídia" },
  ];

  return (
    <div className="root">
      {/* Sidebar */}
      <aside className={`sidebar${sidebarOpen ? "" : " collapsed"}`}>
        <div className="sidebar-logo">
          <div className="logo-icon">⚡</div>
          <div>
            <div className="logo-name">LBCC Agent</div>
            <div className="logo-sub">v1.1</div>
          </div>
        </div>

        <button className="sidebar-new" onClick={newConv}>
          <Icon.Plus /> Nova conversa
        </button>

        <div className="sidebar-section">Navegação</div>
        {NAV.map(n => (
          <div key={n.id}
               className={`conv-item${page === n.id && !activeConv ? " active" : ""}`}
               onClick={() => { setPage(n.id); if (n.id !== "chat") setActiveConv(null); }}>
            {n.label}
          </div>
        ))}

        <div className="sidebar-section">Conversas</div>
        <div className="conv-list">
          {convs.map(c => (
            <div key={c.id}
                 className={`conv-item${activeConv?.id === c.id ? " active" : ""}`}
                 onClick={() => openConv(c)}>
              <Icon.Msg /> {c.title}
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <div className={`status-dot${connected ? "" : " off"}`} />
          {connected ? "Conectado" : "Reconectando..."}
        </div>
      </aside>

      {/* Main */}
      <div className="main">
        {/* Topbar */}
        <div className="topbar">
          <button className="icon-btn" onClick={() => setSidebarOpen(v => !v)}>
            <Icon.Menu />
          </button>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="topbar-title">
              {page === "executions" ? "Execuções" :
               page === "media"      ? "Mídia"     :
               activeConv?.title || "LBCC Agent"}
            </div>
            {browserUrl && page === "chat" && (
              <div className="topbar-url">{browserUrl}</div>
            )}
          </div>

          {/* Viewer última screenshot */}
          {lastScreenshot && page === "chat" && (
            <div className="live-thumb"
                 onClick={() => window.open(`data:image/jpeg;base64,${lastScreenshot}`, "_blank")}
                 title="Última screenshot — clique para ampliar">
              <img src={`data:image/jpeg;base64,${lastScreenshot}`} alt="live" />
              <span className="live-label">AO VIVO</span>
            </div>
          )}

          {/* Botões de controle */}
          {page === "chat" && (
            <>
              <button className="icon-btn" title="Screenshot" onClick={takeScreenshot}>
                <Icon.Camera />
              </button>
              <button
                className={`ctrl-btn${manualMode ? " manual-active" : ""}`}
                onClick={toggleManual}
                title={manualMode ? "Devolver controle ao agente" : "Assumir controle manual"}
              >
                {manualMode ? "🖐 Manual" : "🤖 Auto"}
              </button>
              {busy && (
                <button className="stop-btn" onClick={stopExec}>
                  ⏹ PARAR
                </button>
              )}
            </>
          )}
        </div>

        {/* Conteúdo */}
        {page === "executions" ? <ExecutionsPage /> :
         page === "media"      ? <MediaPage /> :
         /* Chat */
         <>
           <div className="messages">
             {events.length === 0 && !busy && (
               <div className="welcome">
                 <div className="welcome-icon">⚡</div>
                 <h2>LBCC Agent</h2>
                 <p>Agente operacional digital para escritório contábil.<br />
                    Digite um comando ou escolha um exemplo.</p>
                 <div className="quick-btns">
                   {QUICK.map(q => (
                     <button key={q} className="quick-btn" onClick={() => sendMsg(q)}>{q}</button>
                   ))}
                 </div>
               </div>
             )}

             {events.map((ev, i) => (
               <Bubble key={i} event={ev}
                 onApprove={() => {
                   setEvents(prev => [...prev, { type:"system", text:"Aprovado. Continuando..." }]);
                 }}
                 onCancel={() => {
                   setBusy(false);
                   setEvents(prev => [...prev, { type:"system", text:"Cancelado." }]);
                 }}
               />
             ))}

             {busy && <TypingIndicator />}
             <div ref={bottomRef} />
           </div>

           {/* Input */}
           <div className="input-area">
             {manualMode && (
               <div className="manual-banner">
                 🖐 Modo Manual ativo — você controla o navegador. Clique em "Auto" para devolver ao agente.
               </div>
             )}
             <div className="input-wrap">
               <textarea
                 ref={textareaRef}
                 value={input}
                 onChange={e => setInput(e.target.value)}
                 onKeyDown={onKey}
                 placeholder={manualMode ? "Modo manual ativo..." : 'Ex: "Abra o Google"'}
                 rows={1}
                 disabled={busy || manualMode}
               />
               <button className="send-btn"
                       onClick={() => sendMsg(input)}
                       disabled={!input.trim() || busy || manualMode}>
                 <Icon.Send />
               </button>
             </div>
             <div className="input-hint">Enter para enviar · Shift+Enter para nova linha</div>
           </div>
         </>
        }
      </div>
    </div>
  );
}
