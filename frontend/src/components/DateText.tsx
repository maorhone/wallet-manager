import { date } from '../format';
import { Num } from './Num';
import { useMe } from '../api/queries';

export function DateText({ value }: { value: string | Date }) {
  const { data: me } = useMe();
  return <Num>{date(value, me?.timezone ?? 'Asia/Jerusalem')}</Num>;
}
