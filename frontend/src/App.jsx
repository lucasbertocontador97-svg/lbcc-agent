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

export default function App() {
  const [convs, setConvs]           = useState([]);
  const [activeConv, setActiveConv] = useState(null);
  const [events, setEvents]         = useState([]);     // itens exibidos no chat
  const [llmHistory, setLlmHistory] = useState([]);    // histórico para o LLM
  const [busy, setBusy]             = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [browserUrl, setBrowserUrl] = useState("");
  const [input, setInput]           = useState("");
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  // ── Socket ───────────────────────────────────────────────────────────────

  const onEvent = useCallback((ev) => {
    const { type } = ev;

    if (type === "pong") return;

    if (type === "action" || type === "result" || type === "screenshot"
        || type === "system") {
      setEvents(prev => [...prev, ev]);
      return;
    }

    if (type === "done" || type === "error") {
      setEvents(prev => [...prev, ev]);
      setBusy(false);
      setLlmHistory(prev => [
        ...prev,
        { role: "assistant", content: ev.text || "" }
      ]);
      return;
    }

    if (type === "ask") {
      setEvents(prev => [...prev, ev]);
      setBusy(false);
      return;
    }
  }, []);

  const { connected, send } = useSocket(onEvent);

  // ── Init ─────────────────────────────────────────────────────────────────

  useEffect(() => {
    api.listConvs().then(setConvs).catch(() => {});
    api.status().then(s => setBrowserUrl(s.browser_url || "")).catch(() => {});
    const t = setInterval(() => {
      api.status().then(s => setBrowserUrl(s.browser_url || "")).catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, []);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, busy]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [input]);

  // ── Conversations ─────────────────────────────────────────────────────────

  const newConv = async () => {
    const c = await api.createConv("Nova conversa");
    setConvs(prev => [c, ...prev]);
    setActiveConv(c);
    setEvents([]);
    setLlmHistory([]);
  };

  const openConv = async (c) => {
    setActiveConv(c);
    const msgs = await api.getMessages(c.id).catch(() => []);
    const evts = msgs.map(m => ({
      type: m.role === "user" ? "user" : "done",
      text: m.content,
    }));
    setEvents(evts);
    setLlmHistory(msgs
      .filter(m => m.role === "user" || m.role === "assistant")
      .map(m => ({ role: m.role, content: m.content }))
    );
  };

  // ── Send message ──────────────────────────────────────────────────────────

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
    const newHistory = [...llmHistory, { role: "user", content: text }];
    setLlmHistory(newHistory);
    setBusy(true);
    setInput("");

    send({
      type: "chat",
      text,
      conv_id: conv.id,
      history: newHistory,
    });
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMsg(input);
    }
  };

  const takeScreenshot = () => send({ type: "screenshot" });

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="root">

      {/* Sidebar */}
      <aside className={`sidebar${sidebarOpen ? "" : " collapsed"}`}>
        <div className="sidebar-logo">
          <div className="logo-icon">⚡</div>
          <div>
            <div className="logo-name">LBCC Agent</div>
            <div className="logo-sub">Agente Operacional</div>
          </div>
        </div>

        <button className="sidebar-new" onClick={newConv}>
          <Icon.Plus /> Nova conversa
        </button>

        <div className="sidebar-section">Conversas</div>

        <div className="conv-list">
          {convs.map(c => (
            <div
              key={c.id}
              className={`conv-item${activeConv?.id === c.id ? " active" : ""}`}
              onClick={() => openConv(c)}
            >
              <Icon.Msg />
              {c.title}
            </div>
          ))}
          {convs.length === 0 && (
            <div style={{ padding: "8px 10px", fontSize: 12, color: "var(--text3)" }}>
              Sem conversas ainda
            </div>
          )}
        </div>

        <div className="sidebar-footer">
          <div className={`status-dot${connected ? "" : " off"}`} />
          {connected ? "Conectado" : "Reconectando..."}
        </div>
      </aside>

      {/* Main */}
      <div className="main">

        {/* Top bar */}
        <div className="topbar">
          <button className="icon-btn" onClick={() => setSidebarOpen(v => !v)}>
            <Icon.Menu />
          </button>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="topbar-title">
              {activeConv?.title || "LBCC Agent"}
            </div>
            {browserUrl && (
              <div className="topbar-url">{browserUrl}</div>
            )}
          </div>
          <button className="icon-btn" title="Screenshot" onClick={takeScreenshot}>
            <Icon.Camera />
          </button>
        </div>

        {/* Messages */}
        <div className="messages">
          {events.length === 0 && !busy && (
            <div className="welcome">
              <div className="welcome-icon">⚡</div>
              <h2>LBCC Agent</h2>
              <p>
                Agente operacional digital para escritório contábil.<br />
                Digite um comando ou escolha um exemplo abaixo.
              </p>
              <div className="quick-btns">
                {QUICK.map(q => (
                  <button key={q} className="quick-btn" onClick={() => sendMsg(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {events.map((ev, i) => (
            <Bubble
              key={i}
              event={ev}
              onApprove={() => {
                // TODO: retomar tarefa após aprovação
                setEvents(prev => [...prev, {
                  type: "system", text: "Aprovado. Continuando..."
                }]);
              }}
              onCancel={() => {
                setBusy(false);
                setEvents(prev => [...prev, {
                  type: "system", text: "Tarefa cancelada."
                }]);
              }}
            />
          ))}

          {busy && <TypingIndicator />}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="input-area">
          <div className="input-wrap">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder='Ex: "Abra o Google" ou "Acesse o e-CAC"'
              rows={1}
              disabled={busy}
            />
            <button
              className="send-btn"
              onClick={() => sendMsg(input)}
              disabled={!input.trim() || busy}
            >
              <Icon.Send />
            </button>
          </div>
          <div className="input-hint">Enter para enviar · Shift+Enter para nova linha</div>
        </div>

      </div>
    </div>
  );
}
