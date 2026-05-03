// frontend/lib/api_prefix.ts

// Relative path — Next.js rewrites in next.config.ts proxy /api/* to the
// FastAPI backend. Same-origin calls mean no CORS in dev or prod, and the
// frontend has no idea where the backend actually lives.
export const API_PREFIX = 'http://127.0.0.1:8000/api';