import { Button, Modal, NumberInput, Stack } from '@mantine/core';
import { useState } from 'react';
import { useGroupWrite } from '../api/queries';
import { he } from '../i18n/he';
import { ChangeSummaryDialog } from './ChangeSummaryDialog';
import { Credits } from './Credits';

interface Props {
  opened: boolean;
  groupId: string;
  period: string;
  currentCap: number;
  onClose: () => void;
}

/** Defaults for new members (FR-G2): the base cap; goes through the change summary. */
export function MemberDefaultsDialog({ opened, groupId, period, currentCap, onClose }: Props) {
  const [cap, setCap] = useState<number>(currentCap);
  const [review, setReview] = useState(false);
  const write = useGroupWrite(groupId, period);
  return (
    <Modal opened={opened} onClose={onClose} title={he.groupPage.defaults}>
      <Stack>
        <NumberInput
          label={he.groupPage.defaultCap}
          data-testid="default-cap"
          hideControls
          min={0}
          value={cap}
          onChange={(v) => setCap(Number(v) || 0)}
        />
        <Button disabled={cap === currentCap} onClick={() => setReview(true)}>
          {he.groupPage.review}
        </Button>
      </Stack>
      <ChangeSummaryDialog
        opened={review}
        rows={[
          {
            key: 'cap',
            label: he.groupPage.defaultCap,
            before: <Credits value={currentCap} />,
            after: <Credits value={cap} />,
          },
        ]}
        onClose={() => setReview(false)}
        onConfirm={async (reason, key) => {
          // The model caps stay empty here; the endpoint replaces them as a whole.
          await write.mutateAsync({
            path: `/groups/${groupId}/member-defaults`,
            body: { cap, model_caps: {} },
            reason,
            key,
          });
          onClose();
        }}
      />
    </Modal>
  );
}
