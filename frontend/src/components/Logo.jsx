export default function Logo({ compact = false }) {
  return (
    <div className="flex items-center gap-3 select-none">
      <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
        <rect x="0.75" y="0.75" width="30.5" height="30.5" rx="9" fill="#121620" stroke="#1E2433" strokeWidth="1.5" />
        {/* N geométrica: dos verticales y una diagonal, como un trazo de radar */}
        <path d="M10 22V10l12 12V10" fill="none" stroke="#E7EAF1" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="22" cy="10" r="2.4" fill="#10B981" />
        <circle cx="22" cy="10" r="5" fill="none" stroke="#10B981" strokeOpacity="0.25" strokeWidth="1" />
      </svg>
      {!compact && (
        <div className="leading-none">
          <div className="text-[15px] font-semibold tracking-[0.22em] text-fg">NURI</div>
          <div className="mt-1 text-[10px] tracking-[0.14em] text-fg-mute">누리 · LIVE QUANT RADAR</div>
        </div>
      )}
    </div>
  )
}
