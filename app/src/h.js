/* htm bound to React.createElement — JSX-shaped markup with no build step.
   React itself is a vendored UMD global, so every module reads it from here. */
const React = window.React;
export const html = window.htm.bind(React.createElement);
export const {
  useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback,
  useReducer, useContext, createContext, memo, Fragment
} = React;
export default React;
