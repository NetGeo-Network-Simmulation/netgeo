/**
 * FirstRunMapSetup — one-time "choose a map source" screen shown right after
 * first-run admin setup (see LoginPage's isSetup flow + App.tsx).
 *
 * Why this exists instead of an installer dialog: the map-source picker
 * already exists in the Windows installer (packaging/windows/netgeo.iss) and
 * the Linux CLI installer (packaging/linux/install.sh --offline-map[-url]),
 * but a .rpm/.deb package install has no interactive step at all — dnf/apt
 * just unpack files, they never run a wizard. So the choice has to live here,
 * inside the app, reachable no matter how NetGeo was installed.
 *
 * Three honest options, matching what backend/app/services/offline_maps.py
 * can actually do — no "download a region pack" promise, since we host no
 * region catalog (see the same reasoning in netgeo.iss):
 *   - Online (default) — today's behavior, unchanged.
 *   - A local .mbtiles file the user already has.
 *   - A URL to a .mbtiles file, downloaded server-side and installed.
 *
 * Can be revisited later from Settings → General → Offline Map (same
 * mapsApi calls), so skipping here is never a one-way door.
 */
import { useRef, useState } from 'react';
import { Check, Globe, HardDrive, Link2, Loader2 } from 'lucide-react';
import { mapsApi, type ApiError } from '@/api/client';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

type Source = 'online' | 'file' | 'url';

interface FirstRunMapSetupProps {
  onDone: () => void;
}

export function FirstRunMapSetup({ onDone }: FirstRunMapSetupProps) {
  const [source, setSource] = useState<Source>('online');
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const canSubmit =
    !busy && (source === 'online' || (source === 'file' && !!file) || (source === 'url' && !!url.trim()));

  const handleSubmit = async () => {
    if (source === 'online') {
      onDone();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (source === 'file' && file) {
        await mapsApi.uploadOfflineMap(file);
      } else if (source === 'url') {
        await mapsApi.installOfflineMapFromUrl(url.trim());
      }
      onDone();
    } catch (err) {
      const e = err as ApiError;
      setError(e.message || 'Could not install the map file. You can try again from Settings later.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={cn(
        'fixed inset-0 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fade-in',
        zc.modal,
      )}
    >
      <div className="glass-strong relative w-full max-w-md overflow-hidden rounded-2xl border border-fg/15 shadow-glass-lg animate-scale-in">
        <div className="px-8 pb-4 pt-8 text-center">
          <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-accent/15 text-accent shadow-[0_8px_24px_rgb(var(--ng-accent-rgb)_/_0.25)]">
            <Globe className="h-7 w-7" />
          </div>
          <h2 className="text-lg font-semibold text-fg">Map source</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-fg/70">
            NetGeo can show the map online, or from a region file you already have — useful for
            sites with no reliable internet access.
          </p>
        </div>

        <div className="space-y-2 px-8 pb-2">
          <SourceOption
            active={source === 'online'}
            icon={Globe}
            title="Online (default)"
            description="Use satellite/street tiles from the internet, same as today."
            onClick={() => setSource('online')}
          />
          <SourceOption
            active={source === 'file'}
            icon={HardDrive}
            title="Local .mbtiles file"
            description="Point to a region file you already have on this computer."
            onClick={() => setSource('file')}
          >
            {source === 'file' && (
              <div className="mt-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".mbtiles"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="w-full truncate rounded-md border border-fg/15 bg-fg/5 px-3 py-2 text-left text-xs text-fg/70 hover:border-accent/50"
                >
                  {file ? file.name : 'Choose file…'}
                </button>
              </div>
            )}
          </SourceOption>
          <SourceOption
            active={source === 'url'}
            icon={Link2}
            title="URL to a .mbtiles file"
            description="Downloaded once and stored on this computer."
            onClick={() => setSource('url')}
          >
            {source === 'url' && (
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/region.mbtiles"
                className="mt-2 w-full rounded-md border border-fg/15 bg-fg/5 px-3 py-2 text-xs text-fg outline-none placeholder:text-fg/30 focus:border-accent/50"
              />
            )}
          </SourceOption>
        </div>

        {error && (
          <div className="mx-8 mb-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </div>
        )}

        <div className="flex items-center justify-between border-t border-fg/10 px-6 py-4">
          <button
            onClick={onDone}
            disabled={busy}
            className="rounded-md px-3 py-1.5 text-sm text-fg/50 transition-colors hover:bg-fg/10 hover:text-fg/80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Skip for now
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className={cn(
              'flex items-center gap-1.5 rounded-md px-4 py-1.5 text-sm font-medium text-accent-fg transition-colors',
              canSubmit ? 'bg-accent hover:bg-accent-soft' : 'cursor-not-allowed bg-accent/40',
            )}
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {busy
              ? 'Installing…'
              : source === 'online'
                ? 'Continue'
                : source === 'file'
                  ? 'Install & continue'
                  : 'Download & continue'}
          </button>
        </div>
        <p className="border-t border-fg/10 px-6 py-2.5 text-center text-[11px] text-fg/30">
          You can change this later in Settings → General.
        </p>
      </div>
    </div>
  );
}

function SourceOption({
  active,
  icon: Icon,
  title,
  description,
  onClick,
  children,
}: {
  active: boolean;
  icon: typeof Globe;
  title: string;
  description: string;
  onClick: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClick()}
      className={cn(
        'cursor-pointer rounded-lg border p-3 text-left transition-colors',
        active ? 'border-accent bg-accent/10' : 'border-fg/10 bg-fg/5 hover:border-fg/20',
      )}
    >
      <div className="flex items-start gap-2.5">
        <div
          className={cn(
            'mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border',
            active ? 'border-accent bg-accent text-accent-fg' : 'border-fg/25 text-transparent',
          )}
        >
          <Check className="h-3 w-3" />
        </div>
        <div className="min-w-0 flex-1">
          <Icon className={cn('mb-1 h-4 w-4', active ? 'text-accent' : 'text-fg/40')} />
          <p className="text-sm font-medium text-fg/90">{title}</p>
          <p className="text-xs text-fg/50">{description}</p>
          {children}
        </div>
      </div>
    </div>
  );
}
