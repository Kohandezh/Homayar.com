import { Suspense } from "react";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toast } from "@heroui/react";
import { I18nProvider } from "@/lib/i18n";
import AuthProvider from "@/lib/AuthProvider";
import { useI18n } from "@/lib/i18n-context";
import { AppRoutes } from "@/AppRoutes";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10000 } },
});

const routerBasename = import.meta.env.BASE_URL.replace(/\/$/, "") || "/";

function RouteFallback() {
  const { lang } = useI18n();
  return (
    <div className="session-loading" role="status" aria-live="polite">
      <span className="session-loading-mark" aria-hidden="true">H</span>
      <span>{lang === "fa" ? "در حال بارگذاری" : "Loading"}</span>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter basename={routerBasename}>
          <AuthProvider>
            <Suspense fallback={<RouteFallback />}>
              <AppRoutes />
            </Suspense>
            {/* One region for every page's success toasts; it reads the
                language from the surrounding I18nProvider. */}
            <Toast.Provider />
          </AuthProvider>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
