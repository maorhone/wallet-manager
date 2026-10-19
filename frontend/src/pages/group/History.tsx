import { Loader } from '@mantine/core';
import { useGroupAudit } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { HistoryTimeline } from '../../components/HistoryTimeline';
import { he } from '../../i18n/he';

export default function History({ groupId }: { groupId: string }) {
  const audit = useGroupAudit(groupId);
  if (audit.isPending) return <Loader aria-label={he.loading} />;
  if (audit.error || !audit.data) return <ErrorState error={audit.error} onRetry={audit.refetch} />;
  return <HistoryTimeline entries={audit.data} />;
}
