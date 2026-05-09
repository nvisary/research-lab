import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import { Layout } from "./Layout";
import { StrategyList } from "./pages/StrategyList";
import { StrategyDetail } from "./pages/StrategyDetail";
import { Portfolio } from "./pages/Portfolio";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<StrategyList />} />
          <Route path="/strategies/:name" element={<StrategyDetail />} />
          <Route path="/portfolio" element={<Portfolio />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
