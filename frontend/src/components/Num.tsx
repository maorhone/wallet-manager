import type { ReactNode } from 'react';

/** Numbers, model names and ids render left-to-right inside RTL text (§12.1, U8). */
export function Num({ children }: { children: ReactNode }) {
  return <bdi dir="ltr">{children}</bdi>;
}
