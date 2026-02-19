# Aster DEX Component

Guidance-first component scaffold for Aster DEX integrations.

## Overview

This package is intentionally non-functional and is used as a reference scaffold:

- `src/index.ts`: typed exports and setup guidance constants
- `src/example.tsx`: reference usage component
- `src/aster-service.py`: Python API service template for Aster DEX endpoints

## Installation

```bash
pnpm add @cradle/aster-dex
```

## Quick Start

```tsx
import { ASTER_DEX_GUIDANCE, ASTER_DEX_ENDPOINTS } from '@cradle/aster-dex';

console.log(ASTER_DEX_GUIDANCE.summary);
console.log(ASTER_DEX_ENDPOINTS);
```

## Notes

- Use this package as a baseline for implementation details.
- Validate auth, rate limiting, and safety checks before production usage.

