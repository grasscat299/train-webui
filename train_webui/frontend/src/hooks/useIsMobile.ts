import { useEffect, useState } from 'react';

const QUERY = '(max-width: 768px)';

// 画面が狭い（スマホ）かどうか。styles.css の @media (max-width: 768px) と同じ幅で切り替える
export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia(QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(QUERY);
    const on = () => setMobile(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return mobile;
}
