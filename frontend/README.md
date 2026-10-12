# Frontend

React + TypeScript frontend for the Universal AI Testing Platform.

## Structure

```
frontend/
├── public/                 Static assets served as-is
├── src/
│   ├── assets/             Images, icons, fonts
│   ├── components/         Reusable UI components (pure manual CSS)
│   │   ├── ui/             Primitive building blocks (Button, Badge, Card …)
│   │   └── layout/         App shell, sidebar, header, page wrapper
│   ├── hooks/              Custom React hooks (useApi, useToast …)
│   ├── lib/                api-client.ts, query-client.ts
│   ├── pages/              Page-level components (one per route)
│   │   ├── NotFound.tsx
│   │   └── Dashboard.tsx   (placeholder — P4)
│   ├── styles/             Manual CSS files
│   │   ├── reset.css       Minimal CSS reset
│   │   ├── variables.css   Design tokens (colours, spacing, radius …)
│   │   ├── base.css        Typography and base element styles
│   │   └── index.css       Root import that pulls everything together
│   ├── types/              Shared TypeScript types + generated API types
│   │   └── generated/      AUTO-GENERATED — do not edit by hand (ADR-010)
│   ├── App.tsx             App shell with router
│   └── main.tsx            Entry point
├── index.html
├── package.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
└── .eslintrc.cjs
```

## Quick start

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run type-check
npm run lint
npm run build
```

The Vite dev server proxies `/api/*` and `/health` to the FastAPI
backend at `http://localhost:8000`.

## CSS approach (ADR-012)

All styles use **plain CSS** with CSS custom properties (variables) and **CSS Modules**.
No Tailwind, no CSS-in-JS, no external utility frameworks (per ADR-012).

- `src/styles/variables.css` defines all design tokens.
- Components use **CSS Modules** (`Component.module.css`) for scoped styles.
- Global element styles are in `src/styles/base.css`.
- Architecture decision: see `docs/DECISIONS.md` (ADR-012).
