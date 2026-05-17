import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import { Layout } from "./Layout";
import { StrategyList } from "./pages/StrategyList";
import { StrategyDetail } from "./pages/StrategyDetail";
import { Portfolio } from "./pages/Portfolio";
import { MultiStrat } from "./pages/MultiStrat";
import { Features } from "./pages/Features";
import { Forward } from "./pages/Forward";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<StrategyList />} />
          <Route path="/strategies/:name" element={<StrategyDetail />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/multistrat" element={<MultiStrat />} />
          <Route path="/features" element={<Features />} />
          <Route path="/forward" element={<Forward />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
