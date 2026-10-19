import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ApiState } from '../api/types';
import { WalletCard } from './WalletCard';

const base = {
  title: 'הארנק שלי',
  total: 18000,
  used: 12400,
  remaining: 5600,
  percent: 0.689,
  resetDate: '2026-11-01',
  takenAt: null,
};

function render(props: Partial<Parameters<typeof WalletCard>[0]>) {
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(MantineProvider, null, createElement(WalletCard, { ...base, ...props })),
    ),
  );
}

describe('WalletCard', () => {
  const texts: [ApiState, string][] = [
    ['healthy', 'תקין'],
    ['near_limit', 'קרוב למכסה'],
    ['blocked_own', 'חסום: המכסה האישית נוצלה'],
    ['blocked_group', 'חסום: תקציב הקבוצה נוצל'],
    ['blocked_department', 'חסום: תקציב המחלקה נוצל'],
    ['over_allocated', 'הוקצה מעבר לתקציב'],
  ];
  it.each(texts)('state %s is shown as text (not color only)', (state, text) => {
    const html = render({ state });
    expect(html).toContain(text);
    expect(html).toContain('aria-hidden="true"'); // icon next to the text
  });

  it('shows numbers, the 80%/100% markers and the binding line', () => {
    const html = render({ binding: 'מוגבל על ידי: המכסה האישית שלך' });
    expect(html).toContain('18,000');
    expect(html).toContain('12,400');
    expect(html).toContain('68.9%');
    expect(html).toContain('01/11/2026');
    expect(html).toContain('data-marker="80"');
    expect(html).toContain('data-marker="100"');
    expect(html).toContain('מוגבל על ידי: המכסה האישית שלך');
  });

  it('warns when allocated exceeds the total', () => {
    const html = render({ allocated: 20000, unallocated: -2000 });
    expect(html).toContain('ההקצאה גבוהה מהתקציב');
    expect(render({ allocated: 100, unallocated: 17900 })).not.toContain('ההקצאה גבוהה');
  });
});
