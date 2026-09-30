export function ProgressBar({ value, label = 'Progress' }: { value: number; label?: string }) {
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200"
    >
      <div
        className={`h-full rounded-full ${value >= 100 ? 'bg-emerald-500' : 'bg-blue-500'}`}
        style={{ width: `${value}%` }}
      />
    </div>
  );
}
