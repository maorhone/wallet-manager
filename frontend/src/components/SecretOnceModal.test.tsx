// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SecretOnceModal } from './SecretOnceModal';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

afterEach(cleanup);

function open(onClose = vi.fn()) {
  render(
    createElement(
      MantineProvider,
      null,
      createElement(SecretOnceModal, { secret: 'sk-once-abc123', onClose }),
    ),
  );
  return onClose;
}

describe('SecretOnceModal', () => {
  it('copy button copies the secret', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    open();
    expect((screen.getByTestId('secret-value') as HTMLInputElement).value).toBe('sk-once-abc123');
    fireEvent.click(screen.getByTestId('secret-copy'));
    expect(writeText).toHaveBeenCalledWith('sk-once-abc123');
  });

  it('secret cleared after close', () => {
    const onClose = open();
    fireEvent.click(screen.getByTestId('secret-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
