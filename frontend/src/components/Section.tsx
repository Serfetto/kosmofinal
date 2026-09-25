import type { PropsWithChildren } from 'react';

type SectionProps = PropsWithChildren<{
  index: string;
  title: string;
  className?: string;
}>;

export function Section({ index, title, children, className = '' }: SectionProps) {
  return (
    <section className={`sidebar-section animate-soft-in ${className}`}>
      <div className="section-kicker"><span>{index}</span>{title}</div>
      {children}
    </section>
  );
}
