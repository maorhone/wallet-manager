// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { DistributionBar } from './DistributionBar';
afterEach(cleanup);
it('shows three disjoint states that sum to the total', () => {
  render(<DistributionBar used={20} assignedUnused={30} unallocated={50} />);
  const parts = screen.getAllByTestId('distribution-segment');
  expect(parts.map((p) => p.getAttribute('data-value'))).toEqual(['20', '30', '50']);
  expect(parts.map((p) => p.style.width)).toEqual(['20%', '30%', '50%']);
  expect(screen.queryByRole('status')).toBeNull();
});
it('marks over-allocation separately', () => {
  render(<DistributionBar used={20} assignedUnused={100} unallocated={0} overAllocated />);
  expect(screen.getByRole('status').textContent).toContain('הקצאת יתר');
  expect(screen.getAllByTestId('distribution-segment')[1].getAttribute('data-value')).toBe('100');
});
it('handles an empty distribution without invalid widths', () => {
  render(<DistributionBar used={0} assignedUnused={0} unallocated={0} />);
  expect(screen.getAllByTestId('distribution-segment').every((p) => p.style.width === '0%')).toBe(
    true,
  );
});
