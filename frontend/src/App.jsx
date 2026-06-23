import { useState, useEffect, useRef, useCallback } from "react";
import { Bubble, TypingIndicator } from "./components/Bubble.jsx";
import { Icon } from "./components/Icons.jsx";
import { useSocket } from "./hooks/useSocket.js";
import { api } from "./services/api.js";

const QUICK = [
  "Abra o Google",
  "listar abas",
  "Abra o YouTube em nova aba",
  "google_search",
  "receita_federal",
];

// ── Gerenciador de Abas ───────────────────────────────────────────────────────
function TabBar({ tabs, onSwitch, onClose, onNew, send }) {
  if (!tabs || tabs.length === 0) return null;
  return (
    <div className="tab-bar">
      {tabs.map(tab => (
        <div key={tab.index}
             className={`tab-item${tab.active ? " tab-active" : ""}`}
             onClick={() => onSwitch(tab.index)}>
          <span className="tab-favicon">🌐</span>
          <span className="tab-title">{tab.title?.substring(0, 20) || "Nova aba"}</span>
          {tabs.length > 1 && (
            <button className="tab-close"
                    onClick={e => { e.stopPropagation(); onClose(tab.index); }}>
              ×
            </button>
          )}
        </div>
      ))}
      <button className="tab-new" onClick={onNew} title="Nova aba">+</button>
    </div>
  );
}

// ── Páginas secundárias ───────────────────────────────────────────────────────
function ProceduresPage({ onRun }) {
  const [procs, setProcs] = useState([]);
  const [selected, setSelected] = useState(null);
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
        <span className="page-sub">{procs.length} em data/procedures/</span>
      </div>
      <div className="exec-layout">
        <div className="exec-list">
          {procs.map(p => (
            <div key={p.name}
                 className={`exec-item${selected?.name===p.name?" active":""}`}
                 onClick={() => setSelected(p)}>
              <div className="exec-status-dot" style={{ background:"#3b6eea" }} />
              <div className="exec-info">
                <div className="exec-cmd">{p.name}</div>
                <div className="exec-meta">
                  {p.last_status || "nunca_executado"}
                  {p.last_execution && ` | ${new Date(p.last_execution).toLocaleString()}`}
                </div>
                <div className="exec-meta">{p.description} · {p.steps_count} passos
                  {p.variables?.length > 0 && ` · vars: ${p.variables.join(", ")}`}
                </div>
              </div>
              <div style={{ display:"flex", gap:6, flexShrink:0 }}>
                <button className="btn-sm" onClick={e=>{e.stopPropagation();onRun(p.name);}}>▶</button>
                <button className="btn-sm" style={{ color:"var(--red)" }}
                        onClick={e=>{e.stopPropagation();del(p.name);}}>✕</button>
              </div>
            </div>
          ))}
          {procs.length===0 && <div className="empty">Nenhum procedimento.</div>}
        </div>
        {selected && (
          <div className="exec-detail">
            <div className="exec-detail-header">
              <div>
                <div style={{ fontWeight:600, color:"var(--text)" }}>{selected.name}</div>
                <div className="exec-meta">
                  {selected.steps_count} passos | {selected.last_status || "nunca_executado"}
                  {selected.last_execution && ` | ultima execucao: ${new Date(selected.last_execution).toLocaleString()}`}
                </div>
              </div>
              <button className="btn-sm" onClick={() => onRun(selected.name)}>▶ Executar</button>
            </div>
            <div className="log-list">
              {(selected.steps||[]).map((s,i) => (
                <div key={i} className="log-item">
                  <span className="log-action">{s.action}</span>
                  <span className="log-detail">{s.url||s.selector||s.value||s.key||s.message||""}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ExecutionsPage() {
  const [execs, setExecs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [logs, setLogs] = useState([]);
  useEffect(() => { api.listExecutions().then(setExecs).catch(()=>{}); }, []);
  const open = async (ex) => {
    setSelected(ex);
    const l = await api.getExecLogs(ex.id).catch(()=>[]);
    setLogs(l);
  };
  const STATUS = { running:"#3b6eea", completed:"#22c55e", error:"#ef4444",
                   stopped:"#f59e0b", timeout:"#a855f7" };
  return (
    <div className="page">
      <div className="page-header">
        <h2>Execuções</h2>
        <span className="page-sub">{execs.length} registradas</span>
      </div>
      <div className="exec-layout">
        <div className="exec-list">
          {execs.map(ex => (
            <div key={ex.id} className={`exec-item${selected?.id===ex.id?" active":""}`}
                 onClick={() => open(ex)}>
              <div className="exec-status-dot" style={{ background: STATUS[ex.status]||"#666" }} />
              <div className="exec-info">
                <div className="exec-cmd">{ex.command}</div>
                <div className="exec-meta">
                  {new Date(ex.started_at).toLocaleString("pt-BR")}
                  {ex.duration_ms && ` · ${(ex.duration_ms/1000).toFixed(1)}s`}
                </div>
              </div>
              <div className="exec-badge" style={{ color: STATUS[ex.status] }}>{ex.status}</div>
            </div>
          ))}
          {execs.length===0 && <div className="empty">Nenhuma execução.</div>}
        </div>
        {selected && (
          <div className="exec-detail">
            <div className="exec-detail-header">
              <div style={{ fontWeight:600 }}>{selected.command}</div>
              <button className="btn-sm"
                      onClick={() => window.open(`/api/executions/${selected.id}/logs/export`,"_blank")}>
                ↓ JSON
              </button>
            </div>
            <div className="log-list">
              {logs.map((l,i) => (
                <div key={i} className={`log-item${l.ok?"":" log-err"}`}>
                  <span className="log-action">{l.action}</span>
                  <span className="log-detail">{l.detail?.url||l.detail?.selector||l.detail?.value||""}</span>
                  <span className="log-status">{l.ok?"✓":"✗"}</span>
                </div>
              ))}
              {logs.length===0 && <div className="empty">Sem logs.</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MediaPage() {
  const [tab, setTab] = useState("screenshots");
  const [shots, setShots] = useState([]);
  const [videos, setVideos] = useState([]);
  const [preview, setPreview] = useState(null);
  useEffect(() => {
    api.listScreenshots().then(setShots).catch(()=>{});
    api.listVideos().then(setVideos).catch(()=>{});
  }, []);
  return (
    <div className="page">
      <div className="page-header">
        <h2>Mídia</h2>
        <div className="tab-btns">
          {["screenshots","videos"].map(t => (
            <button key={t} className={`tab-btn${tab===t?" active":""}`} onClick={()=>setTab(t)}>
              {t==="screenshots"?`📸 (${shots.length})`:`🎬 (${videos.length})`}
            </button>
          ))}
        </div>
      </div>
      {tab==="screenshots" && (
        <div className="media-grid">
          {shots.length===0 && <div className="empty">Nenhum screenshot.</div>}
          {shots.map(s => (
            <div key={s.filename} className="media-card" onClick={()=>setPreview(s.url)}>
              <img src={s.url} alt={s.filename} />
              <div className="media-name">{s.filename}</div>
            </div>
          ))}
        </div>
      )}
      {tab==="videos" && (
        <div className="media-video-list">
          {videos.length===0 && <div className="empty">Nenhum vídeo.</div>}
          {videos.map(v => (
            <div key={v.filename} className="video-item">
              <video src={v.url} controls style={{width:"100%",borderRadius:8}} />
              <div className="media-name">{v.filename}</div>
            </div>
          ))}
        </div>
      )}
      {preview && (
        <div className="lightbox" onClick={()=>setPreview(null)}>
          <img src={preview} alt="preview" />
        </div>
      )}
    </div>
  );
}

function FilesPage() {
  const [files, setFiles] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [tab, setTab] = useState("downloads");
  const fileRef = useRef(null);
  const load = () => {
    api.listFiles().then(setFiles).catch(()=>{});
    api.listAttachments().then(setAttachments).catch(()=>{});
  };
  useEffect(() => { load(); }, []);
  const upload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    await fetch("/api/attachments", { method:"POST", body:fd });
    load();
  };
  const fmt = (b) => b < 1024 ? `${b}B` : b < 1048576 ? `${(b/1024).toFixed(1)}KB` : `${(b/1048576).toFixed(1)}MB`;
  return (
    <div className="page">
      <div className="page-header">
        <h2>Arquivos</h2>
        <div className="tab-btns">
          <button className={`tab-btn${tab==="downloads"?" active":""}`} onClick={()=>setTab("downloads")}>
            📥 Downloads ({files.length})
          </button>
          <button className={`tab-btn${tab==="attachments"?" active":""}`} onClick={()=>setTab("attachments")}>
            📎 Anexos ({attachments.length})
          </button>
        </div>
        {tab==="attachments" && (
          <>
            <button className="btn-sm" onClick={()=>fileRef.current?.click()}>+ Upload</button>
            <input ref={fileRef} type="file" style={{display:"none"}} onChange={upload} />
          </>
        )}
      </div>
      <div style={{ padding:16, overflow:"auto", flex:1 }}>
        {tab==="downloads" && (
          <>
            {files.length===0 && <div className="empty">Nenhum download ainda. Arquivos baixados pelo agente aparecerão aqui.</div>}
            {files.map(f => (
              <div key={f.filename} className="exec-item">
                <span style={{fontSize:20}}>📄</span>
                <div className="exec-info">
                  <div className="exec-cmd">{f.filename}</div>
                  <div className="exec-meta">{fmt(f.size)} · {new Date(f.modified).toLocaleString("pt-BR")}</div>
                </div>
                <a href={f.url} download={f.filename} className="btn-sm">↓ Baixar</a>
              </div>
            ))}
          </>
        )}
        {tab==="attachments" && (
          <>
            {attachments.length===0 && <div className="empty">Nenhum anexo. Faça upload de PDFs, XMLs para usar no agente.</div>}
            {attachments.map(f => (
              <div key={f.filename} className="exec-item">
                <span style={{fontSize:20}}>📎</span>
                <div className="exec-info">
                  <div className="exec-cmd">{f.filename}</div>
                  <div className="exec-meta">{fmt(f.size)}</div>
                </div>
                <button className="btn-sm" style={{color:"var(--red)"}}
                        onClick={async()=>{await fetch(`/api/attachments/${f.filename}`,{method:"DELETE"});load();}}>
                  ✕
                </button>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ── App Principal ─────────────────────────────────────────────────────────────
function CredentialsPage() {
  const emptyForm = { alias: "", label: "", url: "", email: "", password: "", aliases: "" };
  const [items, setItems] = useState([]);
  const [path, setPath] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const load = () => api.listCredentials().then(data => {
    setItems(data.credentials || []);
    setPath(data.path || "");
  }).catch(() => setMessage("Nao consegui carregar as credenciais."));

  useEffect(() => { load(); }, []);

  const setField = (key, value) => setForm(prev => ({ ...prev, [key]: value }));
  const edit = (item) => {
    setEditing(item.alias);
    setForm({
      alias: item.alias,
      label: item.label || item.alias,
      url: item.url || "",
      email: item.email || "",
      password: "",
      aliases: (item.aliases || []).join(", "),
    });
    setMessage("");
  };
  const reset = () => { setEditing(null); setForm(emptyForm); setMessage(""); };
  const save = async (e) => {
    e.preventDefault();
    if (!form.alias.trim()) { setMessage("Informe um apelido."); return; }
    setSaving(true);
    setMessage("");
    try {
      await api.saveCredential(form);
      await load();
      setEditing(null);
      setForm(emptyForm);
      setMessage("Credencial salva.");
    } catch {
      setMessage("Nao consegui salvar a credencial.");
    } finally {
      setSaving(false);
    }
  };
  const remove = async (alias) => {
    if (!confirm(`Apagar credencial "${alias}"?`)) return;
    await api.deleteCredential(alias);
    if (editing === alias) reset();
    load();
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Credenciais</h2>
        <span className="page-sub">{items.length} salvas</span>
      </div>
      <div className="credentials-layout">
        <form className="credential-form" onSubmit={save}>
          <div className="form-grid">
            <label className="field">
              <span>Apelido</span>
              <input value={form.alias} onChange={e=>setField("alias", e.target.value)}
                     placeholder="iob, hublbcc, dominio.com" disabled={!!editing} />
            </label>
            <label className="field">
              <span>Nome</span>
              <input value={form.label} onChange={e=>setField("label", e.target.value)}
                     placeholder="IOB Online" />
            </label>
            <label className="field wide">
              <span>URL</span>
              <input value={form.url} onChange={e=>setField("url", e.target.value)}
                     placeholder="https://..." />
            </label>
            <label className="field">
              <span>Email ou usuario</span>
              <input value={form.email} onChange={e=>setField("email", e.target.value)}
                     autoComplete="username" placeholder="usuario@empresa.com" />
            </label>
            <label className="field">
              <span>Senha</span>
              <input type="password" value={form.password}
                     onChange={e=>setField("password", e.target.value)}
                     autoComplete="new-password"
                     placeholder={editing ? "manter senha atual" : "senha"} />
            </label>
            <label className="field wide">
              <span>Outros nomes</span>
              <input value={form.aliases} onChange={e=>setField("aliases", e.target.value)}
                     placeholder="folha, portal, login" />
            </label>
          </div>
          <div className="credential-actions">
            <button className="btn-approve" type="submit" disabled={saving}>
              {saving ? "Salvando..." : editing ? "Atualizar" : "Salvar"}
            </button>
            {editing && <button className="btn-cancel" type="button" onClick={reset}>Cancelar</button>}
            {message && <span className="credential-message">{message}</span>}
          </div>
          {path && <div className="credential-path">{path}</div>}
        </form>

        <div className="credential-list">
          {items.length===0 && <div className="empty">Nenhuma credencial salva.</div>}
          {items.map(item => (
            <div key={item.alias} className="credential-item">
              <div className="credential-main" onClick={()=>edit(item)}>
                <div className="credential-title">{item.label || item.alias}</div>
                <div className="credential-meta">{item.alias} {item.url ? `- ${item.url}` : ""}</div>
                <div className="credential-meta">{item.email || "sem usuario"} - {item.password_set ? "senha salva" : "sem senha"}</div>
              </div>
              <div className="credential-actions-row">
                <button className="btn-sm" onClick={()=>edit(item)}>Editar</button>
                <button className="btn-sm danger" onClick={()=>remove(item.alias)}>Apagar</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

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
  const [teaching, setTeaching]     = useState({active:false});
  const [approvalPending, setApprovalPending] = useState(false);
  const [approvalMsg, setApprovalMsg]         = useState("");
  const [sidebarOpen, setSidebarOpen]         = useState(true);
  const [tabs, setTabs]           = useState([]);
  const [lastScreenshot, setLastScreenshot]   = useState(null);
  const [input, setInput]         = useState("");
  const bottomRef   = useRef(null);
  const textareaRef = useRef(null);

  const onEvent = useCallback((ev) => {
    const { type } = ev;
    if (type === "pong") return;
    if (type === "tabs") { setTabs(ev.tabs || []); return; }
    if (type === "screenshot") {
      setLastScreenshot(ev.b64);
      setEvents(prev => [...prev, ev]);
      return;
    }
    if (type === "teach_status") {
      setTeaching(ev.teaching || {active:false});
      setEvents(prev => [...prev, {type:"system", text: ev.text || "Status do modo ensinar atualizado."}]);
      return;
    }
    if (["action","result","system","retry","paused","resumed","download","context"].includes(type)) {
      setEvents(prev => [...prev, ev]);
      if (type === "paused") setPaused(true);
      if (type === "resumed") setPaused(false);
      return;
    }
    if (type === "step_waiting") { setStepWaiting(true); setEvents(prev => [...prev, ev]); return; }
    if (type === "exec_start") return;
    if (type === "exec_end") return;
    if (type === "ask") {
      setApprovalPending(true); setApprovalMsg(ev.text);
      setEvents(prev => [...prev, ev]); setBusy(false); return;
    }
    if (["done","error","stopped","timeout"].includes(type)) {
      setEvents(prev => [...prev, ev]);
      setBusy(false); setStepWaiting(false);
      if (type === "done") setLlmHistory(prev => [...prev, {role:"assistant",content:ev.text||""}]);
      return;
    }
  }, []);

  const { connected, send } = useSocket(onEvent);

  useEffect(() => {
    api.listConvs().then(setConvs).catch(()=>{});
    const poll = () => api.status().then(s => {
      setManualMode(s.manual_mode||false);
      setTeaching(s.teaching || {active:false});
      setPaused(s.paused||false);
      setApprovalPending(s.approval_pending||false);
      setApprovalMsg(s.approval_message||"");
      setTabs(s.tabs||[]);
    }).catch(()=>{});
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({behavior:"smooth"}); }, [events, busy]);
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [input]);

  const newConv = async () => {
    const c = await api.createConv("Nova conversa");
    setConvs(prev => [c, ...prev]);
    setActiveConv(c); setEvents([]); setLlmHistory([]); setPage("chat");
  };

  const openConv = async (c) => {
    setActiveConv(c); setPage("chat");
    const msgs = await api.getMessages(c.id).catch(()=>[]);
    setEvents(msgs.map(m => ({type:m.role==="user"?"user":"done",text:m.content})));
    setLlmHistory(msgs.filter(m=>["user","assistant"].includes(m.role))
                      .map(m=>({role:m.role,content:m.content})));
  };

  const sendMsg = async (text) => {
    text = text.trim();
    if (!text || busy) return;
    let conv = activeConv;
    if (!conv) {
      conv = await api.createConv(text.substring(0,60));
      setConvs(prev => [conv,...prev]);
      setActiveConv(conv);
    }
    setEvents(prev => [...prev, {type:"user",text}]);
    const h = [...llmHistory, {role:"user",content:text}];
    setLlmHistory(h); setBusy(true); setInput("");
    setApprovalPending(false); setStepWaiting(false);
    send({type:"chat",text,conv_id:conv.id,history:h});
  };

  const onKey = (e) => { if (e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMsg(input);} };

  const doStop    = () => { send({type:"stop"}); setBusy(false); };
  const doPause   = () => send({type:paused?"resume":"pause"});
  const doApprove = () => { setApprovalPending(false); setBusy(true); send({type:"approve"}); };
  const doReject  = () => { setApprovalPending(false); send({type:"reject"}); };
  const doNextStep = () => { setStepWaiting(false); send({type:"next_step"}); };
  const toggleStep = () => { const n=!stepMode; setStepMode(n); send({type:n?"step_mode_on":"step_mode_off"}); };
  const toggleManual = () => { send({type:manualMode?"manual_off":"manual_on"}); setManualMode(v=>!v); };
  const startTeach = async () => {
    const stamp = new Date().toISOString().replace(/[-:T.]/g, "").slice(0, 14);
    const name = `procedimento_${stamp}`;
    try {
      const result = await api.teachStart({ name, description: `Procedimento ensinado: ${name}` });
      setTeaching({active:true, name:result.name || name, steps_count:0});
      setEvents(prev => [...prev, {type:"system", text:`Modo ensinar ativo: ${result.name || name}`}]);
    } catch {
      send({type:"teach_start", name, description:`Procedimento ensinado: ${name}`});
    }
  };
  const stopTeach = async () => {
    try {
      const result = await api.teachStop();
      const proc = result.procedure || {};
      setTeaching({active:false});
      setEvents(prev => [...prev, {type:"system", text: result.ok ? `Procedimento salvo: ${proc.name}` : (result.error || "Modo ensinar encerrado.")}]);
    } catch {
      send({type:"teach_stop"});
    }
  };
  const switchTab = (i) => send({type:"switch_tab",index:i});
  const closeTab  = (i) => send({type:"close_tab",index:i});
  const newTab    = () => send({type:"new_tab",url:""});

  const NAV = [
    {id:"chat",label:"💬 Chat"},
    {id:"procedures",label:"📋 Procedimentos"},
    {id:"executions",label:"⚡ Execuções"},
    {id:"media",label:"🖼️ Mídia"},
    {id:"files",label:"📁 Arquivos"},
    {id:"credentials",label:"Credenciais"},
  ];

  return (
    <div className="root">
      <aside className={`sidebar${sidebarOpen?"":" collapsed"}`}>
        <div className="sidebar-logo">
          <div className="logo-icon">⚡</div>
          <div>
            <div className="logo-name">LBCC Agent</div>
            <div className="logo-sub">v3.0</div>
          </div>
        </div>
        <button className="sidebar-new" onClick={newConv}><Icon.Plus /> Nova conversa</button>
        <div className="sidebar-section">Navegação</div>
        {NAV.map(n => (
          <div key={n.id}
               className={`conv-item${page===n.id&&!activeConv?" active":""}`}
               onClick={()=>{setPage(n.id);if(n.id!=="chat")setActiveConv(null);}}>
            {n.label}
          </div>
        ))}
        <div className="sidebar-section">Conversas</div>
        <div className="conv-list">
          {convs.map(c => (
            <div key={c.id}
                 className={`conv-item${activeConv?.id===c.id?" active":""}`}
                 onClick={()=>openConv(c)}>
              <Icon.Msg /> {c.title}
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          <div className={`status-dot${connected?"":" off"}`} />
          {connected?"Conectado":"Reconectando..."}
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <button className="icon-btn" onClick={()=>setSidebarOpen(v=>!v)}><Icon.Menu /></button>
          <div style={{flex:1,minWidth:0}}>
            <div className="topbar-title">
              {page==="procedures"?"Procedimentos":page==="executions"?"Execuções":
               page==="media"?"Mídia":page==="files"?"Arquivos":activeConv?.title||"LBCC Agent"}
            </div>
            {tabs.length > 0 && page==="chat" && (
              <div className="topbar-url">
                {tabs.find(t=>t.active)?.url||""}
              </div>
            )}
          </div>
          {lastScreenshot && page==="chat" && (
            <div className="live-thumb"
                 onClick={()=>window.open(`data:image/jpeg;base64,${lastScreenshot}`,"_blank")}>
              <img src={`data:image/jpeg;base64,${lastScreenshot}`} alt="live" />
              <span className="live-label">AO VIVO</span>
            </div>
          )}
          {page==="chat" && (
            <>
              <button className="icon-btn" onClick={()=>send({type:"screenshot"})}><Icon.Camera /></button>
              <button className={`ctrl-btn${stepMode?" manual-active":""}`} onClick={toggleStep}>🔢</button>
              {busy && <button className="ctrl-btn" onClick={doPause}>{paused?"▶":"⏸"}</button>}
              <button className={`ctrl-btn${teaching.active?" manual-active":""}`}
                      onClick={teaching.active ? stopTeach : startTeach}>
                {teaching.active ? "Parar ensino" : "Ensinar"}
              </button>
              <button className={`ctrl-btn${manualMode?" manual-active":""}`} onClick={toggleManual}>
                {manualMode?"🖐 Manual":"🤖 Auto"}
              </button>
              {busy && <button className="stop-btn" onClick={doStop}>⏹ PARAR</button>}
            </>
          )}
        </div>

        {/* Barra de abas — visível no chat */}
        {page==="chat" && tabs.length > 0 && (
          <TabBar tabs={tabs} onSwitch={switchTab} onClose={closeTab} onNew={newTab} send={send} />
        )}

        {page==="procedures" ? <ProceduresPage onRun={t=>{setPage("chat");sendMsg(t);}} /> :
         page==="executions" ? <ExecutionsPage /> :
         page==="media"      ? <MediaPage /> :
         page==="files"      ? <FilesPage /> :
         page==="credentials"? <CredentialsPage /> :
         <>
           <div className="messages">
             {events.length===0 && !busy && (
               <div className="welcome">
                 <div className="welcome-icon">⚡</div>
                 <h2>LBCC Agent v3</h2>
                 <p>Estação de trabalho persistente. Sessões, abas e downloads sempre disponíveis.</p>
                 <div className="quick-btns">
                   {QUICK.map(q=>(
                     <button key={q} className="quick-btn" onClick={()=>sendMsg(q)}>{q}</button>
                   ))}
                 </div>
               </div>
             )}
             {events.map((ev,i) => (
               <Bubble key={i} event={ev} onApprove={doApprove} onCancel={doReject} />
             ))}
             {stepWaiting && (
               <div className="step-banner">
                 <span>⏭ Aguardando próximo passo</span>
                 <button className="btn-approve" onClick={doNextStep}>Próximo →</button>
               </div>
             )}
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
           <div className="input-area">
             {manualMode && <div className="manual-banner">🖐 Modo manual — você controla o navegador.</div>}
             {paused && <div className="manual-banner" style={{color:"#60a5fa"}}>⏸ Pausado — clique ▶ para retomar.</div>}
             <div className="input-wrap">
               <textarea ref={textareaRef} value={input}
                         onChange={e=>setInput(e.target.value)} onKeyDown={onKey}
                         placeholder='"Abra o Google" · "nova aba" · "listar abas" · "trocar para aba 1"'
                         rows={1} disabled={busy||manualMode} />
               <button className="send-btn" onClick={()=>sendMsg(input)}
                       disabled={!input.trim()||busy||manualMode}>
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
