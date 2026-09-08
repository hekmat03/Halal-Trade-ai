# HalalTrade AI — Frontend (placeholder)

The dashboard is a **LATER delivery** and is intentionally NOT built yet.

This project's Delivery 1 is the backend foundation (the four policy gates + the
pipeline, fully tested). The frontend will be a minimal Next.js 14+ / TypeScript /
Tailwind / shadcn-ui dashboard covering:

- Paper / Live trading indicator (Live is DISABLED by default)
- Emergency kill switch
- Audit log viewer
- Risk-limit configuration

**Planned structure** (for the future delivery):

```
frontend/
├── app/            # Next.js app router pages
├── components/     # shadcn/ui components
├── lib/            # API client for the FastAPI backend
└── tailwind/       # styling
```

Nothing here yet — this file exists so the scaffold is explicit about scope.
