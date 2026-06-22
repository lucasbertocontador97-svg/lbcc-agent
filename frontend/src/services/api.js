const base = "";   // mesmo host — proxy do Vite em dev, direto em prod

async function get(path) {
  const r = await fetch(base + path);
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(base + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

export const api = {
  status:           ()          => get("/api/status"),
  listConvs:        ()          => get("/api/conversations"),
  createConv:       (title)     => post("/api/conversations", { title }),
  getMessages:      (cid)       => get(`/api/conversations/${cid}/messages`),
  listFiles:        ()          => get("/api/files"),
  listProcedures:   ()          => get("/api/procedures"),
};
