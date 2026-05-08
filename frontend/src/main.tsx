import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import { Layout } from "./Layout";
import { StrategyList } from "./pages/StrategyList";
import { StrategyDetail } from "./pages/StrategyDetail";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<StrategyList />} />
          <Route path="/strategies/:name" element={<StrategyDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
