/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND_ORIGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** App version injected at build time from package.json (vite.config define). */
declare const __APP_VERSION__: string;
