import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import LandingPage from "./components/LandingPage";
import ReviewUI    from "./components/ReviewUI";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/"    element={<LandingPage />} />
        <Route path="/app" element={<ReviewUI />} />
        {/* catch-all: unknown paths → landing */}
        <Route path="*"    element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
