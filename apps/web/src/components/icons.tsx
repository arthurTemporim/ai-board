import type { SVGProps } from 'react';

const base = (props: SVGProps<SVGSVGElement>) => ({
  width: 14,
  height: 14,
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
  focusable: false,
  ...props,
});

export const ThumbIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M6 9v8H3V9h3Zm0 0 3-6c1.5 0 2 1 2 2v3h5a1.5 1.5 0 0 1 1.5 1.8l-1 5A1.5 1.5 0 0 1 14.100 16H6" />
  </svg>
);
export const CommentIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M3 4.500A1.500 1.500 0 0 1 4.500 3h11A1.500 1.500 0 0 1 17 4.500v7a1.500 1.500 0 0 1-1.500 1.500H9l-4 3.500V13h-.5A1.500 1.500 0 0 1 3 11.500v-7Z" />
  </svg>
);
export const CopyIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <rect x="7" y="7" width="10" height="10" rx="1.500" />
    <path d="M13 7V4.500A1.500 1.500 0 0 0 11.500 3h-7A1.500 1.500 0 0 0 3 4.500v7A1.500 1.500 0 0 0 4.500 13H7" />
  </svg>
);
export const LinkIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M8.500 11.500a3 3 0 0 0 4.200 0l2.800-2.800a3 3 0 0 0-4.200-4.200l-1 1M11.500 8.500a3 3 0 0 0-4.200 0L4.500 11.300a3 3 0 0 0 4.200 4.200l1-1" />
  </svg>
);
export const SparkleIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M10 2.500 11.800 8 17.500 10l-5.700 2L10 17.500 8.200 12 2.500 10 8.200 8 10 2.500Z" />
  </svg>
);
export const GripIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)} fill="currentColor" stroke="none">
    <circle cx="7" cy="5" r="1.300" />
    <circle cx="13" cy="5" r="1.300" />
    <circle cx="7" cy="10" r="1.300" />
    <circle cx="13" cy="10" r="1.300" />
    <circle cx="7" cy="15" r="1.300" />
    <circle cx="13" cy="15" r="1.300" />
  </svg>
);
