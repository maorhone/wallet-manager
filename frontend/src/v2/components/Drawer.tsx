import { Drawer as MantineDrawer } from '@mantine/core';
import type { ReactNode } from 'react';
import { he } from '../../i18n/he';
/** Mantine provides focus trapping, Escape dismissal and focus restoration. */
export function Drawer({
  opened,
  onClose,
  title,
  children,
}: {
  opened: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <MantineDrawer
      opened={opened}
      onClose={onClose}
      title={title}
      position="right"
      dir="rtl"
      closeButtonProps={{ 'aria-label': he.v2.close }}
      styles={{ content: { fontFamily: "Arial, 'Noto Sans Hebrew', sans-serif" } }}
    >
      {children}
    </MantineDrawer>
  );
}
