/**
 * Native-shell detection + window-state hooks shared by App.tsx (rounded-
 * corner frame) and NativeTitleBar.tsx (window chrome). Split out so both
 * agree on the same source of truth instead of drifting.
 */
import { useEffect, useState } from 'react';

/** True only inside the pywebview native window — never in a plain browser
 * tab (distribution variants #4/#5). pywebview injects `window.pywebview`
 * before page scripts run, but a `pywebviewready` listener covers the
 * documented case where it lands slightly later than React's first paint. */
export function useIsNativeShell(): boolean {
  const [isNative, setIsNative] = useState(() => typeof window !== 'undefined' && 'pywebview' in window);
  useEffect(() => {
    if (isNative) return;
    const onReady = () => setIsNative(true);
    window.addEventListener('pywebviewready', onReady);
    return () => window.removeEventListener('pywebviewready', onReady);
  }, [isNative]);
  return isNative;
}

/** Real maximize/restore state, sourced from the window manager via
 * packaging/launcher.py's `window.events.maximized`/`restored` (fired for
 * ANY state change — our own title-bar button, a WM keybinding, or
 * drag-to-edge snap — not just clicks inside NativeTitleBar). Starts
 * false: the launcher never passes `maximized=True` to
 * `webview.create_window()`. */
export function useIsMaximized(): [boolean, React.Dispatch<React.SetStateAction<boolean>>] {
  const [isMaximized, setIsMaximized] = useState(false);
  useEffect(() => {
    const onMax = () => setIsMaximized(true);
    const onRestore = () => setIsMaximized(false);
    window.addEventListener('netgeo:maximize', onMax);
    window.addEventListener('netgeo:restore', onRestore);
    return () => {
      window.removeEventListener('netgeo:maximize', onMax);
      window.removeEventListener('netgeo:restore', onRestore);
    };
  }, []);
  return [isMaximized, setIsMaximized];
}
