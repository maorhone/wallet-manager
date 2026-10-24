import { he } from '../../i18n/he';
export function FundingTag({ owner }: { owner: string }) {
  return (
    <span className="v2-tag">
      {he.v2.fundingOwner}: {owner}
    </span>
  );
}
