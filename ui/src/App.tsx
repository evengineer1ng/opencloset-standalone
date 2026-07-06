import { useState } from "react";
import DesktopShell from "./layout/DesktopShell";
import type { CenterView, ShellVariant } from "./layout/DesktopShell";
import "./App.css";

export default function App() {
  const [centerView, setCenterView] = useState<CenterView>("build");
  const queryVariant =
    typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("ui") : null;
  const envVariant = import.meta.env.VITE_OPENCLOSET_UI_VARIANT as ShellVariant | undefined;
  const variant: ShellVariant = queryVariant === "chat-native" || envVariant === "chat-native" ? "chat-native" : "legacy";

  return <DesktopShell centerView={centerView} onCenterViewChange={setCenterView} variant={variant} />;
}
