import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Deployed at https://sarthak-sharma2003.github.io/fpl-ai-scout/ucl/ — a fixed
// subpath, not the domain root, so every built asset must be prefixed with it.
export default defineConfig({
  base: "/fpl-ai-scout/ucl/",
  plugins: [react()],
});
