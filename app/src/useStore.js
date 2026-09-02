/* subscribe a component to the store */
import { useState, useEffect } from './h.js';
import { S, subscribe } from './store.js';

export function useStore(){
  const [, bump] = useState(0);
  useEffect(() => subscribe(() => bump(n => n + 1)), []);
  return S;
}
