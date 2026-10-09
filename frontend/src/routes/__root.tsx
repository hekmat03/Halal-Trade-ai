import { HeadContent, Outlet, Scripts, createRootRoute } from "@tanstack/react-router";
import type { ReactNode } from "react";
import appCss from "~/styles/app.css?url";

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1, viewport-fit=cover" },
      { name: "theme-color", content: "#04060c" },
      { name: "color-scheme", content: "dark" },
      { title: "HalalTrade AI — Control Dashboard" },
      {
        name: "description",
        content:
          "Shariah-compliant BTC/USDT spot trading agent: AI recommends, policies decide, risk controls, you authorize. Paper and backtest only — live trading disabled.",
      },
    ],
    links: [
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap",
      },
      { rel: "stylesheet", href: appCss },
    ],
  }),
  notFoundComponent: () => (
    <div className="flex min-h-dvh items-center justify-center bg-slate-950 px-6 text-slate-300">
      <div className="glass gradient-border max-w-sm rounded-2xl p-8 text-center">
        <p className="text-lg font-semibold text-slate-100">Page not found</p>
        <p className="mt-2 text-sm text-slate-400">That route does not exist on this dashboard.</p>
      </div>
    </div>
  ),
  component: RootComponent,
});

function RootComponent() {
  return (
    <RootDocument>
      <Outlet />
    </RootDocument>
  );
}

function RootDocument({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <head>
        <HeadContent />
      </head>
      <body className="min-h-dvh bg-slate-950 text-slate-100 antialiased">
        {children}
        <Scripts />
      </body>
    </html>
  );
}
