import { useCallback, useEffect, useRef, useState } from "react";

export function useSocket(onEvent) {
  const [connected, setConnected] = useState(false);
  const ws = useRef(null);
  const timer = useRef(null);
  const sid = useRef(`s_${Date.now()}`);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    // Funciona tanto em localhost quanto no Replit (https → wss)
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/${sid.current}`;
    const socket = new WebSocket(url);
    ws.current = socket;

    socket.onopen = () => {
      setConnected(true);
      clearTimeout(timer.current);
    };
    socket.onmessage = (e) => {
      try { onEventRef.current(JSON.parse(e.data)); } catch {}
    };
    socket.onclose = () => {
      setConnected(false);
      timer.current = setTimeout(connect, 3000);
    };
    socket.onerror = () => socket.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(timer.current);
      ws.current?.close();
    };
  }, [connect]);

  const send = useCallback((data) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, send };
}
