import { useMemo, useState } from "react";
import { browse, type BrowseResponse } from "../api";
import type { BuiltTopology } from "../topology";

// State lives at App level so the last-visited page survives switching to the
// Chat tab and back (the demo flow is: browse -> ask the LLM to block -> browse
// again), same pattern as the chat conversation.
export interface BrowserTabState {
  node: string;   // which lab PC is "running" the browser
  url: string;    // address-bar content
  result: BrowseResponse | null;
  error: string | null; // request-level failure (backend down, invalid target)
}

export const initialBrowserState: BrowserTabState = {
  node: "",
  url: "http://example.com",
  result: null,
  error: null,
};

interface Props {
  topology: BuiltTopology | null;
  labReady: boolean;
  state: BrowserTabState;
  setState: (s: BrowserTabState) => void;
}

// The /webbrowser surface (supervisor request 2026-08-25): a browser-styled
// page whose fetches REALLY run inside the selected lab PC (backend POST
// /browse -> docker exec wget), so the request crosses the firewalled data
// path and an active DROP rule visibly kills the page load. Nothing here is
// simulated — the footer shows the exact command that ran and its exit code.
export default function BrowserView({ topology, labReady, state, setState }: Props) {
  const [loading, setLoading] = useState(false);

  const pcs = useMemo(
    () => (topology ? topology.deviceNodes.filter((n) => n.data.role === "pc").map((n) => n.id) : []),
    [topology]
  );

  // Browsable sites: REAL public-internet sites (the lab routes to the
  // internet through the firewalls since 2026-08-25), plus every lab node
  // serving HTTP :80 and any scenario aliases.
  const sites = useMemo(() => {
    if (!topology) return [];
    const out: { url: string; label: string }[] = [
      { url: "http://example.com", label: "example.com" },
      { url: "http://neverssl.com", label: "neverssl.com" },
    ];
    for (const n of topology.deviceNodes) {
      for (const a of n.data.aliases) out.push({ url: `http://${a}`, label: a });
      if (n.data.ports.includes(80)) out.push({ url: `http://${n.id}`, label: n.id });
    }
    return out;
  }, [topology]);

  const node = state.node || pcs[0] || "";

  async function go(url?: string) {
    const target = (url ?? state.url).trim();
    if (!target || !node || loading) return;
    setLoading(true);
    setState({ ...state, node, url: target, result: null, error: null });
    try {
      const result = await browse(node, target);
      setState({ ...state, node, url: target, result, error: null });
    } catch (e) {
      setState({ ...state, node, url: target, result: null, error: String(e) });
    } finally {
      setLoading(false);
    }
  }

  const r = state.result;
  const host = (() => {
    try {
      return new URL(state.url.includes("://") ? state.url : `http://${state.url}`).hostname;
    } catch {
      return state.url;
    }
  })();

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-gray-100">
      {/* Browser chrome */}
      <div className="border-b border-gray-200 bg-gray-50 px-3 py-2 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 whitespace-nowrap">Browsing from</span>
          <select
            value={node}
            onChange={(e) => setState({ ...state, node: e.target.value })}
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white"
            title="The lab PC this browser runs inside"
          >
            {pcs.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>

          <form
            className="flex-1 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              go();
            }}
          >
            <input
              value={state.url}
              onChange={(e) => setState({ ...state, url: e.target.value })}
              placeholder="http://example.com"
              spellCheck={false}
              className="flex-1 font-mono text-xs border border-gray-300 rounded-full px-4 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
            <button
              type="submit"
              disabled={loading || !labReady}
              className="bg-blue-600 text-white px-4 py-1.5 rounded-full text-xs font-medium disabled:opacity-40 hover:bg-blue-700 transition-colors"
            >
              {loading ? "Loading…" : "Go"}
            </button>
          </form>
        </div>

        {sites.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs text-gray-400">Sites:</span>
            {sites.map((s) => (
              <button
                key={s.url}
                onClick={() => go(s.url)}
                disabled={loading || !labReady}
                className="text-xs font-mono px-2 py-0.5 bg-white border border-gray-200 rounded-full text-blue-700 hover:border-blue-300 disabled:opacity-40 transition-colors"
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Page area */}
      <div className="flex-1 min-h-0 flex flex-col">
        {!labReady ? (
          <div className="flex-1 flex items-center justify-center text-xs text-gray-400">
            Start a lab first — the browser runs inside a lab PC.
          </div>
        ) : loading ? (
          <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
            Loading {host} from {node}…
          </div>
        ) : r && r.ok && r.html !== null ? (
          <iframe
            title="lab-page"
            sandbox=""
            srcDoc={r.html}
            className="flex-1 w-full bg-white"
          />
        ) : r && !r.ok ? (
          <div className="flex-1 flex items-center justify-center bg-white">
            <div className="max-w-md text-center space-y-3 px-6">
              <div className="text-5xl text-gray-300">&#9888;</div>
              <div className="text-lg font-semibold text-gray-700">This site can&rsquo;t be reached</div>
              <div className="text-sm text-gray-500">
                <span className="font-mono">{host}</span> took too long to respond or refused the connection.
              </div>
              <div className="text-xs text-gray-400">
                The request really ran inside <span className="font-mono">{r.node}</span> and was
                dropped on the network path — this is what an active firewall block looks like.
              </div>
              <div className="text-xs font-mono text-gray-400 bg-gray-50 border border-gray-200 rounded p-2 text-left">
                {r.error}
              </div>
              <button
                onClick={() => go()}
                className="text-xs bg-gray-100 border border-gray-300 rounded px-3 py-1.5 text-gray-600 hover:bg-gray-200 transition-colors"
              >
                Try again
              </button>
            </div>
          </div>
        ) : state.error ? (
          <div className="flex-1 flex items-center justify-center bg-white">
            <div className="max-w-md text-center space-y-2 px-6">
              <div className="text-sm font-semibold text-gray-700">Cannot open this address</div>
              <div className="text-xs font-mono text-gray-500 bg-gray-50 border border-gray-200 rounded p-2 text-left whitespace-pre-wrap">
                {state.error}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center space-y-2">
              <div className="text-sm text-gray-400">Enter an address, or pick a site above.</div>
              <div className="text-xs text-gray-300">
                Every page load runs for real inside the selected lab PC.
              </div>
            </div>
          </div>
        )}

        {/* Authenticity footer: the exact command that ran inside the container */}
        {r && (
          <div className="border-t border-gray-200 bg-gray-50 px-3 py-1 text-xs font-mono text-gray-400 truncate">
            {r.container}$ {r.command} &rarr; exit {r.exit} &middot; {r.elapsed_s}s
          </div>
        )}
      </div>
    </div>
  );
}
