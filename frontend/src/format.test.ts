import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Credits } from './components/Credits';
import { Num } from './components/Num';
import { credits, date, freshness, pct } from './format';

describe('format', () => {
  it('credits use thousands separators and no decimals', () => {
    expect(credits(1234567)).toBe('1,234,567');
    expect(credits(1234.6)).toBe('1,235');
    expect(credits(0)).toBe('0');
  });

  it('pct has one decimal', () => {
    expect(pct(12.345)).toBe('12.3%');
    expect(pct(100)).toBe('100.0%');
  });

  it('date is dd/mm/yyyy in the given timezone', () => {
    expect(date('2026-03-05T12:00:00Z', 'Asia/Jerusalem')).toBe('05/03/2026');
    expect(date('2026-03-05T23:30:00Z', 'Asia/Jerusalem')).toBe('06/03/2026');
  });

  it('freshness says minutes ago', () => {
    const now = new Date('2026-03-05T12:10:00Z');
    expect(freshness('2026-03-05T12:07:00Z', now)).toBe('עודכן לפני 3 דקות');
    expect(freshness('2026-03-05T12:09:50Z', now)).toBe('עודכן לפני 0 דקות');
  });

  it('numbers and ids are wrapped in <bdi dir="ltr">', () => {
    expect(renderToStaticMarkup(createElement(Num, null, 'gpt-4o'))).toBe(
      '<bdi dir="ltr">gpt-4o</bdi>',
    );
    const html = renderToStaticMarkup(createElement(Credits, { value: 1500 }));
    expect(html).toContain('<bdi dir="ltr">1,500</bdi>');
  });
});
