import { Navigate, Route, Routes } from "react-router-dom";

import { ObservabilityLayout } from "./ObservabilityLayout";
import { PerformanceTab } from "./PerformanceTab";

export { HealthPanel } from "./HealthPanel";

export function ObservabilityRoutes() {
  return (
    <Routes>
      <Route element={<ObservabilityLayout />}>
        <Route index element={<Navigate to="performance" replace />} />
        <Route path="performance" element={<PerformanceTab />} />
      </Route>
    </Routes>
  );
}
