/**
 * Auth store — bearer-token session backed by the backend (AUTH_CONTRACT.md).
 *
 * Credentials are verified server-side by POST /api/auth/login; this store only
 * holds the resulting token + identity. There are NO client-side credentials
 * (the old hardcoded user table was RB-02, now removed). The token itself lives
 * in api/token.ts so the REST/WS layers can read it without an import cycle.
 */
import { create } from 'zustand';
import { authApi, type ApiError } from '@/api/client';
import { getToken, setToken, setUnauthorizedHandler } from '@/api/token';

interface AuthState {
  isAuthenticated: boolean;
  username: string | null;
  role: string | null;
  loginError: string | null;
  loggingIn: boolean;
  /** null = unknown (not checked yet); true = first-run setup pending. */
  setupRequired: boolean | null;
  /** True for the rest of this session right after `setup()` succeeds —
   *  ModalLayer reads it once to trigger the first-run map-source screen.
   *  Deliberately in-memory only (not persisted): setup can succeed at most
   *  once per server, so losing this on reload just means the operator opens
   *  Settings → General instead, not a stuck or repeating prompt. */
  justCompletedSetup: boolean;

  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
  clearError: () => void;
  /** Query the backend for first-run setup status (login page, on mount). */
  checkSetup: () => Promise<void>;
  /** First-run setup: create the admin account, then sign in with its token. */
  setup: (username: string, password: string) => Promise<boolean>;
  /** Consume the justCompletedSetup flag once ModalLayer has acted on it. */
  clearJustCompletedSetup: () => void;
  /** Change the signed-in user's password. Resolves to an error message or null. */
  changePassword: (currentPassword: string, newPassword: string) => Promise<string | null>;
}

export const useAuthStore = create<AuthState>((set) => ({
  // Optimistically restore: if a token survived in sessionStorage we treat the
  // session as live. If it is stale, the first authenticated request returns
  // 401 and the unauthorized handler (registered below) tears it down.
  isAuthenticated: !!getToken(),
  username: null,
  role: null,
  loginError: null,
  loggingIn: false,
  setupRequired: null,
  justCompletedSetup: false,

  login: async (username, password) => {
    set({ loggingIn: true, loginError: null });
    try {
      const { access_token } = await authApi.login(username, password);
      setToken(access_token);
      // Resolve identity/role for the UI (non-fatal if it fails).
      let me: { username: string; role: string } | null = null;
      try {
        me = await authApi.me();
      } catch {
        /* identity is best-effort; token is already valid */
      }
      set({
        isAuthenticated: true,
        username: me?.username ?? username,
        role: me?.role ?? null,
        loginError: null,
        loggingIn: false,
      });
      return true;
    } catch (err) {
      const e = err as ApiError;
      const message =
        e.status === 429
          ? 'Too many attempts. Please wait a minute and try again.'
          : e.status === 0
            ? 'Cannot reach the server. Check your connection.'
            : 'Incorrect username or password.';
      setToken(null);
      set({ isAuthenticated: false, username: null, role: null, loginError: message, loggingIn: false });
      return false;
    }
  },

  logout: () => {
    setToken(null);
    set({ isAuthenticated: false, username: null, role: null, loginError: null });
  },

  clearError: () => set({ loginError: null }),

  checkSetup: async () => {
    try {
      const { setup_required } = await authApi.setupStatus();
      set({ setupRequired: setup_required });
    } catch {
      // Backend unreachable or older version without /setup — show normal login.
      set({ setupRequired: false });
    }
  },

  setup: async (username, password) => {
    set({ loggingIn: true, loginError: null });
    try {
      const { access_token } = await authApi.setup(username, password);
      setToken(access_token);
      set({
        isAuthenticated: true,
        username,
        role: 'admin',
        loginError: null,
        loggingIn: false,
        setupRequired: false,
        justCompletedSetup: true,
      });
      return true;
    } catch (err) {
      const e = err as ApiError;
      const message =
        e.status === 409
          ? 'Setup was already completed. Please sign in instead.'
          : e.status === 0
            ? 'Cannot reach the server. Check your connection.'
            : e.message || 'Could not complete setup.';
      setToken(null);
      set({
        isAuthenticated: false,
        loginError: message,
        loggingIn: false,
        // A 409 means someone finished setup already — fall back to sign-in.
        setupRequired: e.status === 409 ? false : true,
      });
      return false;
    }
  },

  clearJustCompletedSetup: () => set({ justCompletedSetup: false }),

  changePassword: async (currentPassword, newPassword) => {
    try {
      await authApi.changePassword(currentPassword, newPassword);
      return null;
    } catch (err) {
      const e = err as ApiError;
      if (e.status === 401) return 'Current password is incorrect.';
      if (e.status === 0) return 'Cannot reach the server. Check your connection.';
      return e.message || 'Could not change the password.';
    }
  },
}));

// Wire the transport-layer 401/4401 signal to a session teardown. Registered
// once at module load; the REST client and WS layer call notifyUnauthorized().
setUnauthorizedHandler(() => {
  useAuthStore.getState().logout();
});
