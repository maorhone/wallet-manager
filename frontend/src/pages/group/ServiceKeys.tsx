import type { GroupSummary } from '../../api/types';
import { ServiceKeysPanel } from '../../components/ServiceKeysPanel';

/** Tab "מפתחות שירות" (§12.5.2 tab 5). */
export default function ServiceKeys({
  groupId,
  summary,
}: {
  groupId: string;
  summary: GroupSummary;
}) {
  return (
    <ServiceKeysPanel
      ownerKind="group"
      ownerId={Number(groupId)}
      manage={summary.actions.service_keys.manage && !summary.read_only}
      parent={{ allocated: summary.wallet.allocated, base: summary.wallet.base }}
    />
  );
}
