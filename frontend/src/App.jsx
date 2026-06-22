import { useState, useEffect, useRef, useCallback } from "react";
import { Bubble, TypingIndicator } from "./components/Bubble.jsx";
import { Icon } from "./components/Icons.jsx";
import { useSocket } from "./hooks/useSocket.js";
import { api } from "./services/api.js";

const QUICK = [
  "Abra o Google",
  "google_search",
  "receita_federal",
  "Abra o site da Receita Federal",
  "Pesquise no Google: CNPJ consulta",
];

// ── Página Procedimentos ───────────────────────────────────────────────────────
function ProceduresPage({ onRun }) {
  const [procs, setProcs]     = useState([]);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const load = () => api.listProcedures().then(setProcs).catch(() => {});
  useEffect(() => { load(); }, []);

  const del = async (name) => {
    if (!confirm(`Excluir "${name}"?`)) return;
    await fetch(`/api/procedures/${name}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Procedimentos</h2>
        <span className="page-sub">{procs.length} salvos em data/procedures/</span>
      </div>
      <div className="exec-layout">
        <div className="exec-list">
          {procs.map(p => (
            <div key={p.name}
                 className={`exec-item${selected?.name === p.name ? " active" : ""}`}
                 onClick={() => setSelected(p)}>
              <div className="exec-status-dot" style={{ background: "#3b6eea" }} />
              <div className="exec-info">
                <div className="exec-cmd">{p.name}</div>
                <div className="exec-meta">{p.description} · {p.steps_count} passos</div>
              </div>
              <div style={{ display:"flex", gap:6, flexShrink:0 }}>
                <button className="btn-sm" onClick={e => { e.stopPropagation(); onRun(p.name); }}>
                  ▶ Run
                </button>
                <button className="btn-sm" style={{ color:"var(--red)" }}
                        onClick={e => { e.stopPropagation(); del(p.name); }}>
                  ✕
                </button>
              </div>
            </div>
          ))}
          {procs.length === 0 && <div className="empty">Nenhum procedimento ainda.</div>}
        </div>

        {selected && (
          <div className="exec-detail">
            <div className="exec-detail-header">
              <div>
                <div style={{ fontWeight:600, color:"var(--text)" }}>{selected.name}</div>
                <div style={{ fontSize:12, color:"var(--text3)", marginTop:4 }}>
                  {selected.description}
                  {selected.variables?.length > 0 &&
                    ` · Variáveis: ${selected.variables.join(", ")}`}
                </div>
              </div>
              <button className="btn-sm" onClick={() => onRun(selected.name)}>▶ Executar</button>
            </div>
            <div className="log-list">
              {(selected.steps || []).map((s, i) => (
                <div key={i} className="log-item">
                  <span className="log-action">{s.action}</span>
                  <span className="log-detail">
                    {s.url || s.selector || s.value || s.key || s.message || ""}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Página Execuções ──────────────────────────────────────────────────────────
function ExecutionsPage() {
  const [execs, setExecs]     = useState([]);
  const [selected, setSelected] = useState(null);
  const [logs, setLogs]       = useState([]);

  useEffect(() => {
    api.listExecutions().then(setExecs).catch(() => {});
  }, []);

  const openExec = async (ex) => {
    setSelected(ex);
    const l = await api.getExecLogs(ex.id).catch(() => []);
    setLogs(l);
  };

  const STATUS_COLOR = {
    running: "#3b6eea", completed: "#22c55e",
    error: "#ef4444",   stopped: "#f59e0b", timeout: "#a855f7",
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Execuções</h2>
        <span className="page-sub">{execs.length} registradas</span>
      </div>
      <div className="exec-layout">
        <div className="exec-list">
          {execs.map(ex => (
            <div key={ex.id}
                 className={`exec-item${selected?.id === ex.id ? " active" : ""}`}
                 onClick={() => openExec(ex)}>
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
          {execs.length === 0 && <div className="empty">Nenhuma execução ainda.</div>}
        </div>
        {selected && (
          <div className="exec-detail">
            <div className="exec-detail-header">
              <div style={{ fontWeight:600, color:"var(--text)" }}>{selected.command}</div>
              <button className="btn-sm"
                      onClick={() => window.open(`/api/executions/${selected.id}/logs/export`, "_blank")}>
                ↓ JSON
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

// ── Página Mídia ──────────────────────────────────────────────────────────────
function MediaPage() {
  const [tab, setTab]     = useState("screenshots");
  const [shots, setShots] = useState([]);
  const [videos, setVideos] = useState([]);
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
          {["screenshots","videos"].map(t => (
            <button key={t} className={`tab-btn${tab===t?" active":""}`} onClick={() => setTab(t)}>
              {t==="screenshots" ? `📸 (${shots.length})` : `🎬 (${videos.length})`}
            </button>
          ))}
        </div>
      </div>
      {tab === "screenshots" && (
        <div className="media-grid">
          {shots.length === 0 && <div className="empty">Nenhum screenshot.</div>}
          {shots.map(s => (
            <div key={s.filename} className="media-card" onClick={() => setPreview(s.url)}>
              <img src={s.url} alt={s.filename} />
              <div className="media-name">{s.filename}</div>
            </div>
          ))}
        </div>
      )}
      {tab === "videos" && (
        <div className="media-video-list">
          {videos.length === 0 && <div className="empty">Nenhum vídeo.</div>}
          {videos.map(v => (
            <div key={v.filename} className="video-item">
              <video src={v.url} controls style={{ width:"100%", borderRadius:8 }} />
              <div className="media-name">{v.filename}</div>
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

// ── Página Anexos ─────────────────────────────────────────────────────────────
function AttachmentsPage() {
  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);

  const load = () => fetch("/api/attachments").then(r => r.json()).then(setFiles).catch(() => {});
  useEffect(() => { load(); }, []);

  const upload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    await fetch("/api/attachments", { method: "POST", body: fd });
    load();
  };

  const del = async (name) => {
    await fetch(`/api/attachments/${name}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Anexos</h2>
        <button className="btn-sm" onClick={() => fileRef.current?.click()}>+ Upload</button>
        <input ref={fileRef} type="file" style={{ display:"none" }} onChange={upload} />
      </div>
      <div style={{ padding:16 }}>
        {files.length === 0 && <div className="empty">Nenhum anexo. Faça upload de PDFs, XMLs, etc.</div>}
        {files.map(f => (
          <div key={f.filename} className="exec-item">
            <span style={{ fontSize:20 }}>📎</span>
            <div className="exec-info">
              <div className="exec-cmd">{f.filename}</div>
              <div className="exec-meta">{(f.size/1024).toFixed(1)} KB</div>
            </div>
            <button className="btn-sm" style={{ color:"var(--red)" }} onClick={() => del(f.filename)}>✕</button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── App Principal ─────────────────────────────────────────────────────────────
export default function App() {
  const [page, setPage]           = useState("chat");
  const [convs, setConvs]         = useState([]);
  const [activeConv, setActiveConv] = useState(null);
  const [events, setEvents]       = useState([]);
  const [llmHistory, setLlmHistory] = useState([]);
  const [busy, setBusy]           = useState(false);
  const [paused, setPaused]       = useState(false);
  const [stepMode, setStepMode]   = useState(false);
  const [stepWaiting, setStepWaiting] = useState(false);
  const [manualMode, setManualMode] = useState(false);
  const [approvalPending, setApprovalPending] = useState(false);
  const [approvalMsg, setApprovalMsg]         = useState("");
  const [sidebarOpen, setSidebarOpen]         = useState(true);
  const [browserUrl, setBrowserUrl]           = useState("");
  const [lastScreenshot, setLastScreenshot]   = useState(null);
  const [input, setInput]         = useState("");
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

    if (["action","result","system","retry","paused","resumed"].includes(type)) {
      setEvents(prev => [...prev, ev]);
      if (type === "paused") setPaused(true);
      if (type === "resumed") setPaused(false);
      return;
    }

    if (type === "step_waiting") {
      setStepWaiting(true);
      setEvents(prev => [...prev, ev]);
      return;
    }

    if (type === "exec_start") return;
    if (type === "exec_end") return;

    if (type === "ask") {
      setApprovalPending(true);
      setApprovalMsg(ev.text);
      setEvents(prev => [...prev, ev]);
      setBusy(false);
      return;
    }

    if (["done","error","stopped","timeout"].includes(type)) {
      setEvents(prev => [...prev, ev]);
      setBusy(false);
      setStepWaiting(false);
      if (type === "done") {
        setLlmHistory(prev => [...prev, { role:"assistant", content: ev.text || "" }]);
      }
      return;
    }
  }, []);

  const { connected, send } = useSocket(onEvent);

  // ── Init ────────────────────────────────────────────────────────────────────
  useEffect(() => {
    api.listConvs().then(setConvs).catch(() => {});
    const poll = () => api.status().then(s => {
      setBrowserUrl(s.browser_url || "");
      setManualMode(s.manual_mode || false);
      setPaused(s.paused || false);
    }).catch(() => {});
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:"smooth" }); }, [events, busy]);

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
    setActiveConv(c); setEvents([]); setLlmHistory([]); setPage("chat");
  };

  const openConv = async (c) => {
    setActiveConv(c); setPage("chat");
    const msgs = await api.getMessages(c.id).catch(() => []);
    setEvents(msgs.map(m => ({ type: m.role==="user"?"user":"done", text: m.content })));
    setLlmHistory(msgs.filter(m => ["user","assistant"].includes(m.role))
                      .map(m => ({ role: m.role, content: m.content })));
  };

  const runProcedure = async (name) => {
    setPage("chat");
    await sendMsg(name);
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

    setEvents(prev => [...prev, { type:"user", text }]);
    const newHist = [...llmHistory, { role:"user", content: text }];
    setLlmHistory(newHist);
    setBusy(true); setInput("");
    setApprovalPending(false); setStepWaiting(false);

    send({ type:"chat", text, conv_id: conv.id, history: newHist });
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(input); }
  };

  // ── Controles ─────────────────────────────────────────────────────────────────
  const doStop   = () => { send({ type:"stop" }); setBusy(false); };
  const doPause  = () => { send({ type: paused ? "resume" : "pause" }); };
  const doApprove = () => { setApprovalPending(false); setBusy(true); send({ type:"approve" }); };
  const doReject  = () => { setApprovalPending(false); send({ type:"reject" }); };
  const doNextStep = () => { setStepWaiting(false); send({ type:"next_step" }); };
  const toggleStep = () => {
    const next = !stepMode;
    setStepMode(next);
    send({ type: next ? "step_mode_on" : "step_mode_off" });
  };
  const toggleManual = () => {
    send({ type: manualMode ? "manual_off" : "manual_on" });
    setManualMode(v => !v);
  };

  // ── NAV ───────────────────────────────────────────────────────────────────────
  const NAV = [
    { id:"chat",        label:"💬 Chat" },
    { id:"procedures",  label:"📋 Procedimentos" },
    { id:"executions",  label:"⚡ Execuções" },
    { id:"media",       label:"🖼️ Mídia" },
    { id:"attachments", label:"📎 Anexos" },
  ];

  return (
    <div className="root">
      {/* Sidebar */}
      <aside className={`sidebar${sidebarOpen ? "" : " collapsed"}`}>
        <div className="sidebar-logo">
          <div className="logo-icon">⚡</div>
          <div>
            <div className="logo-name">LBCC Agent</div>
            <div className="logo-sub">v2.0</div>
          </div>
        </div>

        <button className="sidebar-new" onClick={newConv}>
          <Icon.Plus /> Nova conversa
        </button>

        <div className="sidebar-section">Navegação</div>
        {NAV.map(n => (
          <div key={n.id}
               className={`conv-item${page===n.id && !activeConv?"active":""}`}
               onClick={() => { setPage(n.id); if(n.id!=="chat") setActiveConv(null); }}>
            {n.label}
          </div>
        ))}

        <div className="sidebar-section">Conversas</div>
        <div className="conv-list">
          {convs.map(c => (
            <div key={c.id}
                 className={`conv-item${activeConv?.id===c.id?" active":""}`}
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
          <div style={{ flex:1, minWidth:0 }}>
            <div className="topbar-title">
              {page==="procedures"?"Procedimentos":page==="executions"?"Execuções":
               page==="media"?"Mídia":page==="attachments"?"Anexos":
               activeConv?.title || "LBCC Agent"}
            </div>
            {browserUrl && page==="chat" && (
              <div className="topbar-url">{browserUrl}</div>
            )}
          </div>

          {/* Viewer AO VIVO */}
          {lastScreenshot && page==="chat" && (
            <div className="live-thumb"
                 onClick={() => window.open(`data:image/jpeg;base64,${lastScreenshot}`,"_blank")}
                 title="Última screenshot">
              <img src={`data:image/jpeg;base64,${lastScreenshot}`} alt="live" />
              <span className="live-label">AO VIVO</span>
            </div>
          )}

          {page==="chat" && (
            <>
              <button className="icon-btn" title="Screenshot" onClick={() => send({type:"screenshot"})}>
                <Icon.Camera />
              </button>
              {/* Passo a passo */}
              <button className={`ctrl-btn${stepMode?" manual-active":""}`}
                      onClick={toggleStep} title="Modo passo a passo">
                {stepMode ? "🔢 Passo" : "🔢"}
              </button>
              {/* Pause/Resume */}
              {busy && (
                <button className="ctrl-btn" onClick={doPause}
                        title={paused ? "Retomar" : "Pausar"}>
                  {paused ? "▶" : "⏸"}
                </button>
              )}
              {/* Manual */}
              <button className={`ctrl-btn${manualMode?" manual-active":""}`}
                      onClick={toggleManual}>
                {manualMode ? "🖐 Manual" : "🤖 Auto"}
              </button>
              {/* Stop */}
              {busy && (
                <button className="stop-btn" onClick={doStop}>⏹ PARAR</button>
              )}
            </>
          )}
        </div>

        {/* Conteúdo */}
        {page==="procedures"  ? <ProceduresPage onRun={runProcedure} /> :
         page==="executions"  ? <ExecutionsPage /> :
         page==="media"       ? <MediaPage /> :
         page==="attachments" ? <AttachmentsPage /> :

         /* Chat */
         <>
           <div className="messages">
             {events.length===0 && !busy && (
               <div className="welcome">
                 <div className="welcome-icon">⚡</div>
                 <h2>LBCC Agent v2</h2>
                 <p>Agente operacional com sessões persistentes, procedimentos e controle humano.</p>
                 <div className="quick-btns">
                   {QUICK.map(q => (
                     <button key={q} className="quick-btn" onClick={() => sendMsg(q)}>{q}</button>
                   ))}
                 </div>
               </div>
             )}

             {events.map((ev, i) => (
               <Bubble key={i} event={ev}
                 onApprove={doApprove}
                 onCancel={doReject}
               />
             ))}

             {/* Banner passo a passo */}
             {stepWaiting && (
               <div className="step-banner">
                 <span>⏭ Aguardando aprovação do próximo passo</span>
                 <button className="btn-approve" onClick={doNextStep}>Próximo →</button>
               </div>
             )}

             {/* Banner aprovação */}
             {approvalPending && (
               <div className="approval-banner">
                 <div className="approval-msg">🤔 {approvalMsg}</div>
                 <div className="approve-btns">
                   <button className="btn-approve" onClick={doApprove}>✅ Aprovar</button>
                   <button className="btn-cancel"  onClick={doReject}>❌ Rejeitar</button>
                 </div>
               </div>
             )}

             {busy && !approvalPending && <TypingIndicator />}
             <div ref={bottomRef} />
           </div>

           {/* Input */}
           <div className="input-area">
             {manualMode && (
               <div className="manual-banner">
                 🖐 Modo Manual ativo — você controla o navegador.
               </div>
             )}
             {paused && (
               <div className="manual-banner" style={{ color:"#60a5fa" }}>
                 ⏸ Execução pausada — clique ▶ na barra para retomar.
               </div>
             )}
             <div className="input-wrap">
               <textarea ref={textareaRef} value={input}
                         onChange={e => setInput(e.target.value)}
                         onKeyDown={onKey}
                         placeholder={manualMode ? "Modo manual ativo..." : 'Ex: "google_search" ou "Abra o e-CAC"'}
                         rows={1} disabled={busy || manualMode} />
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
