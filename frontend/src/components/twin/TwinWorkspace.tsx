/**
 * TwinWorkspace — the Digital Twin full-bleed view (NG-TW-01/02).
 *
 * Reuses the live TopologyCanvas as the device canvas (the twin operates on the
 * *current* project's graph), and layers the twin chrome over it: a derived
 * stepper, the Link Inference inspector, a reachability query bar, and a
 * validation list. Import/reachability hit the backend; proposals + validation
 * are derived client-side from the topology store.
 */
import { useMemo, useState } from 'react';
import { Network } from 'lucide-react';
import { TopologyCanvas } from '@/components/canvas/TopologyCanvas';
import { useTopologyStore } from '@/store/topologyStore';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { TwinStepper } from './TwinStepper';
import { LinkInferencePanel } from './LinkInferencePanel';
import { ReachabilityBar } from './ReachabilityBar';
import { ValidationIssues } from './ValidationIssues';
import { ImportConfigModal } from './ImportConfigModal';
import {
  deriveProposals,
  deriveStepIndex,
  deriveValidationIssues,
} from './twinLogic';

export function TwinWorkspace() {
  const nodes = useTopologyStore((s) => s.nodes);
  const links = useTopologyStore((s) => s.links);
  const importOpen = useUiStore((s) => s.activeModal === 'importConfig');
  const openImport = () => useUiStore.getState().openModal('importConfig');
  const closeImport = () => useUiStore.getState().closeModal();
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [resolved, setResolved] = useState(0);

  const nodeList = useMemo(() => Array.from(nodes.values()), [nodes]);
  const linkList = useMemo(() => Array.from(links.values()), [links]);

  const proposals = useMemo(
    () => deriveProposals(nodeList, linkList).filter((p) => !dismissed.has(p.id)),
    [nodeList, linkList, dismissed],
  );
  const issues = useMemo(
    () => deriveValidationIssues(nodeList, linkList),
    [nodeList, linkList],
  );
  const stepIndex = deriveStepIndex(nodeList, linkList, proposals.length, issues.length);

  const reject = (id: string) => {
    setDismissed((prev) => new Set(prev).add(id));
    setResolved((r) => r + 1);
  };

  return (
    <>
      <div className="absolute inset-0">
        <TopologyCanvas topLeftExtra={<TwinStepper stepIndex={stepIndex} onImport={openImport} />} />
      </div>

      {/* Empty-state guidance — shared WorkspaceEmptyState (design 12-UI §2.6). */}
      {nodeList.length === 0 && (
        <WorkspaceEmptyState
          icon={Network}
          title="Import a device config to build your digital twin"
          hint="Links and reachability are inferred from the imported interfaces."
          action={{ label: 'Import Config', onClick: openImport }}
        />
      )}

      <LinkInferencePanel
        proposals={proposals}
        resolved={resolved}
        onReject={reject}
        onResolved={() => setResolved((r) => r + 1)}
      />
      <ReachabilityBar nodes={nodeList} />
      <ValidationIssues issues={issues} />

      {importOpen && <ImportConfigModal onClose={closeImport} />}
    </>
  );
}
