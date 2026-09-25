export function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 42 42" fill="none">
        <defs>
          <linearGradient id="aquaflow-mark" x1="7" y1="7" x2="35" y2="35" gradientUnits="userSpaceOnUse">
            <stop stopColor="#f4ffd0" />
            <stop offset=".52" stopColor="#d8ff45" />
            <stop offset="1" stopColor="#26c7b8" />
          </linearGradient>
        </defs>
        <path d="M8 17.5c4-7.3 11.5-10.2 18.2-7.1 3.4 1.5 5.9 4.4 7.3 8" stroke="url(#aquaflow-mark)" />
        <path d="M7.5 25c4.7-4.2 9.3-4.2 14 0s9.3 4.2 14 0" stroke="url(#aquaflow-mark)" />
        <path d="M10.5 31c3.7-2.8 7.4-2.8 11.1 0s7.4 2.8 11.1 0" opacity=".4" stroke="url(#aquaflow-mark)" />
        <circle cx="31" cy="13" r="2.4" fill="#ff7657" stroke="none" />
      </svg>
    </div>
  );
}
