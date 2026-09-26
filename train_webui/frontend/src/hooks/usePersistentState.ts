import { useEffect, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';

// useState と同じ使い方で、値を localStorage に保存し、再読み込み後もその値から始める。
// 閲覧者ごとの画面の状態（選んでいた run・Result の表示設定など）用。保存できない環境では普通の useState と同じ。
export function usePersistentState<T>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const storageKey = `train_webui.${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      // 保存できない環境（プライベートモード等）では保存しないだけ
    }
  }, [storageKey, value]);
  return [value, setValue];
}
