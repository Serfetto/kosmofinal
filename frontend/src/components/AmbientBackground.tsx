import { useEffect, type CSSProperties } from 'react';

type ParticleStyle = CSSProperties & {
  '--x': string;
  '--y': string;
  '--size': string;
  '--delay': string;
  '--duration': string;
};

const particles: ParticleStyle[] = Array.from({ length: 20 }, (_, index) => ({
  '--x': `${(index * 37 + 11) % 100}%`,
  '--y': `${(index * 61 + 17) % 100}%`,
  '--size': `${1 + (index % 3)}px`,
  '--delay': `${-(index % 8) * 1.4}s`,
  '--duration': `${12 + (index % 6) * 2}s`,
}));

export function AmbientBackground() {
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const shell = document.getElementById('app-shell');
    let frame = 0;
    const onPointerMove = (event: PointerEvent) => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        const x = event.clientX;
        const y = event.clientY;
        shell?.style.setProperty('--pointer-x', `${x}px`);
        shell?.style.setProperty('--pointer-y', `${y}px`);
        shell?.style.setProperty('--parallax-x', `${(x / window.innerWidth - 0.5) * 16}px`);
        shell?.style.setProperty('--parallax-y', `${(y / window.innerHeight - 0.5) * 12}px`);
        frame = 0;
      });
    };

    window.addEventListener('pointermove', onPointerMove, { passive: true });
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div className="ambient" aria-hidden="true">
      <div className="aurora aurora-a" />
      <div className="aurora aurora-b" />
      <div className="aurora aurora-c" />
      <div className="plankton">
        {particles.map((style, index) => <i key={index} style={style} />)}
      </div>
      <div className="noise" />
      <div className="cursor-aura" />
    </div>
  );
}
