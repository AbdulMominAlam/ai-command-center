import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ApiError } from "./api";
import { App } from "./App";
import "./index.css";

// A plain 401 (no reconnect_url) means the session cookie is gone: re-check /me,
// which switches the app to the "Connect Google" screen.
const onError = (error: Error) => {
  if (error instanceof ApiError && error.status === 401 && !error.reconnect) {
    queryClient.invalidateQueries({ queryKey: ["me"] });
  }
};

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Don't retry auth or validation errors, only flaky network/server ones.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
