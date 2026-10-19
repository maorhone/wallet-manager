import { pct } from '../format';
import { Num } from './Num';

export function Pct({ value }: { value: number }) {
  return <Num>{pct(value)}</Num>;
}
