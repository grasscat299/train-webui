export type AxisScale = 'linear' | 'log';

// 軸の linear / log を画面上で切り替えるボタン（表示だけの切り替えで、送られた定義は変えない）。
export function ScaleToggle({ axis, value, onChange }: { axis: 'X' | 'Y'; value: AxisScale; onChange: (v: AxisScale) => void }) {
  return (
    <button
      className="scale-toggle"
      title={`${axis}軸を${value === 'log' ? 'linear' : 'log'}にする`}
      onClick={() => onChange(value === 'log' ? 'linear' : 'log')}
    >
      {axis}: {value}
    </button>
  );
}
