import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AllocationTable, distributeEvenly, summarize, type AllocRow } from './AllocationTable';

const rows: AllocRow[] = [
  { key: 'dana', label: 'Dana', value: 15000, editable: false, owner: 'נציג מחלקה: Rina' },
  { key: 'dani', label: 'Dani', value: 15000, editable: true },
  { key: 'yael', label: 'Yael', value: 15000, editable: true },
];

function render(values: Record<string, number>) {
  return renderToStaticMarkup(
    createElement(
      MantineProvider,
      null,
      createElement(AllocationTable, {
        rows,
        edits: values,
        total: 250000,
        totalLabel: 'בסיס הקבוצה',
        nameHeader: 'שם',
        valueHeader: 'מכסה',
        onChange: () => {},
        onSave: () => {},
      }),
    ),
  );
}

const SAVE_DISABLED = /<button[^>]*data-disabled="true"[^>]*data-testid="alloc-save"/;

describe('AllocationTable', () => {
  it('footer math: allocated and remaining follow the typed values', () => {
    expect(summarize(rows, { dani: 20000 }, 250000)).toEqual({
      allocated: 50000,
      remaining: 200000,
      negative: false,
    });
    expect(summarize(rows, { dani: 300000 }, 250000)).toMatchObject({ negative: true });
  });

  it('red footer and disabled Save when the remainder is negative', () => {
    const html = render({ dani: 300000 });
    expect(html).toContain('data-negative="true"');
    expect(html).toMatch(SAVE_DISABLED);
    expect(render({ dani: 15001 })).not.toContain('data-negative="true"');
  });

  it('Save is disabled until something changed', () => {
    expect(render({})).toMatch(SAVE_DISABLED);
    expect(render({ dani: 16000 })).not.toMatch(SAVE_DISABLED);
  });

  it('shows the owner of a read-only row', () => {
    expect(render({})).toContain('נציג מחלקה: Rina');
  });

  it('distribute evenly: (base − read-only rows) / editable rows, floored', () => {
    // (100001 − 15000) / 2 = 42500.5 → 42500 each, the remainder stays unallocated
    expect(distributeEvenly(rows, 100001)).toEqual({ dani: 42500, yael: 42500 });
    expect(distributeEvenly(rows, 10)).toEqual({ dani: 0, yael: 0 });
  });
});
