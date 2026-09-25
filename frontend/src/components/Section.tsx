import type { PropsWithChildren } from 'react';

type SectionProps = PropsWithChildren<{
  title: string;
  className?: string;
}>;

export function Section({ title, children, className = '' }: SectionProps) {
  return (
    <section className={`sidebar-section animate-soft-in ${className}`}>
      <h2 className="section-title">{title}</h2>
      {children}
    </section>
  );
}
