import { credits } from '../format';
import { Num } from './Num';

export function Credits({ value }: { value: number }) {
  return <Num>{credits(value)}</Num>;
}
