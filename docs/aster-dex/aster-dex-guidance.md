# Aster DEX Guidance

This node adds a guidance-first Aster DEX integration scaffold to your generated app.

## What gets generated

- A component package: `packages/aster-dex`
- A Python API reference service: `src/aster-service.py` inside that package
- Documentation and guidance for adapting the service to your own backend

## Notes

- This block does not execute trading actions by itself.
- Treat the service file as a reference implementation and validate it for your stack.
- Add authentication, rate limiting, and production safeguards before going live.