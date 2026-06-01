import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Link, useLocation } from "react-router-dom";
import "./index.css";
import App from "./App";
import ConsolePage from "./pages/ConsolePage";

// Tiny floating toggle between the chat view (`/`) and the debug console
// (`/console`). Fixed + pointer-events-none wrapper so it never shifts App's
// full-screen layout or blocks clicks elsewhere.
function NavToggle() {
  const onConsole = useLocation().pathname.startsWith("/console");
  return (
    <div className="fixed top-2 left-1/2 -translate-x-1/2 z-50 pointer-events-none">
      <Link
        to={onConsole ? "/" : "/console"}
        className="pointer-events-auto text-[11px] px-2.5 py-1 rounded-full bg-gray-800 text-white shadow hover:bg-gray-700 transition-colors"
      >
        {onConsole ? "← Chat view" : "Console →"}
      </Link>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <NavToggle />
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/console" element={<ConsolePage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
