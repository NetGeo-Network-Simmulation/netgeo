/**
 * ModalLayer — the app-wide, cross-mode modal surfaces (design 12-UI §2.3):
 * Settings, Scenarios, and the first-run Onboarding wizard. All read the single
 * uiStore.activeModal slot, so at most one is ever mounted. Workspace-scoped
 * modals (device picker, import config, device library, map onboarding, fiber
 * detail) live inside their own workspace but share the same slot.
 *
 * Mounted once by AppShell. Owns the first-run onboarding trigger so onboarding
 * participates in the exclusive-modal contract instead of stacking over others.
 */
import { useEffect } from 'react';
import { useUiStore } from '@/store/uiStore';
import { useAuthStore } from '@/store/authStore';
import { ModalScrim } from './ModalScrim';
import { SettingsPanel } from '@/components/SettingsPanel';
import { ScenariosPanel } from '@/components/ScenariosPanel';
import { OnboardingModal, ONBOARDING_KEY } from '@/components/OnboardingModal';
import { FirstRunMapSetup } from '@/components/FirstRunMapSetup';
import { AddressingWizard } from '@/components/lab/AddressingWizard';
import { IconLibraryModal } from '@/components/icons/IconLibraryModal';

export function ModalLayer() {
  const activeModal = useUiStore((s) => s.activeModal);
  const openModal = useUiStore((s) => s.openModal);
  const closeModal = useUiStore((s) => s.closeModal);

  // First-run map source: claim the slot before the workflow tour below, if
  // this session just created the admin account (QA 2026-09-14). Runs first
  // in declaration order so it wins the single-slot race on the same mount.
  useEffect(() => {
    if (useAuthStore.getState().justCompletedSetup && useUiStore.getState().activeModal === null) {
      openModal('mapSourceSetup');
    }
  }, [openModal]);

  // First-run onboarding: claim the modal slot once, if nothing else holds it.
  useEffect(() => {
    if (localStorage.getItem(ONBOARDING_KEY) === 'true') return;
    if (useUiStore.getState().activeModal === null) openModal('onboarding');
  }, [openModal]);

  const dismissMapSourceSetup = () => {
    useAuthStore.getState().clearJustCompletedSetup();
    // Chain into the workflow tour exactly as if nothing had intervened.
    if (localStorage.getItem(ONBOARDING_KEY) !== 'true') openModal('onboarding');
    else closeModal();
  };

  const dismissOnboarding = () => {
    localStorage.setItem(ONBOARDING_KEY, 'true');
    closeModal();
  };

  if (activeModal === 'mapSourceSetup') return <FirstRunMapSetup onDone={dismissMapSourceSetup} />;

  if (activeModal === 'onboarding') return <OnboardingModal onClose={dismissOnboarding} />;

  if (activeModal === 'addressingWizard') return <AddressingWizard />;

  if (activeModal === 'iconLibrary') return <IconLibraryModal />;

  if (activeModal === 'settings')
    return (
      <ModalScrim label="Settings" onClose={closeModal} className="max-w-3xl">
        <div className="h-[70vh]">
          <SettingsPanel />
        </div>
      </ModalScrim>
    );

  if (activeModal === 'scenarios')
    return (
      <ModalScrim label="Scenarios" onClose={closeModal} className="max-w-xl">
        <div className="flex items-center gap-2 border-b border-fg/10 px-4 py-3">
          <h2 className="text-sm font-semibold text-fg/85">Scenarios</h2>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <ScenariosPanel />
        </div>
      </ModalScrim>
    );

  return null;
}
