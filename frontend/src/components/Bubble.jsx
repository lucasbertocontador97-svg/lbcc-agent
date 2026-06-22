import { Icon } from "./Icons.jsx";

const ACTION_LABELS = {
  navigate:   ["act-navigate", "navegar"],
  click:      ["act-click",    "clicar"],
  fill:       ["act-fill",     "preencher"],
  wait:       ["act-wait",     "aguardar"],
  screenshot: ["act-other",    "screenshot"],
};

export function Bubble({ event, onApprove, onCancel }) {
  const { type } = event;

  // Usuário
  if (type === "user") {
    return (
      <div className="msg-row user">
        <div className="avatar user">👤</div>
        <div className="bubble user">{event.text}</div>
      </div>
    );
  }

  // Resposta final do agente
  if (type === "done" || type === "error") {
    const isErr = type === "error";
    return (
      <div className="msg-row">
        <div className="avatar bot">🤖</div>
        <div className="bubble bot" style={isErr ? { borderColor: "rgba(239,68,68,.3)", color: "#f87171" } : {}}>
          {event.text}
        </div>
      </div>
    );
  }

  // Ação do browser
  if (type === "action") {
    const [cls, label] = ACTION_LABELS[event.action] || ["act-other", event.action];
    const detail = event.url || event.selector || (event.value ? `"${event.value}"` : "");
    return (
      <div className="msg-row">
        <div className="avatar bot" style={{ opacity: .5 }}>🤖</div>
        <div className="bubble-action">
          <span className={`act-label ${cls}`}>{label}</span>
          <span style={{ wordBreak: "break-all" }}>{detail}</span>
        </div>
      </div>
    );
  }

  // Resultado da ação
  if (type === "result") {
    const ok = event.ok !== false;
    return (
      <div className="msg-row">
        <div style={{ width: 32, flexShrink: 0 }} />
        <div className={`bubble-result ${ok ? "ok" : "err"}`}>
          {ok ? <Icon.Check /> : <Icon.X />}
          {ok
            ? (event.title || event.url ? `${event.title || ""} — ${event.url || ""}`.trim() : "ok")
            : event.error?.substring(0, 120)
          }
        </div>
      </div>
    );
  }

  // Screenshot
  if (type === "screenshot") {
    const src = `data:image/jpeg;base64,${event.b64}`;
    return (
      <div className="msg-row">
        <div className="avatar bot" style={{ opacity: .5 }}>🤖</div>
        <div className="screenshot-wrap" onClick={() => window.open(src, "_blank")}>
          <img src={src} alt="screenshot" />
        </div>
      </div>
    );
  }

  // Pergunta / aprovação
  if (type === "ask") {
    return (
      <div className="msg-row">
        <div className="avatar bot">🤖</div>
        <div className="bubble-ask">
          <p>{event.text}</p>
          <div className="approve-btns">
            <button className="btn-approve" onClick={onApprove}>Sim, continuar</button>
            <button className="btn-cancel"  onClick={onCancel}>Cancelar</button>
          </div>
        </div>
      </div>
    );
  }

  // Sistema
  if (type === "system") {
    return <div className="sys-msg">{event.text}</div>;
  }

  return null;
}

export function TypingIndicator() {
  return (
    <div className="typing-row">
      <div className="avatar bot">🤖</div>
      <div className="typing-dots">
        <div className="dot" /><div className="dot" /><div className="dot" />
      </div>
    </div>
  );
}
