/**
 * LoginPage — full-viewport glassmorphism login screen.
 * Matches the NetGeo dark-glass design language: radial gradient background,
 * frosted card, Apple-blue accent. Credentials are validated by authStore.
 *
 * Two modes, decided by GET /api/auth/setup on mount:
 *   - setup: no account exists yet → "create admin password" form (one-time)
 *   - login: normal username + password sign-in
 */
import { useEffect, useState, useRef } from 'react';
import { Eye, EyeOff, Lock, LogIn, ShieldCheck, User } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { applyTheme } from '@/theme/tokens';
import { cn } from '@/lib/cn';

const MIN_PASSWORD_LENGTH = 8;

/** 24×24 "mirrored node" mark — brand set 8a, `netgeo-icon.svg` — inlined so
 * it inherits `currentColor` instead of shipping a second asset request
 * (same approach as `NativeTitleBar`'s `BrandGlyph`). */
function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" fillRule="evenodd" className={className} aria-hidden>
      <path d="M8.8 8.2 A3.2 3.2 0 1 0 15.2 8.2 A3.2 3.2 0 1 0 8.8 8.2 Z M10.8 8.2 A1.2 1.2 0 1 0 13.2 8.2 A1.2 1.2 0 1 0 10.8 8.2 Z M4.6 3.4 A1.8 1.8 0 1 0 8.2 3.4 A1.8 1.8 0 1 0 4.6 3.4 Z M15.8 3.4 A1.8 1.8 0 1 0 19.4 3.4 A1.8 1.8 0 1 0 15.8 3.4 Z M10.221 5.357 L8.418 3.812 L7.116 5.332 L8.919 6.877 Z M13.779 5.357 L15.582 3.812 L16.884 5.332 L15.081 6.877 Z M3.5 10.9 H7.5 V13.1 H3.5 Z M16.5 10.9 H20.5 V13.1 H16.5 Z M8.8 15.8 A3.2 3.2 0 1 0 15.2 15.8 A3.2 3.2 0 1 0 8.8 15.8 Z M10.8 15.8 A1.2 1.2 0 1 0 13.2 15.8 A1.2 1.2 0 1 0 10.8 15.8 Z M4.6 20.6 A1.8 1.8 0 1 0 8.2 20.6 A1.8 1.8 0 1 0 4.6 20.6 Z M15.8 20.6 A1.8 1.8 0 1 0 19.4 20.6 A1.8 1.8 0 1 0 15.8 20.6 Z M10.221 18.643 L8.418 20.188 L7.116 18.668 L8.919 17.123 Z M13.779 18.643 L15.582 20.188 L16.884 18.668 L15.081 17.123 Z" />
    </svg>
  );
}

export function LoginPage() {
  const login = useAuthStore((s) => s.login);
  const setup = useAuthStore((s) => s.setup);
  const checkSetup = useAuthStore((s) => s.checkSetup);
  const setupRequired = useAuthStore((s) => s.setupRequired);
  const loginError = useAuthStore((s) => s.loginError);
  const clearError = useAuthStore((s) => s.clearError);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);

  const isSetup = setupRequired === true;

  // Always render in dark mode on the login screen.
  useEffect(() => {
    applyTheme('dark');
    checkSetup();
  }, [checkSetup]);

  // Focus + prefill once the mode is known.
  useEffect(() => {
    if (setupRequired === null) return;
    if (setupRequired) setUsername((u) => u || 'admin');
    usernameRef.current?.focus();
  }, [setupRequired]);

  const passwordTooShort = isSetup && password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const passwordsMismatch =
    isSetup && confirmPassword.length > 0 && password !== confirmPassword;
  const submitDisabled =
    loading ||
    !username.trim() ||
    !password ||
    (isSetup && (password.length < MIN_PASSWORD_LENGTH || password !== confirmPassword));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitDisabled) return;
    setLoading(true);
    try {
      if (isSetup) {
        await setup(username.trim(), password);
      } else {
        await login(username.trim(), password);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 flex items-center justify-center"
      style={{
        background:
          'radial-gradient(120% 120% at 12% 0%, #141a2e 0%, #0b1020 60%)',
      }}
    >
      {/* Decorative blobs */}
      <div
        className="pointer-events-none absolute left-1/4 top-1/4 h-96 w-96 -translate-x-1/2 -translate-y-1/2 rounded-full opacity-20 blur-3xl"
        style={{ background: 'radial-gradient(circle, #007AFF 0%, transparent 70%)' }}
      />
      <div
        className="pointer-events-none absolute right-1/4 bottom-1/4 h-64 w-64 translate-x-1/2 translate-y-1/2 rounded-full opacity-10 blur-3xl"
        style={{ background: 'radial-gradient(circle, #5856D6 0%, transparent 70%)' }}
      />

      {/* Login card */}
      <div className="relative w-full max-w-sm animate-scale-in px-4">
        <div className="glass-strong overflow-hidden rounded-2xl border border-fg/10 shadow-glass-lg">
          {/* Header stripe */}
          <div className="border-b border-fg/10 px-8 pb-6 pt-8 text-center">
            <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-accent shadow-lg shadow-accent/40">
              <BrandMark className="h-7 w-7 text-accent-fg" />
            </div>
            <h1 className="text-xl font-semibold text-fg">NetGeo</h1>
            <p className="mt-1 text-sm text-fg/50">
              {isSetup ? 'First-run setup — create your admin account' : 'Network Simulation Platform'}
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4 px-8 py-6">
            {/* Username */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium uppercase tracking-wide text-fg/50">
                Username
              </label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg/35" />
                <input
                  ref={usernameRef}
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => { setUsername(e.target.value); clearError(); }}
                  placeholder="admin"
                  className={cn(
                    'w-full rounded-md border bg-recess/25 py-2.5 pl-9 pr-3 text-sm text-fg/90 outline-none transition-colors placeholder:text-fg/25',
                    loginError
                      ? 'border-danger focus:border-danger'
                      : 'border-fg/10 focus:border-accent',
                  )}
                />
              </div>
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium uppercase tracking-wide text-fg/50">
                {isSetup ? 'New password' : 'Password'}
              </label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg/35" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  autoComplete={isSetup ? 'new-password' : 'current-password'}
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); clearError(); }}
                  placeholder="••••••••"
                  className={cn(
                    'w-full rounded-md border bg-recess/25 py-2.5 pl-9 pr-10 text-sm text-fg/90 outline-none transition-colors placeholder:text-fg/25',
                    loginError || passwordTooShort
                      ? 'border-danger focus:border-danger'
                      : 'border-fg/10 focus:border-accent',
                  )}
                />
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-fg/35 hover:text-fg/60"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {passwordTooShort && (
                <p className="text-xs text-danger">
                  At least {MIN_PASSWORD_LENGTH} characters.
                </p>
              )}
            </div>

            {/* Confirm password — setup mode only */}
            {isSetup && (
              <div className="space-y-1.5">
                <label className="block text-xs font-medium uppercase tracking-wide text-fg/50">
                  Confirm password
                </label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg/35" />
                  <input
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(e) => { setConfirmPassword(e.target.value); clearError(); }}
                    placeholder="••••••••"
                    className={cn(
                      'w-full rounded-md border bg-recess/25 py-2.5 pl-9 pr-3 text-sm text-fg/90 outline-none transition-colors placeholder:text-fg/25',
                      passwordsMismatch
                        ? 'border-danger focus:border-danger'
                        : 'border-fg/10 focus:border-accent',
                    )}
                  />
                </div>
                {passwordsMismatch && (
                  <p className="text-xs text-danger">Passwords do not match.</p>
                )}
              </div>
            )}

            {/* Error message */}
            {loginError && (
              <p className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
                {loginError}
              </p>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={submitDisabled}
              className={cn(
                'flex w-full items-center justify-center gap-2 rounded-md py-2.5 text-sm font-semibold text-accent-fg transition-all',
                submitDisabled
                  ? 'cursor-not-allowed bg-accent/50'
                  : 'bg-accent shadow-soft hover:bg-accent-soft active:scale-[0.98]',
              )}
            >
              {loading ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-fg/30 border-t-fg" />
              ) : isSetup ? (
                <ShieldCheck className="h-4 w-4" />
              ) : (
                <LogIn className="h-4 w-4" />
              )}
              {loading
                ? isSetup ? 'Creating account…' : 'Signing in…'
                : isSetup ? 'Create account & sign in' : 'Sign in'}
            </button>

            <p className="text-center text-[11px] text-fg/25">
              {isSetup
                ? 'This one-time setup secures your NetGeo instance'
                : 'Sign in with your NetGeo account'}
            </p>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-fg/20">
          NetGeo v{__APP_VERSION__} &mdash; Network Simulation Platform
        </p>
      </div>
    </div>
  );
}
