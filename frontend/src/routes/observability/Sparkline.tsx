interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  ariaLabel: string;
}

export function Sparkline({ values, width = 120, height = 24, ariaLabel }: SparklineProps) {
  if (values.length === 0) {
    return (
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        className="observability-sparkline empty"
      />
    );
  }
  const max = Math.max(...values, 1);
  const stepX = values.length > 1 ? width / (values.length - 1) : 0;
  const points = values
    .map((v, i) => `${(i * stepX).toFixed(2)},${(height - (v / max) * height).toFixed(2)}`)
    .join(" ");
  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={ariaLabel}
      className="observability-sparkline"
    >
      <polyline fill="none" stroke="currentColor" strokeWidth={1.5} points={points} />
    </svg>
  );
}
