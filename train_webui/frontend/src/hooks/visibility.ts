// ブラウザのタブが裏にある（見ていない）間は自動更新を止めるためのヘルパー。
// 各ポーリングは取りに行く前にこれを await する。見えている時はすぐ進み、裏にある時は表に戻るまで待つ。
export function waitUntilVisible(): Promise<void> {
  if (typeof document === 'undefined' || document.visibilityState !== 'hidden') return Promise.resolve();
  return new Promise((resolve) => {
    const onChange = () => {
      if (document.visibilityState !== 'hidden') {
        document.removeEventListener('visibilitychange', onChange);
        resolve();
      }
    };
    document.addEventListener('visibilitychange', onChange);
  });
}
