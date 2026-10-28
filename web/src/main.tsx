import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { router } from "./router";
import { SettingsProvider } from "./settings/SettingsDialog";
import "./theme.css";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <RouterProvider router={router} context={{ queryClient }} />
      </SettingsProvider>
    </QueryClientProvider>
  </StrictMode>,
);
