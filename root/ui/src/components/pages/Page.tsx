import type { ReactNode } from "react";

/** A full page in the main card: an eyebrow, a title, a lede, and a column of content. */
export function Page({ eyebrow, title, lede, actions, children }: { eyebrow: string; title: string; lede: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <main className="card body">
      <div className="transcript scroll">
        <div className="column page">
          <div className="eyebrow">{eyebrow}</div>
          <div className="pageHead">
            <h1 className="pageTitle">{title}</h1>
            {actions}
          </div>
          <p className="help">{lede}</p>
          {children}
        </div>
      </div>
    </main>
  );
}
