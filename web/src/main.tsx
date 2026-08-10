import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles/index.css";
import "./styles/shell.css";
import "./styles/overview.css";
import "./styles/find.css";
import "./styles/detail.css";
import "./styles/curate.css";
import "./styles/console.css";
import { App } from "./App";

const el = document.getElementById("root");
if (!el) throw new Error("#root missing from index.html");

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
