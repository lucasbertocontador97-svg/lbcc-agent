const get  = p => fetch(p).then(r => { if(!r.ok) throw new Error(r.statusText); return r.json(); });
const post = (p, b) => fetch(p, {
  method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(b)
}).then(r => { if(!r.ok) throw new Error(r.statusText); return r.json(); });

export const api = {
  status:          ()    => get("/api/status"),
  listConvs:       ()    => get("/api/conversations"),
  createConv:      (t)   => post("/api/conversations", { title:t }),
  getMessages:     (cid) => get(`/api/conversations/${cid}/messages`),
  listExecutions:  ()    => get("/api/executions"),
  getExecution:    (id)  => get(`/api/executions/${id}`),
  getExecLogs:     (id)  => get(`/api/executions/${id}/logs`),
  listProcedures:  ()    => get("/api/procedures"),
  getProcedure:    (n)   => get(`/api/procedures/${n}`),
  listScreenshots: ()    => get("/api/screenshots"),
  listVideos:      ()    => get("/api/videos"),
  listFiles:       ()    => get("/api/files"),
  listAttachments: ()    => get("/api/attachments"),
};
