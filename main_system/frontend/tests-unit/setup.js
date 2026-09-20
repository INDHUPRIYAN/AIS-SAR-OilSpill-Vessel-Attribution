import "@testing-library/jest-dom/vitest";

// maplibre-gl builds its worker from a blob URL at import time; jsdom has no
// createObjectURL. Nothing in the unit suite renders a map -- this only lets
// modules that import one load.
if (typeof window !== "undefined" && !window.URL.createObjectURL) {
  window.URL.createObjectURL = () => "blob:unit-test";
  window.URL.revokeObjectURL = () => {};
}
