// OpenCloset Design Tokens
// Serious, tool-like, calm. Information-dense but readable.

export const colors = {
  // Background layers
  bgBase: '#111114',
  bgSurface: '#1a1a1f',
  bgPanel: '#1e1e24',
  bgPanelElevated: '#232329',
  bgHover: '#2a2a32',
  bgActive: '#333340',
  bgDisabled: '#151518',

  // Text
  textPrimary: '#e4e4e8',
  textSecondary: '#9a9aa2',
  textMuted: '#6b6b75',
  textInverse: '#111114',

  // Brand / accent
  accent: '#6e8ffb',
  accentDim: '#4a5fad',
  accentGlow: 'rgba(110, 143, 251, 0.12)',

  // Status
  statusSuccess: '#4ade80',
  statusWarning: '#fbbf24',
  statusError: '#f87171',
  statusIdle: '#6b7280',
  statusPending: '#a78bfa',
  statusRunning: '#6e8ffb',

  // Borders
  borderSubtle: '#2a2a32',
  borderDefault: '#333340',
  borderActive: '#6e8ffb',

  // Special
  runtimeGlow: 'rgba(110, 143, 251, 0.06)',
  cardBg: '#1e1e24',
  cardBorder: '#2a2a32',
  badgeBg: '#2a2a32',
};

export const spacing = {
  xs: '2px',
  sm: '4px',
  md: '8px',
  lg: '12px',
  xl: '16px',
  '2xl': '20px',
  '3xl': '24px',
  '4xl': '32px',
  '5xl': '40px',
};

export const typography = {
  fontMono: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
  fontSans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  size: {
    xs: '10px',
    sm: '12px',
    md: '13px',
    base: '14px',
    lg: '16px',
    xl: '18px',
    '2xl': '20px',
  },
  weight: {
    normal: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
  },
  leading: {
    tight: 1.2,
    normal: 1.5,
    loose: 1.7,
  },
};

export const radius = {
  sm: '4px',
  md: '6px',
  lg: '8px',
  full: '9999px',
};

export const navWidth = '220px';
export const rightPanelWidth = '280px';
export const dockHeight = '48px';
export const dockExpandedHeight = '320px';
export const headerHeight = '36px';

export const transitions = {
  fast: '100ms ease',
  normal: '200ms ease',
  slow: '300ms ease',
};
