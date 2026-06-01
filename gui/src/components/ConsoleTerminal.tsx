import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { wsConsoleUrl } from "../api";

interface Props {
  scenario: string;
  node: string;
}

type Status = "connecting" | "open" | "closed";

// A live PTY shell into a lab container, rendered with xterm.js and bridged to
// the backend GET /ws/console/{scenario}/{node} WebSocket. Everything typed here
// runs for real inside the container (ip, ping, firewall-cmd, vim, …). Remounts
// (via a React key on {scenario}/{node}) tear the old ws/term down cleanly.
export default function ConsoleTerminal({ scenario, node }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<Status>("connecting");

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'ui-monospace, "Cascadia Code", "Fira Code", Menlo, monospace',
      theme: { background: "#0b1020", foreground: "#e2e8f0" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    fit.fit();

    const ws = new WebSocket(wsConsoleUrl(scenario, node));

    const sendResize = () => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };

    ws.onopen = () => {
      setStatus("open");
      sendResize();
      term.focus();
    };
    ws.onmessage = (ev) => {
      term.write(typeof ev.data === "string" ? ev.data : "");
    };
    ws.onclose = () => setStatus("closed");
    ws.onerror = () => setStatus("closed");

    const dataSub = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    // Keep the container PTY's window size in sync with the rendered terminal.
    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
        sendResize();
      } catch {
        /* element not measurable yet */
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      dataSub.dispose();
      ws.close();
      term.dispose();
    };
  }, [scenario, node]);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-2 py-1 text-[11px] border-b border-gray-200 bg-gray-50">
        <span
          className={`w-2 h-2 rounded-full ${
            status === "open"
              ? "bg-green-500"
              : status === "connecting"
              ? "bg-amber-400"
              : "bg-red-500"
          }`}
        />
        <span className="text-gray-500">
          {status === "open"
            ? `live shell — ${node}`
            : status === "connecting"
            ? "connecting…"
            : "disconnected"}
        </span>
      </div>
      <div ref={containerRef} className="flex-1 min-h-0 bg-[#0b1020] p-1" />
    </div>
  );
}
