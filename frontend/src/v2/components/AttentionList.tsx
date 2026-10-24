import { Link } from 'react-router-dom';
import { he } from '../../i18n/he';
export interface AttentionItem {
  id: string;
  title: string;
  description?: string;
  href?: string;
}
/** Callers supply only verified, authorized items. No synthetic forecasts. */
export function AttentionList({ items }: { items: AttentionItem[] }) {
  return (
    <section className="v2-card">
      <h2>{he.v2.attention}</h2>
      {items.length ? (
        <ul className="v2-attention">
          {items.map((item) => (
            <li key={item.id}>
              {item.href ? <Link to={item.href}>{item.title}</Link> : <strong>{item.title}</strong>}
              {item.description && <p>{item.description}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="v2-muted">{he.v2.noAttention}</p>
      )}
    </section>
  );
}
