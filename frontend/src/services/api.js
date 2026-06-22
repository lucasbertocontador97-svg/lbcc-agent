const get  = path => fetch(path).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
const post = (path, body) => fetch(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });

export const api = {
  status:          ()      => get("/api/status"),
  listConvs:       ()      => get("/api/conversations"),
  createConv:      (title) => post("/api/conversations", { title }),
  getMessages:     (cid)   => get(`/api/conversations/${cid}/messages`),
  listExecutions:  ()      => get("/api/executions"),
  getExecution:    (id)    => get(`/api/executions/${id}`),
  getExecLogs:     (id)    => get(`/api/executions/${id}/logs`),
  listScreenshots: ()      => get("/api/screenshots"),
  listVideos:      ()      => get("/api/videos"),
  listFiles:       ()      => get("/api/files"),
  listProcedures:  ()      => get("/api/procedures"),
};
