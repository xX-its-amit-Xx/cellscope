// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Tailwind CSS configuration for the CellScope frontend.
 *
 * Defines a compact dark theme used across the single-cell browser UI.
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Dark surface palette tuned for an embedding viewport on black.
        cell: {
          bg: '#0b0d10',
          panel: '#15181d',
          border: '#262b33',
          muted: '#8a93a0',
          text: '#e6e9ee',
          accent: '#4f9cf9',
          accentHover: '#6aaefb',
          danger: '#f97066',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
