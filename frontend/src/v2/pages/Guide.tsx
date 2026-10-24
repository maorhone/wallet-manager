import { useState } from 'react';
import { Link } from 'react-router-dom';
import { he } from '../../i18n/he';

const g = he.v2.guidePage;

export default function Guide() {
  const [tab, setTab] = useState('employee');
  return (
    <>
      <p className="v2-card">{g.flow}</p>
      <p className="v2-card">{g.kinds}</p>
      <p className="v2-card">{g.creditsVsRate}</p>
      <div role="tablist">
        {Object.entries(g.tabs).map(([k, label]) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            onClick={() => setTab(k)}
          >
            {label}
          </button>
        ))}
      </div>
      <p role="tabpanel" className="v2-card">
        {g.body[tab]}
      </p>
      <h2>{g.faq}</h2>
      {g.faqItems.map(([q, a]) => (
        <details key={q}>
          <summary>{q}</summary>
          <p>{a}</p>
        </details>
      ))}
      <Link to="/v2/requests">{g.cta}</Link>
      <p className="v2-muted">{g.note}</p>
    </>
  );
}
