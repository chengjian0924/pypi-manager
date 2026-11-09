/**
 * 极简发布订阅状态容器。
 * @template T
 * @param {T} initialState
 */
export function createStore(initialState) {
  let state = initialState;
  const subscribers = new Set();

  return {
    getState() {
      return state;
    },
    /** @param {Partial<T>|((state: T) => T)} patch */
    setState(patch) {
      state = typeof patch === "function" ? patch(state) : Object.assign({}, state, patch);
      subscribers.forEach(fn => fn(state));
      return state;
    },
    /** @param {(state: T) => void} fn */
    subscribe(fn) {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    }
  };
}
