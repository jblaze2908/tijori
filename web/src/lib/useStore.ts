import { useSyncExternalStore } from "react";
import { getVersion, onChange } from "./api";

/** Re-renders the caller whenever the request cache changes; components then call read() during render. */
export const useStore = () => useSyncExternalStore(onChange, getVersion);
