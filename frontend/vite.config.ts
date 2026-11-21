import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const apiUrl = JSON.stringify(env.VITE_API_URL || "http://127.0.0.1:8000");

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
    },
    preview: {
      host: "127.0.0.1",
      port: 5173,
    },
    define: {
      "import.meta.env.VITE_API_URL": apiUrl,
    },
  };
});
