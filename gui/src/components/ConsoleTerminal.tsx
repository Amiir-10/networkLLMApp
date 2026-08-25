import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { wsConsoleUrl } from "../api";

interface Props {
  scenario: string;
  node: string;
  // Typed + run automatically once the shell is up (e.g. firewalls show their
  // live rule set on open — supervisor request 2026-08-25). Sent through the
  // same input path as keystrokes, so what the viewer sees is a real command
  // actually executing in the container.
  autoCommand?: string;
}

type Status = "connecting" | "open" | "closed";

// A live PTY shell into a lab container, rendered with xterm.js and bridged to
// the backend GET /ws/console/{scenario}/{node} WebSocket. Everything typed here
// runs for real inside the container (ip, ping, firewall-cmd, vim, …). Remounts
// (via a React key on {scenario}/{node}) tear the old ws/term down cleanly.
export default function ConsoleTerminal({ scenario, node, autoCommand }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<Status>("connecting");

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const term = new Terminal({
      cursorBlink: true,
      // Track the root font-size so the terminal scales with the screen
      // like the rest of the UI (see index.css html { font-size: clamp(...) }).
      fontSize: Math.round(
        parseFloat(getComputedStyle(document.documentElement).fontSize) * 0.85
      ),
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

    let autoTimer: number | undefined;
    ws.onopen = () => {
      setStatus("open");
      sendResize();
      term.focus();
      if (autoCommand) {
        // Small delay so the shell prompt has rendered first — the viewer sees
        // the command being typed at a prompt, then its real output.
        autoTimer = window.setTimeout(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "input", data: `${autoCommand}\n` }));
          }
        }, 600);
      }
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
      if (autoTimer !== undefined) window.clearTimeout(autoTimer);
      ws.close();
      term.dispose();
    };
  }, [scenario, node, autoCommand]);

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-2 py-1 text-xs border-b border-gray-200 bg-gray-50">
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
