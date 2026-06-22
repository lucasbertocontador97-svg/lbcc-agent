import { Icon } from "./Icons.jsx";

const ACTION_LABELS = {
  navigate:      ["act-navigate", "navegar"],
  click:         ["act-click",    "clicar"],
  fill:          ["act-fill",     "preencher"],
  wait:          ["act-wait",     "aguardar"],
  key:           ["act-other",    "tecla"],
  scroll:        ["act-other",    "scroll"],
  wait_selector: ["act-other",    "aguardar elemento"],
  select:        ["act-fill",     "selecionar"],
  hover:         ["act-other",    "hover"],
  upload:        ["act-other",    "upload"],
  screenshot:    ["act-other",    "screenshot"],
};

export function Bubble({ event, onApprove, onCancel }) {
  const { type } = event;

  if (type === "user") return (
    <div className="msg-row user">
      <div className="avatar user">👤</div>
      <div className="bubble user">{event.text}</div>
    </div>
  );

  if (type === "done") return (
    <div className="msg-row">
      <div className="avatar bot">🤖</div>
      <div className="bubble bot">{event.text}</div>
    </div>
  );

  if (["error","stopped","timeout"].includes(type)) {
    const icons = { error:"❌", stopped:"⏹", timeout:"⏱" };
    return (
      <div className="msg-row">
        <div className="avatar bot">🤖</div>
        <div className="bubble bot" style={{ borderColor:"rgba(239,68,68,.3)", color:"#f87171" }}>
          {icons[type]} {event.text}
        </div>
      </div>
    );
  }

  if (type === "retry") return (
    <div className="msg-row">
      <div style={{ width:32, flexShrink:0 }} />
      <div className="bubble-result" style={{ color:"var(--amber)" }}>🔄 {event.text}</div>
    </div>
  );

  if (type === "paused") return (
    <div className="msg-row">
      <div style={{ width:32, flexShrink:0 }} />
      <div className="bubble-result" style={{ color:"#60a5fa" }}>⏸ {event.text}</div>
    </div>
  );

  if (type === "resumed") return (
    <div className="msg-row">
      <div style={{ width:32, flexShrink:0 }} />
      <div className="bubble-result" style={{ color:"var(--green)" }}>▶ {event.text}</div>
    </div>
  );

  if (type === "step_waiting") return (
    <div className="msg-row">
      <div style={{ width:32, flexShrink:0 }} />
      <div className="bubble-result" style={{ color:"#a78bfa" }}>🔢 {event.text}</div>
    </div>
  );

  if (type === "action") {
    const [cls, label] = ACTION_LABELS[event.action] || ["act-other", event.action];
    const detail = event.url || event.selector || (event.value ? `"${event.value}"` : "") || event.key || "";
    return (
      <div className="msg-row">
        <div className="avatar bot" style={{ opacity:.5 }}>🤖</div>
        <div className="bubble-action">
          <span className={`act-label ${cls}`}>{label}</span>
          <span style={{ wordBreak:"break-all" }}>{detail}</span>
        </div>
      </div>
    );
  }

  if (type === "result") {
    const ok = event.ok !== false;
    return (
      <div className="msg-row">
        <div style={{ width:32, flexShrink:0 }} />
        <div className={`bubble-result ${ok?"ok":"err"}`}>
          {ok ? <Icon.Check /> : <Icon.X />}
          {ok
            ? (event.title ? `${event.title} — ${event.url||""}` : "ok")
            : event.error?.substring(0,120)
          }
        </div>
      </div>
    );
  }

  if (type === "screenshot") {
    const src = `data:image/jpeg;base64,${event.b64}`;
    return (
      <div className="msg-row">
        <div className="avatar bot" style={{ opacity:.5 }}>🤖</div>
        <div>
          {event.label && (
            <div style={{ fontSize:11, color:"var(--text3)", marginBottom:4, fontFamily:"var(--mono)" }}>
              📸 {event.label}
            </div>
          )}
          <div className="screenshot-wrap" onClick={() => window.open(src,"_blank")}>
            <img src={src} alt="screenshot" />
          </div>
        </div>
      </div>
    );
  }

  if (type === "system") return <div className="sys-msg">{event.text}</div>;

  return null;
}

export function TypingIndicator() {
  return (
    <div className="typing-row">
      <div className="avatar bot">🤖</div>
      <div className="typing-dots">
        <div className="dot"/><div className="dot"/><div className="dot"/>
      </div>
    </div>
  );
}
